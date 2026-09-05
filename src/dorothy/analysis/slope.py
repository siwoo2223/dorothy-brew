"""캔들 상승/하락 각도.

⚠ 먼저 짚어야 할 것: **차트에서 눈으로 보는 각도는 그 자체로 의미가 없다.**

같은 데이터라도 세로축을 늘리면 45도가 70도가 되고, 창을 넓히면 20도가 된다.
"BTC가 60도로 올라간다"는 문장은 차트 창 크기에 의존하는 말이라
코드로 옮기면 재현되지 않는다. 종목이 다르면 더 심하다
(BTC 1000달러 움직임과 XRP 0.01달러 움직임을 같은 각도로 비교할 수 없다).

그래서 여기서는 **ATR로 정규화한 각도**를 쓴다.
    "1봉당 1 ATR만큼 움직이면 45도"
이렇게 정의하면 종목·타임프레임·가격대가 달라도 같은 숫자를 비교할 수 있다.
ICT에서 말하는 displacement(변위)를 수치화한 것이라고 보면 된다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..models import Candle
from ..data.indicators import atr as atr_indicator


@dataclass(frozen=True)
class Slope:
    degrees: float        # ATR 정규화 각도 (+상승 / -하락). 45도 = 1봉당 1 ATR
    per_bar: float        # 1봉당 가격 변화량 (원 단위)
    atr: float            # 정규화에 쓴 ATR
    r_squared: float      # 추세의 '깔끔함' (1에 가까울수록 직선적)

    @property
    def is_steep(self) -> bool:
        return abs(self.degrees) >= 45.0

    @property
    def is_clean(self) -> bool:
        """지그재그 없이 한 방향으로 밀어붙였는가."""
        return self.r_squared >= 0.7


def linear_regression(values: list[float]) -> tuple[float, float, float]:
    """최소제곱 직선. (기울기, 절편, R²)를 돌려준다.

    두 점만 잇는 것보다 낫다. 중간 흔들림까지 반영하므로
    '깔끔한 추세'와 '결국 같은 자리에 온 톱니'를 구분할 수 있다.
    """
    n = len(values)
    if n < 2:
        return 0.0, values[0] if values else 0.0, 0.0

    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    sxx = sum((i - mean_x) ** 2 for i in range(n))
    sxy = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    if sxx == 0:
        return 0.0, mean_y, 0.0

    slope = sxy / sxx
    intercept = mean_y - slope * mean_x

    ss_tot = sum((v - mean_y) ** 2 for v in values)
    ss_res = sum((v - (slope * i + intercept)) ** 2 for i, v in enumerate(values))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, intercept, max(0.0, r2)


def normalize_angle(per_bar: float, atr: float) -> float:
    """가격 변화량을 ATR 기준 각도로 변환한다.

    1봉당 1 ATR = 45도, 2 ATR ≈ 63도, 0.5 ATR ≈ 27도.
    atan을 쓰므로 아무리 급해도 90도를 넘지 않는다 (이상치에 강하다).
    """
    if atr <= 0:
        return 0.0
    return math.degrees(math.atan(per_bar / atr))


def measure(
    candles: list[Candle], *, period: int = 10, atr_period: int = 14, end: int | None = None
) -> Slope | None:
    """최근 `period`봉의 각도를 잰다. end를 주면 그 시점 기준으로 계산한다."""
    last = len(candles) - 1 if end is None else end
    start = last - period + 1
    if start < 0 or last >= len(candles):
        return None

    atr_line = atr_indicator(
        [c.high for c in candles], [c.low for c in candles], [c.close for c in candles], atr_period
    )
    atr_value = atr_line[last]
    if atr_value is None or atr_value <= 0:
        return None

    closes = [c.close for c in candles[start : last + 1]]
    per_bar, _, r2 = linear_regression(closes)
    return Slope(normalize_angle(per_bar, atr_value), per_bar, atr_value, r2)


def leg_angle(
    candles: list[Candle], start_index: int, end_index: int, *, atr_period: int = 14
) -> Slope | None:
    """스윙 구간(다리) 하나의 각도. 파동의 기울기를 비교할 때 쓴다.

    엘리엇에서 '3파는 보통 가장 가파르다'를 검증하려면 이 값이 필요하다.
    """
    if end_index <= start_index or end_index >= len(candles):
        return None

    atr_line = atr_indicator(
        [c.high for c in candles], [c.low for c in candles], [c.close for c in candles], atr_period
    )
    atr_value = atr_line[end_index]
    if atr_value is None or atr_value <= 0:
        return None

    closes = [c.close for c in candles[start_index : end_index + 1]]
    per_bar, _, r2 = linear_regression(closes)
    return Slope(normalize_angle(per_bar, atr_value), per_bar, atr_value, r2)
