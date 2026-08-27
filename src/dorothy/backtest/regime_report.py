"""국면별 성과 분해 — 내 전략은 어떤 시장에서 벌고 어떤 시장에서 잃나.

백테스트 총합은 "이 전략이 통한다/안 통한다"만 말해준다.
그런데 대부분의 전략은 **특정 국면에서만 통한다.** 추세추종은 추세장에서 벌고
횡보장에서 그만큼 토해낸다. 총합이 0이면 "쓸모없는 전략"처럼 보이지만
사실은 "국면 필터가 없는 전략"일 수 있다.

각 매매를 **진입 시점의 국면**으로 분류해 성과를 나눠 본다.
한쪽 국면에서만 벌고 있다면, 답은 전략 교체가 아니라 국면 필터다.

국면은 진입 시점까지의 캔들만으로 판정한다 — 나중에 돌아보면 안 된다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..analysis.regime import Regime, Trendiness, classify
from ..config import Config
from ..models import Candle, Trade
from ..strategy.base import Strategy
from . import engine as backtest_engine


@dataclass
class RegimeBucket:
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
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t.net_pnl > 0) / self.n * 100

    @property
    def expectancy(self) -> float:
        return self.total_pnl / self.n if self.n else 0.0


@dataclass
class RegimeReport:
    by_trendiness: dict[str, RegimeBucket]
    by_regime: dict[str, RegimeBucket]
    total_trades: int

    def report(self) -> str:
        lines = [
            "═" * 70,
            "  국면별 성과 분해 — 어떤 시장에서 벌고 있나",
            "═" * 70,
            f"  {'국면':<16}{'거래':>6}{'총손익':>12}{'승률':>9}{'1회기대':>11}",
            "─" * 70,
        ]
        for bucket in sorted(self.by_trendiness.values(), key=lambda b: b.total_pnl, reverse=True):
            flag = "" if bucket.n >= 10 else "  ⚠표본부족"
            lines.append(
                f"  {bucket.label:<16}{bucket.n:>6}{bucket.total_pnl:>12,.2f}"
                f"{bucket.win_rate:>8.1f}%{bucket.expectancy:>11,.2f}{flag}"
            )

        lines += ["─" * 70, "  변동성까지 나눈 것", "─" * 70]
        for bucket in sorted(self.by_regime.values(), key=lambda b: b.total_pnl, reverse=True):
            flag = "" if bucket.n >= 10 else "  ⚠표본부족"
            lines.append(
                f"  {bucket.label:<16}{bucket.n:>6}{bucket.total_pnl:>12,.2f}"
                f"{bucket.win_rate:>8.1f}%{bucket.expectancy:>11,.2f}{flag}"
            )
        lines.append("═" * 70)

        usable = [b for b in self.by_trendiness.values() if b.n >= 10]
        winners = [b for b in usable if b.total_pnl > 0]
        losers = [b for b in usable if b.total_pnl <= 0]

        if winners and losers:
            w = ", ".join(b.label for b in winners)
            l = ", ".join(b.label for b in losers)
            lines += [
                f"  ✓ 버는 국면: {w}",
                f"  ✗ 잃는 국면: {l}",
                "",
                "  → 전략을 바꾸기 전에 **잃는 국면에서 쉬는 것**을 먼저 시도하세요.",
                "     같은 전략이 국면 필터 하나로 달라질 수 있습니다.",
            ]
        elif winners and not losers:
            lines.append("  ✓ 모든 국면에서 수익입니다. 국면 필터의 이득은 크지 않을 수 있습니다.")
        elif losers and not winners:
            lines.append("  ✗ 어떤 국면에서도 수익이 나지 않습니다. 국면 필터로 해결될 문제가 아닙니다.")
        else:
            lines.append("  ⚠ 표본 10건 이상인 국면이 없습니다. 기간을 늘리세요.")

        if self.total_trades < 30:
            lines.append(f"  ⚠ 전체 매매가 {self.total_trades}건뿐입니다. 분해 결과도 그만큼 불안정합니다.")
        return "\n".join(lines)


def _regime_at(candles: list[Candle], index: int, window: int) -> Regime | None:
    """진입 시점까지의 캔들만으로 국면을 판정한다."""
    if index < 50:
        return None
    return classify(candles[: index + 1], window=window)


def analyse(
    candles: list[Candle], strategy: Strategy, config: Config, *, window: int = 200
) -> RegimeReport:
    trades = _collect_trades(candles, strategy, config)
    ts_to_index = {c.ts: i for i, c in enumerate(candles)}

    by_trend: dict[str, RegimeBucket] = {}
    by_regime: dict[str, RegimeBucket] = {}

    for trade in trades:
        index = ts_to_index.get(trade.opened_at)
        if index is None:
            continue
        regime = _regime_at(candles, index, window)
        if regime is None:
            continue

        trend_label = {"trending": "추세장", "random": "무작위장", "reverting": "회귀장"}[
            regime.trendiness.value
        ]
        by_trend.setdefault(trend_label, RegimeBucket(trend_label)).trades.append(trade)
        by_regime.setdefault(regime.label, RegimeBucket(regime.label)).trades.append(trade)

    return RegimeReport(by_trend, by_regime, len(trades))


def _collect_trades(candles: list[Candle], strategy: Strategy, config: Config) -> list[Trade]:
    """백테스트를 돌려 체결 목록을 가져온다."""
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
