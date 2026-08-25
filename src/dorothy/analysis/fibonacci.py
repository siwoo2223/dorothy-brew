"""피보나치 되돌림 / 확장 + 레벨 허용오차.

말씀하신 "고점 저점에 슬리피지를 넣는다"를 여기서 구현한다.
두 가지 의미가 있고 둘 다 필요하다:

1. **레벨 허용오차 (tolerance)** — 가격이 0.618을 소수점까지 정확히 찍는 일은 없다.
   ATR 비례 밴드를 씌워 "그 근처"를 인정한다. 이걸 안 하면 신호가 거의 안 나온다.
2. **체결 슬리피지** — 이미 PaperExchange에 반영돼 있다 (항상 불리한 방향).

허용오차를 고정 퍼센트가 아니라 ATR 비례로 잡는 이유:
변동성이 커지면 밴드도 같이 넓어져야 한다. 고정값은 조용한 장에서 너무 넓고
급변 장에서 너무 좁다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 되돌림 비율 — 0.705는 0.618과 0.786의 중간(ICT에서 흔히 쓰는 값)
RETRACEMENT_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.705, 0.786, 0.886)
EXTENSION_RATIOS = (1.272, 1.414, 1.618, 2.0, 2.618)

# ICT의 OTE(Optimal Trade Entry) 구간
OTE_LOW, OTE_HIGH = 0.62, 0.79


@dataclass(frozen=True)
class Zone:
    """가격 구간. 허용오차가 이미 반영된 상태."""

    low: float
    high: float
    label: str = ""

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high

    def touched_by(self, bar_low: float, bar_high: float) -> bool:
        """캔들의 고저 범위가 이 구간과 겹치는가 (꼬리로 스친 것도 포함)."""
        return bar_high >= self.low and bar_low <= self.high

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass(frozen=True)
class Leg:
    """되돌림을 재는 기준 구간. start → end 방향이 추세 방향이다."""

    start: float
    end: float

    @property
    def is_up(self) -> bool:
        return self.end > self.start

    @property
    def size(self) -> float:
        return abs(self.end - self.start)

    def retracement(self, ratio: float) -> float:
        """되돌림 가격. ratio=0이면 end, 1이면 start."""
        return self.end - (self.end - self.start) * ratio

    def extension(self, ratio: float) -> float:
        """확장 목표가. ratio=1.618이면 다리 길이의 1.618배 지점."""
        return self.start + (self.end - self.start) * ratio

    def retracement_of(self, price: float) -> float:
        """현재가가 몇 % 되돌렸는지. 0=미되돌림, 1=전량 되돌림, >1=돌파."""
        if self.size == 0:
            return 0.0
        return (self.end - price) / (self.end - self.start)


def levels(leg: Leg, ratios: tuple[float, ...] = RETRACEMENT_RATIOS) -> dict[float, float]:
    return {r: leg.retracement(r) for r in ratios}


def extensions(leg: Leg, ratios: tuple[float, ...] = EXTENSION_RATIOS) -> dict[float, float]:
    return {r: leg.extension(r) for r in ratios}


def zone_at(leg: Leg, ratio: float, *, atr: float, tolerance_mult: float = 0.25) -> Zone:
    """특정 되돌림 레벨에 ATR 비례 허용오차를 씌운 구간."""
    price = leg.retracement(ratio)
    pad = atr * tolerance_mult
    return Zone(price - pad, price + pad, f"fib {ratio:.3f}")


def ote_zone(leg: Leg, *, atr: float = 0.0, tolerance_mult: float = 0.0) -> Zone:
    """OTE 구간 (0.62~0.79 되돌림).

    이미 구간이라 허용오차가 필수는 아니지만, 경계에서 스치는 것도
    인정하고 싶으면 tolerance_mult를 준다.
    """
    a = leg.retracement(OTE_LOW)
    b = leg.retracement(OTE_HIGH)
    low, high = min(a, b), max(a, b)
    pad = atr * tolerance_mult
    return Zone(low - pad, high + pad, "OTE 0.62-0.79")


def equal_within(a: float, b: float, *, atr: float, tolerance_mult: float = 0.1) -> bool:
    """두 가격이 '사실상 같은 레벨'인가.

    등가 고점/저점(EQH/EQL) 판정의 기초다. 정확히 같은 값을 요구하면
    실제 차트에서는 거의 걸리지 않는다.
    """
    return abs(a - b) <= atr * tolerance_mult
