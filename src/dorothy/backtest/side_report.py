"""롱과 숏을 나눠서 우위를 잰다.

합쳐서 보면 안 보이는 게 있다. 5년 실제 데이터에서 돈치안(40)은 합쳐서
1회 +0.227%였는데, 나눠보니 롱 +0.402% / 숏 +0.001%였다. 숏이 롱의 이익을
정확히 갉아먹고 있었다. 한쪽을 끄는 것만으로 +2.47% → +23.49%가 됐다.

**다만 "롱이 항상 낫다"가 아니다.** 같은 데이터에서:

    돈치안·ema_cross (추세추종형)   → 롱이 우위
    supertrend·mean_reversion      → 숏이 우위

오르는 자산에서 돌파 롱이 통하고, 급등을 되받는 숏이 통한다. 전략의 성격과
방향이 맞아야 한다는 뜻이지, 방향 하나가 늘 옳다는 뜻이 아니다.
그래서 이 보고서는 어느 쪽을 끄라고 정해주지 않고, 양쪽을 나란히 보여준다.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from ..config import Config
from ..models import Action, Candle, Side
from ..ml.labeling import triple_barrier
from ..strategy.base import Strategy


@dataclass
class SideStats:
    side: Side
    returns: list[float] = field(default_factory=list)
    cost: float = 0.0

    @property
    def count(self) -> int:
        return len(self.returns)

    @property
    def gross(self) -> float:
        return statistics.fmean(self.returns) * 100 if self.returns else 0.0

    @property
    def net(self) -> float:
        return self.gross - self.cost * 100

    @property
    def total(self) -> float:
        return self.net * self.count

    @property
    def t_stat(self) -> float:
        if len(self.returns) < 3:
            return 0.0
        spread = statistics.stdev(self.returns)
        if spread <= 0:
            return 0.0
        return (statistics.fmean(self.returns) - self.cost) / (
            spread / math.sqrt(len(self.returns))
        )


@dataclass
class SideComparison:
    long: SideStats
    short: SideStats
    cost: float

    @property
    def combined(self) -> SideStats:
        return SideStats(Side.LONG, self.long.returns + self.short.returns, self.cost)

    @property
    def better(self) -> SideStats | None:
        """1회 기대수익이 나은 쪽. 양쪽 다 표본이 없으면 None."""
        options = [s for s in (self.long, self.short) if s.count >= 3]
        return max(options, key=lambda s: s.net) if options else None

    @property
    def worse(self) -> SideStats | None:
        options = [s for s in (self.long, self.short) if s.count >= 3]
        return min(options, key=lambda s: s.net) if options else None

    def report(self) -> str:
        lines = [
            "═" * 70,
            "  방향별 분해 — 한쪽이 다른 쪽을 갉아먹고 있는가",
            "═" * 70,
            f"  왕복 비용 {self.cost * 100:.2f}%",
            "─" * 70,
            f"  {'방향':<8}{'건수':>8}{'수수료 전':>12}{'수수료 후':>12}{'t':>8}{'합계':>10}",
            "─" * 70,
        ]
        for stats, label in ((self.long, "롱"), (self.short, "숏")):
            if not stats.count:
                lines.append(f"  {label:<8}{0:>8}   (신호 없음)")
                continue
            mark = "✓" if stats.net > 0 and stats.t_stat >= 2 else (
                "?" if stats.net > 0 else "✗")
            lines.append(
                f"  {label:<8}{stats.count:>8}{stats.gross:>+11.3f}%"
                f"{stats.net:>+11.3f}%{stats.t_stat:>8.2f}{stats.total:>+9.1f}% {mark}"
            )
        combined = self.combined
        lines.append("─" * 70)
        lines.append(
            f"  {'합쳐':<8}{combined.count:>8}{combined.gross:>+11.3f}%"
            f"{combined.net:>+11.3f}%{combined.t_stat:>8.2f}{combined.total:>+9.1f}%"
        )
        lines.append("═" * 70)
        return "\n".join(lines + self._verdict())

    def _verdict(self) -> list[str]:
        better, worse = self.better, self.worse
        if better is None or worse is None or better is worse:
            return ["  한쪽만 신호가 나와 비교할 수 없습니다."]

        names = {Side.LONG: "롱", Side.SHORT: "숏"}
        good, bad = names[better.side], names[worse.side]
        gap = better.net - worse.net
        out = []

        if worse.net < 0 < better.net:
            out.append(f"  ⚠ {bad}이 이익을 갉아먹고 있습니다"
                       f" ({bad} {worse.net:+.3f}%/회, {good} {better.net:+.3f}%/회).")
            out.append(f"  {bad}을 끄면 합계가 {self.combined.total:+.1f}%에서"
                       f" {better.total:+.1f}%로 바뀝니다.")
        elif gap > 0.1:
            out.append(f"  {good}이 {bad}보다 1회당 {gap:.3f}%p 낫습니다"
                       f" (양쪽 다 {'흑자' if worse.net > 0 else '적자'}).")
        else:
            out.append(f"  양쪽 차이가 {gap:.3f}%p로 작습니다. 방향을 끄는 근거가 못 됩니다.")

        if better.net > 0 and better.t_stat < 2:
            out.append(f"  ※ 다만 {good}도 t={better.t_stat:.2f}로 우연과 구별되지 않습니다"
                       f" ({better.count}건).")
        out.append("  ※ 한쪽을 끄는 건 그 방향이 늘 나쁘다는 뜻이 아니라, 이 자산의 이 기간에"
                   " 이 전략과 맞지 않았다는 뜻입니다.")
        return out


def analyse(
    candles: list[Candle],
    strategy: Strategy,
    cfg: Config,
    *,
    max_bars: int = 168,
    step: int = 1,
) -> SideComparison:
    """전략이 낸 신호를 방향별로 갈라 삼중 배리어로 결과를 재고 비교한다."""
    cost = 2 * (cfg.exchange.taker_fee + cfg.exchange.slippage)
    long = SideStats(Side.LONG, cost=cost)
    short = SideStats(Side.SHORT, cost=cost)

    for i in range(strategy.warmup, len(candles) - 1, step):
        signal = strategy.generate(candles[: i + 1], None)
        if signal.action is Action.HOLD or not signal.is_entry:
            continue
        if signal.stop_loss is None or signal.take_profit is None:
            continue

        side = Side.LONG if signal.action is Action.ENTER_LONG else Side.SHORT
        outcome = triple_barrier(
            candles, i, side, signal.stop_loss, signal.take_profit, max_bars=max_bars
        )
        if outcome is None:
            continue

        entry = candles[i].close
        exit_price = candles[outcome.exit_index].close
        change = (exit_price - entry) / entry * side.sign
        (long if side is Side.LONG else short).returns.append(change)

    return SideComparison(long, short, cost)
