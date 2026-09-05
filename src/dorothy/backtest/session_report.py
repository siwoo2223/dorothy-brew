"""시간대별 성과 분해 — 그리고 다중검정 함정 처리.

**이 모듈이 다루는 진짜 문제는 통계다.**

24개 시간대를 재보면 그중 최고는 반드시 좋아 보인다. 성과가 완전히 무작위여도
가장 좋은 시간대는 존재한다. "화요일 14시에 진입하면 승률 70%"는
24×7 = 168번 뽑기에서 최고를 고른 결과일 뿐인 경우가 대부분이다.

그래서 각 분해마다 **최고 구간이 우연히 나올 확률**을 순열검정으로 계산한다.
라벨을 무작위로 섞어 같은 통계량을 수천 번 다시 재고,
실제만큼 좋은 결과가 얼마나 자주 나오는지 센다.

이건 개별 구간에 대한 검정이 아니라 **'최댓값'에 대한 검정**이라
뽑기 횟수가 자동으로 반영된다. 그게 다중검정 보정이다.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field

from ..analysis.sessions import (
    killzone_of,
    session_of,
    utc_hour,
    weekday,
)
from ..config import Config
from ..models import Candle, Trade
from ..strategy.base import Strategy
from . import engine as backtest_engine


@dataclass
class Bucket:
    label: str
    trades: list[Trade] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def total_pnl(self) -> float:
        return sum(t.net_pnl for t in self.trades)

    @property
    def win_rate(self) -> float:
        return (
            sum(1 for t in self.trades if t.net_pnl > 0) / self.n * 100 if self.n else 0.0
        )

    @property
    def expectancy(self) -> float:
        return self.total_pnl / self.n if self.n else 0.0


def max_bucket_p_value(
    labels: list[str], values: list[float], *, min_n: int = 5, runs: int = 4000, seed: int = 11
) -> float:
    """'가장 좋은 구간'이 우연히 나올 확률.

    개별 구간이 아니라 최댓값을 검정하므로 뽑기 횟수가 자동으로 반영된다.
    구간이 많을수록 우연히 좋은 구간이 나오기 쉽고, p값도 그만큼 커진다.
    """
    if len(labels) != len(values) or len(values) < min_n * 2:
        return 1.0

    def best_mean(assigned: list[str]) -> float:
        groups: dict[str, list[float]] = defaultdict(list)
        for label, value in zip(assigned, values):
            groups[label].append(value)
        means = [sum(v) / len(v) for v in groups.values() if len(v) >= min_n]
        return max(means) if means else float("-inf")

    observed = best_mean(labels)
    if observed == float("-inf"):
        return 1.0

    shuffled = list(labels)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(runs):
        rng.shuffle(shuffled)
        if best_mean(shuffled) >= observed:
            extreme += 1
    return extreme / runs


@dataclass
class Breakdown:
    title: str
    buckets: dict[str, Bucket]
    p_value: float
    min_n: int = 5

    def lines(self) -> list[str]:
        out = [
            f"  {self.title}",
            "  " + "─" * 64,
            f"  {'구간':<18}{'거래':>6}{'총손익':>11}{'승률':>9}{'1회기대':>10}",
        ]
        for bucket in sorted(self.buckets.values(), key=lambda b: b.total_pnl, reverse=True):
            flag = "" if bucket.n >= self.min_n else "  ⚠"
            out.append(
                f"  {bucket.label:<18}{bucket.n:>6}{bucket.total_pnl:>11,.2f}"
                f"{bucket.win_rate:>8.1f}%{bucket.expectancy:>10,.2f}{flag}"
            )
        verdict = (
            "유의미함" if self.p_value < 0.05
            else "판단 보류" if self.p_value < 0.2
            else "우연으로도 흔함"
        )
        out += [
            f"  최고 구간이 우연일 확률  p={self.p_value:.3f}  → {verdict}",
            "",
        ]
        return out


@dataclass
class SessionReport:
    breakdowns: list[Breakdown]
    total_trades: int

    def report(self) -> str:
        lines = [
            "═" * 70,
            "  시간대별 성과 분해 (UTC 기준)",
            "═" * 70,
            f"  전체 {self.total_trades}건",
            "─" * 70,
        ]
        for breakdown in self.breakdowns:
            lines += breakdown.lines()

        lines += [
            "═" * 70,
            "  ※ p값은 '가장 좋은 구간'에 대한 검정입니다.",
            "     구간을 24개 재보면 그중 최고는 성과가 무작위여도 좋아 보입니다.",
            "     개별 구간이 아니라 최댓값을 검정해야 그 뽑기 횟수가 반영됩니다.",
        ]

        significant = [b for b in self.breakdowns if b.p_value < 0.05]
        if significant:
            names = ", ".join(b.title for b in significant)
            lines.append(f"  ✓ 우연으로 보기 어려운 분해: {names}")
            lines.append("     그래도 워크포워드로 다른 구간에서 재현되는지 확인하세요.")
        else:
            lines.append("  ⚠ 어떤 분해도 통계적으로 유의하지 않습니다.")
            lines.append("     지금 데이터로는 시간대 편향을 주장할 수 없습니다.")

        if self.total_trades < 100:
            lines.append(
                f"  ⚠ 매매가 {self.total_trades}건뿐입니다. 시간대로 나누면 구간당 표본이 더 작아집니다."
            )
        return "\n".join(lines)


def _bucketize(trades: list[Trade], key) -> dict[str, Bucket]:
    buckets: dict[str, Bucket] = {}
    for trade in trades:
        label = key(trade.opened_at)
        buckets.setdefault(label, Bucket(label)).trades.append(trade)
    return buckets


def _breakdown(trades: list[Trade], title: str, key, *, min_n: int = 5) -> Breakdown:
    buckets = _bucketize(trades, key)
    labels = [key(t.opened_at) for t in trades]
    values = [t.net_pnl for t in trades]
    return Breakdown(title, buckets, max_bucket_p_value(labels, values, min_n=min_n), min_n)


def analyse(candles: list[Candle], strategy: Strategy, config: Config) -> SessionReport:
    trades = _collect_trades(candles, strategy, config)
    breakdowns = [
        _breakdown(trades, "세션별", lambda ts: session_of(ts).korean),
        _breakdown(trades, "ICT 킬존별", lambda ts: killzone_of(ts).korean),
        _breakdown(trades, "요일별", weekday),
        _breakdown(trades, "시간대별 (UTC)", lambda ts: f"{utc_hour(ts):02d}시", min_n=8),
    ]
    return SessionReport(breakdowns, len(trades))


def _collect_trades(candles: list[Candle], strategy: Strategy, config: Config) -> list[Trade]:
    captured: dict = {}
    original = backtest_engine.PaperExchange

    class Capturing(original):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            captured["exchange"] = self

    backtest_engine.PaperExchange = Capturing
    try:
        backtest_engine.run(candles, strategy, config)
    finally:
        backtest_engine.PaperExchange = original
    return captured["exchange"].trades
