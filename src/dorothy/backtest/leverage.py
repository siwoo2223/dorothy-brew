"""레버리지 트레이드오프 — 배율을 올리면 정말 더 버는가.

"낙폭을 줄였으니 레버리지를 올려 수익을 되찾는다"는 말은 직관적이지만
**BTC에서는 성립하지 않는다.** 이유가 셋이다.

1. **변동성 드래그.** +50%와 −50%를 번갈아 맞으면 원금은 줄어든다.
   배율을 올리면 이 손실이 배율의 제곱으로 커진다.
2. **펀딩비.** 1배를 넘는 부분은 무기한 선물로 얹어야 하고, 8.6년을
   1.5배로 들고 있으면 펀딩만 자본의 46%다.
3. **청산.** 자본은 0 아래로 못 간다. 한 번 청산되면 그 뒤 반등은 못 받는다.

셋 다 배율이 올라갈수록 나빠지는데, 수익은 선형으로만 는다.
그래서 어느 지점부터는 배율을 올릴수록 손해다. 이 보고서가 그 지점을 찾는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Config
from ..models import Candle
from .vol_target import Curve, analyse


@dataclass
class Rung:
    """배율 하나에서의 결과."""

    label: str
    leverage: float
    curve: Curve

    @property
    def ruined(self) -> bool:
        return self.curve.ruined


@dataclass
class LeverageTable:
    rungs: list[Rung] = field(default_factory=list)

    @property
    def best(self) -> Rung | None:
        alive = [r for r in self.rungs if not r.ruined]
        return max(alive, key=lambda r: r.curve.ratio) if alive else None

    def by_label(self, label: str) -> list[Rung]:
        return [r for r in self.rungs if r.label == label]

    def report(self) -> str:
        lines = [
            "═" * 78,
            "  레버리지 트레이드오프 — 배율을 올리면 정말 더 버는가",
            "═" * 78,
            f"  {'전략':<22}{'배율':>8}{'수익률':>12}{'MDD':>9}"
            f"{'수익/MDD':>10}{'펀딩':>9}  청산",
            "─" * 78,
        ]
        previous = None
        for rung in self.rungs:
            if previous is not None and rung.label != previous:
                lines.append("")
            previous = rung.label
            c = rung.curve
            lines.append(
                f"  {rung.label:<22}{rung.leverage:>7.2f}x{c.return_pct:>+11.1f}%"
                f"{c.max_drawdown_pct:>8.1f}%{c.ratio:>10.2f}{c.funding_paid:>8.1f}%"
                f"  {'❌ 청산' if c.ruined else '—'}"
            )
        lines.append("═" * 78)
        return "\n".join(lines + self._verdict())

    def _verdict(self) -> list[str]:
        best = self.best
        if best is None:
            return ["  ✗ 모든 배율에서 청산됐습니다."]

        out = [f"  최선  {best.label} {best.leverage:g}배"
               f"  수익 {best.curve.return_pct:+.1f}%"
               f"  MDD {best.curve.max_drawdown_pct:.1f}%"
               f"  수익/MDD {best.curve.ratio:.2f}"]

        if best.leverage <= 1.0:
            out.append("  → 배율을 올리는 것이 어떤 경우에도 도움이 되지 않았습니다.")

        # 배율을 올릴수록 나빠지기 시작하는 지점을 전략별로 찾는다
        for label in dict.fromkeys(r.label for r in self.rungs):
            rungs = sorted(self.by_label(label), key=lambda r: r.leverage)
            peak = max(rungs, key=lambda r: r.curve.return_pct)
            worse = [r for r in rungs if r.leverage > peak.leverage]
            if not worse:
                continue
            last = worse[-1]
            out.append(
                f"  {label}: 수익이 {peak.leverage:g}배에서 정점"
                f"({peak.curve.return_pct:+.1f}%)이고,"
                f" {last.leverage:g}배에서는 {last.curve.return_pct:+.1f}%입니다."
            )

        out.append("  ※ 변동성 드래그·펀딩비·청산이 모두 배율에 따라 나빠집니다."
                   " 수익은 선형으로만 늘어납니다.")
        return out


def analyse_leverage(
    candles: list[Candle],
    cfg: Config,
    *,
    levels: tuple[float, ...] = (1.0, 1.25, 1.5, 2.0, 3.0),
    target_vol: float = 0.50,
    lookback: int = 120,
    rebalance_band: float = 0.30,
    start_index: int | None = None,
) -> LeverageTable:
    """매수 후 보유와 변동성 타게팅을 여러 배율에서 나란히 잰다.

    타게팅 쪽은 목표 변동성을 배율만큼 키운다 — 그래야 실제로 노출이 커진다.
    상한도 함께 올리지 않으면 목표만 높이고 배율이 막혀 의미가 없다.
    """
    table = LeverageTable()
    if start_index is None:
        start_index = lookback + 1

    for level in levels:
        result = analyse(
            candles, cfg, target_vol=target_vol, lookback=lookback,
            rebalance_band=rebalance_band, max_leverage=1.0,
            hold_leverage=level, venue="spot", start_index=start_index,
        )
        table.rungs.append(Rung("매수 후 보유", level, result.hold))

    for level in levels:
        result = analyse(
            candles, cfg, target_vol=target_vol * level, lookback=lookback,
            rebalance_band=rebalance_band, max_leverage=level,
            venue="spot", start_index=start_index,
        )
        table.rungs.append(Rung("변동성 타게팅", level, result.targeted))

    return table
