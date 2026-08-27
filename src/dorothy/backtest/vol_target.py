"""변동성 타게팅 — 방향을 맞히지 않고 노출만 조절한다.

지금까지 시도한 전략은 전부 **방향 예측**이었고 전부 매수 후 보유에 졌다.
8.6년 매수 후 보유는 +486%인데 최대 낙폭이 81.5%다. 문제는 수익이 아니라 낙폭이다.

변동성 타게팅은 방향을 전혀 맞히지 않는다. 하는 일은 하나다:

    수량 = 목표변동성 / 최근실현변동성

변동성이 커지면 줄이고 잠잠하면 늘린다. 이게 예측이 아닌 이유는,
**수익률은 예측이 거의 안 되지만 변동성은 뭉쳐서 나타나기 때문**이다
(volatility clustering). 어제 크게 움직였으면 오늘도 크게 움직일 확률이 높다 —
어느 **방향**으로 움직일지는 여전히 모른다. 그 비대칭을 쓰는 것이다.

⚠ 이게 수익을 늘려주지는 않는다. 리스크를 **일정하게** 만들 뿐이다.
   낙폭이 줄고, 그만큼 레버리지를 올릴 여지가 생긴다. 공짜는 아니다 —
   변동성이 급변할 때 재조정 비용과 펀딩비를 낸다. 그것까지 계산에 넣는다.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from ..config import Config
from ..models import Candle


@dataclass
class Curve:
    """자본 곡선 하나와 그 요약."""

    label: str
    equity: list[float] = field(default_factory=list)
    fees_paid: float = 0.0
    funding_paid: float = 0.0
    rebalances: int = 0
    weights: list[float] = field(default_factory=list)

    @property
    def return_pct(self) -> float:
        if len(self.equity) < 2 or self.equity[0] <= 0:
            return 0.0
        return (self.equity[-1] / self.equity[0] - 1) * 100

    @property
    def max_drawdown_pct(self) -> float:
        peak = -math.inf
        worst = 0.0
        for value in self.equity:
            peak = max(peak, value)
            if peak > 0:
                worst = max(worst, (peak - value) / peak)
        return worst * 100

    @property
    def mean_weight(self) -> float:
        return statistics.fmean(self.weights) if self.weights else 0.0

    @property
    def ratio(self) -> float:
        """수익 ÷ 최대낙폭. 낙폭이 0이면 비교 의미가 없으므로 0."""
        mdd = self.max_drawdown_pct
        return self.return_pct / mdd if mdd > 0 else 0.0

    def realized_vol_pct(self, bars_per_year: float) -> float:
        """실현 연변동성. 목표를 실제로 맞췄는지 확인하는 값이다."""
        rets = [
            b / a - 1 for a, b in zip(self.equity, self.equity[1:]) if a > 0
        ]
        if len(rets) < 3:
            return 0.0
        return statistics.pstdev(rets) * math.sqrt(bars_per_year) * 100


def realized_vol(candles: list[Candle], index: int, lookback: int) -> float | None:
    """index 시점까지의 실현 변동성(봉당 로그수익 표준편차). 미래를 보지 않는다."""
    start = index - lookback
    if start < 1:
        return None
    rets = []
    for i in range(start, index + 1):
        prev, now = candles[i - 1].close, candles[i].close
        if prev > 0 and now > 0:
            rets.append(math.log(now / prev))
    if len(rets) < 3:
        return None
    spread = statistics.pstdev(rets)
    return spread if spread > 0 else None


@dataclass
class VolTargetResult:
    hold: Curve
    targeted: Curve
    target_vol: float
    lookback: int
    max_leverage: float
    bars_per_year: float
    rebalance_band: float
    venue: str = "spot"

    def report(self) -> str:
        lines = [
            "═" * 76,
            "  변동성 타게팅 — 방향을 맞히지 않고 노출만 조절하면",
            "═" * 76,
            f"  목표 연변동성 {self.target_vol * 100:.0f}%"
            f"   측정 구간 {self.lookback}봉"
            f"   최대 배율 {self.max_leverage:.1f}x"
            f"   재조정 밴드 {self.rebalance_band * 100:.0f}%",
            f"  체결 방식 {self.venue}"
            + ("  (1배까지 현물 — 펀딩비 없음, 초과분만 선물)" if self.venue == "spot"
               else "  (전체 노출에 펀딩비)"),
            "─" * 76,
            f"  {'':<16}{'수익률':>12}{'MDD':>9}{'수익/MDD':>10}"
            f"{'실현변동성':>12}{'평균배율':>10}{'재조정':>8}",
            "─" * 76,
        ]
        for curve in (self.hold, self.targeted):
            lines.append(
                f"  {curve.label:<16}{curve.return_pct:>+11.2f}%"
                f"{curve.max_drawdown_pct:>8.1f}%{curve.ratio:>10.2f}"
                f"{curve.realized_vol_pct(self.bars_per_year):>11.1f}%"
                f"{curve.mean_weight:>10.2f}x{curve.rebalances:>8,}"
            )
        lines += [
            "─" * 76,
            f"  타게팅이 낸 비용   수수료 {self.targeted.fees_paid:.1f}%"
            f"   펀딩 {self.targeted.funding_paid:.1f}%",
            "═" * 76,
        ]
        return "\n".join(lines + self._verdict())

    def _verdict(self) -> list[str]:
        hold, targeted = self.hold, self.targeted
        out = []

        if targeted.max_drawdown_pct < hold.max_drawdown_pct:
            cut = (1 - targeted.max_drawdown_pct / hold.max_drawdown_pct) * 100
            out.append(f"  낙폭이 {hold.max_drawdown_pct:.1f}% →"
                       f" {targeted.max_drawdown_pct:.1f}%로 {cut:.0f}% 줄었습니다.")
        else:
            out.append(f"  ✗ 낙폭이 줄지 않았습니다"
                       f" ({hold.max_drawdown_pct:.1f}% → {targeted.max_drawdown_pct:.1f}%).")

        kept = (targeted.return_pct / hold.return_pct * 100) if hold.return_pct > 0 else 0.0
        out.append(f"  수익은 {hold.return_pct:+.1f}% → {targeted.return_pct:+.1f}%"
                   f" ({kept:.0f}% 유지).")

        if targeted.ratio > hold.ratio:
            out.append(f"  ✓ 리스크 대비로 낫습니다 ({hold.ratio:.2f} → {targeted.ratio:.2f}).")
            out.append("  ※ 수익을 늘린 게 아니라 리스크를 낮춘 것입니다."
                       " 같은 낙폭까지 레버리지를 올릴 여지가 생긴 것이 이득의 실체입니다.")
        else:
            out.append(f"  ✗ 리스크 대비로도 낫지 않습니다"
                       f" ({hold.ratio:.2f} → {targeted.ratio:.2f}).")

        achieved = targeted.realized_vol_pct(self.bars_per_year)
        miss = abs(achieved - self.target_vol * 100)
        if miss > self.target_vol * 100 * 0.35:
            out.append(f"  ⚠ 목표 {self.target_vol * 100:.0f}%를 겨냥했는데"
                       f" 실현은 {achieved:.1f}%입니다. 측정 구간이나 배율 상한이"
                       " 맞지 않을 수 있습니다.")
        out.append("  ※ 펀딩비와 재조정 수수료를 모두 뺀 값입니다.")
        return out


def analyse(
    candles: list[Candle],
    cfg: Config,
    *,
    target_vol: float = 0.50,
    lookback: int = 30,
    max_leverage: float = 3.0,
    rebalance_band: float = 0.10,
    venue: str = "spot",
    bars_per_year: float | None = None,
) -> VolTargetResult:
    """계속 롱으로 들고 있되, 수량만 변동성에 반비례시킨다.

    rebalance_band는 목표 배율이 현재 배율에서 이 비율 이상 벗어날 때만
    재조정한다는 뜻이다. 매 봉 재조정하면 수수료가 이득을 먹는다.

    venue="spot"이면 1배까지는 현물로 들고 있다고 보아 펀딩비를 물리지 않는다.
    "perp"면 전체 노출에 문다. 벤치마크(매수 후 보유)와 조건을 맞추려면
    spot이 맞다 — 현물을 그냥 들고 있는 사람은 펀딩비를 내지 않는다.
    """
    if venue not in ("spot", "perp"):
        raise ValueError(f"venue는 spot 또는 perp여야 합니다: {venue}")
    interval_ms = candles[1].ts - candles[0].ts if len(candles) > 1 else 3600_000
    if bars_per_year is None:
        bars_per_year = 365.25 * 24 * 3600_000 / interval_ms
    bar_vol_target = target_vol / math.sqrt(bars_per_year)

    cost = cfg.exchange.taker_fee + cfg.exchange.slippage
    funding_per_bar = (
        cfg.exchange.funding_rate * interval_ms
        / (cfg.exchange.funding_interval_hours * 3600_000)
    )

    def funded_notional(w: float) -> float:
        """펀딩비를 무는 명목가.

        perp: 전체에 문다. 무기한 선물로 노출을 만들면 1배도 펀딩을 낸다.
        spot: 1배까지는 현물이라 안 낸다. 1배를 넘는 부분만 선물로 얹는다.

        이 구분이 결론을 바꾼다. 8.6년을 1배로 들고 있으면 펀딩만 100%가 넘는다.
        현물 벤치마크와 선물 전략을 나란히 놓으면 전략이 부당하게 진다.
        """
        return w if venue == "perp" else max(0.0, w - 1.0)

    hold = Curve("매수 후 보유", [1.0])
    targeted = Curve(f"변동성 타게팅", [1.0])
    weight = 0.0

    start = lookback + 1
    for i in range(start, len(candles)):
        prev, now = candles[i - 1].close, candles[i].close
        if prev <= 0:
            continue
        change = now / prev - 1

        hold.equity.append(hold.equity[-1] * (1 + change))
        hold.weights.append(1.0)

        # 배율은 **직전 봉까지의** 변동성으로 정한다. 현재 봉을 보면 미래참조다.
        spread = realized_vol(candles, i - 1, lookback)
        wanted = 0.0 if spread is None else min(bar_vol_target / spread, max_leverage)

        if weight == 0.0 or abs(wanted - weight) > rebalance_band * max(weight, 1e-9):
            turnover = abs(wanted - weight)
            fee = turnover * cost
            targeted.equity[-1] *= 1 - fee
            targeted.fees_paid += fee * 100
            targeted.rebalances += 1
            weight = wanted

        funding = funded_notional(weight) * funding_per_bar
        targeted.funding_paid += funding * 100
        targeted.equity.append(targeted.equity[-1] * (1 + weight * change - funding))
        targeted.weights.append(weight)

    return VolTargetResult(
        hold, targeted, target_vol, lookback, max_leverage, bars_per_year,
        rebalance_band, venue,
    )
