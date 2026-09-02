"""손절이 먼저 걸리는가, 청산이 먼저 오는가.

**이 모듈이 막는 사고:**
리스크 사이징을 쓰면 레버리지는 수량을 바꾸지 않는다 — 1배든 20배든
같은 수량이 잡힌다(그건 risk_per_trade가 정한다). 그래서 "배율은 상관없다"고
생각하기 쉽다. **틀렸다.** 배율은 그 포지션에 걸리는 증거금을 정하고,
증거금이 청산가를 정한다.

    청산까지의 거리 ≈ 1/레버리지 − 유지증거금률

    10배  → 9.5%    20배 → 4.5%
     5배  → 19.5%   50배 → 1.5%

여기에 손절폭(ATR × 배수)을 겹쳐 보면 답이 나온다.
**손절폭이 청산 거리보다 멀면 손절은 영원히 발동하지 않는다.**
거래소가 먼저 가져간다. 그러면 잃는 것이 '계획된 1%'가 아니라
'그 포지션의 증거금 전부'가 된다.

배율을 고를 때 봐야 하는 것은 수익이 아니라 이 여유폭이다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass


def liquidation_distance(leverage: float, maintenance_margin: float = 0.005) -> float:
    """진입가에서 청산가까지의 거리 (진입가 대비 비율).

    격리 마진 기준 근사. 수수료와 펀딩은 뺐으므로 **실제는 이보다 가깝다.**
    여유를 두고 판단하라는 뜻이다.
    """
    if leverage <= 0:
        raise ValueError("레버리지는 0보다 커야 합니다.")
    return max(0.0, 1.0 / leverage - maintenance_margin)


def max_safe_leverage(
    stop_distance_pct: float,
    *,
    safety: float = 2.0,
    maintenance_margin: float = 0.005,
) -> float:
    """손절폭이 주어졌을 때, 청산이 손절보다 `safety`배 멀도록 하는 최대 배율.

    safety=2.0이면 "청산은 손절보다 최소 두 배 멀어야 한다"는 뜻이다.
    1.0으로 두면 손절과 청산이 같은 자리가 되어 아무 여유가 없다.
    """
    if stop_distance_pct <= 0:
        raise ValueError("손절폭은 0보다 커야 합니다.")
    if safety < 1.0:
        raise ValueError("safety는 1.0 이상이어야 합니다 (1.0이면 여유가 없습니다).")
    need = stop_distance_pct * safety + maintenance_margin
    if need >= 1.0:
        return 1.0
    return 1.0 / need


@dataclass
class LeverageCheck:
    """한 배율에서, 실제 손절폭 분포와 견줘본 결과."""

    leverage: float
    liq_distance: float
    stop_distances: list[float]
    safety: float = 2.0

    @property
    def unsafe_count(self) -> int:
        """청산이 손절보다 가까웠던(= 손절이 무력한) 매매 수."""
        return sum(1 for d in self.stop_distances if d >= self.liq_distance)

    @property
    def unsafe_share(self) -> float:
        if not self.stop_distances:
            return 0.0
        return self.unsafe_count / len(self.stop_distances) * 100

    @property
    def thin_share(self) -> float:
        """여유폭이 safety배에 못 미친 비율. 청산은 안 됐어도 위험하다."""
        if not self.stop_distances:
            return 0.0
        thin = sum(1 for d in self.stop_distances if d * self.safety >= self.liq_distance)
        return thin / len(self.stop_distances) * 100

    @property
    def verdict(self) -> str:
        if self.unsafe_count > 0:
            # 18,946건 중 1건은 "0.0%"로 찍힌다 — ✗와 앞뒤가 안 맞는다.
            # 비율이 반올림돼 사라지면 건수로 말한다.
            if self.unsafe_share < 0.1:
                return (
                    f"✗ {self.unsafe_count}건에서 손절보다 청산이 가깝습니다 "
                    f"({len(self.stop_distances):,}건 중)"
                )
            return f"✗ {self.unsafe_share:.0f}%의 매매에서 손절보다 청산이 가깝습니다"
        if self.thin_share > 10:
            return f"? 여유가 {self.safety:g}배 미만인 매매가 {self.thin_share:.0f}%입니다"
        return "✓ 손절이 먼저 걸립니다"


def stop_distances(
    candles, *, atr_period: int = 14, atr_stop_mult: float = 2.0
) -> list[float]:
    """봉마다 'ATR × 배수 ÷ 종가' — 실제로 잡히게 될 손절폭 분포."""
    from ..data.indicators import atr as atr_fn

    values = atr_fn(
        [c.high for c in candles], [c.low for c in candles],
        [c.close for c in candles], atr_period,
    )
    out = []
    for candle, a in zip(candles, values):
        if a is None or candle.close <= 0:
            continue
        out.append(a * atr_stop_mult / candle.close)
    return out


def analyse(
    candles,
    *,
    leverages=(1, 2, 3, 5, 10, 20, 50),
    atr_period: int = 14,
    atr_stop_mult: float = 2.0,
    safety: float = 2.0,
    maintenance_margin: float = 0.005,
) -> list[LeverageCheck]:
    dists = stop_distances(candles, atr_period=atr_period, atr_stop_mult=atr_stop_mult)
    return [
        LeverageCheck(
            leverage=float(lev),
            liq_distance=liquidation_distance(float(lev), maintenance_margin),
            stop_distances=dists,
            safety=safety,
        )
        for lev in leverages
    ]


def render(checks: list[LeverageCheck], *, atr_stop_mult: float = 2.0) -> str:
    if not checks:
        return "잴 것이 없습니다."
    dists = checks[0].stop_distances
    if not dists:
        return "ATR을 계산할 수 없습니다 (캔들이 부족합니다)."
    ordered = sorted(dists)
    def pct(p):
        return ordered[min(len(ordered) - 1, int(p * len(ordered)))] * 100

    lines = [
        f"손절폭 분포 (ATR × {atr_stop_mult:g} ÷ 종가, 표본 {len(dists):,}개)",
        f"  중앙값 {pct(0.50):.2f}%   상위 10% {pct(0.90):.2f}%   "
        f"상위 1% {pct(0.99):.2f}%   최대 {ordered[-1]*100:.2f}%",
        "",
        f"  {'배율':>5}{'청산까지':>10}{'손절이 무력':>12}{'여유 부족':>10}   판정",
        "  " + "─" * 62,
    ]
    for c in checks:
        lines.append(
            f"  {c.leverage:>4.0f}배{c.liq_distance * 100:>9.1f}%"
            f"{c.unsafe_share:>11.1f}%{c.thin_share:>9.1f}%   {c.verdict}"
        )
    worst = ordered[int(0.99 * len(ordered))]
    safe = max_safe_leverage(worst, safety=checks[0].safety)
    lines += [
        "  " + "─" * 62,
        "",
        f"  상위 1% 손절폭({worst * 100:.2f}%)까지 감당하려면 **{safe:.1f}배 이하**입니다.",
        "  ※ 수수료·펀딩·슬리피지를 뺀 근사라 실제 청산은 이보다 가깝습니다.",
    ]
    return "\n".join(lines)
