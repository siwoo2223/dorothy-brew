"""박스(횡보 구간) 탐지 — 어디가 바닥이고 어디가 천장인가.

**왜 지표가 아니라 이걸 쓰는가**
Choppiness Index나 ADX는 "지금 횡보인가"만 말한다. 매매하려면 그것으로는
부족하다. **바닥과 천장이 어디인지**를 숫자로 받아야 주문을 낼 수 있다.

**박스의 조건 셋** (전부 만족해야 박스로 인정한다)

  1. 방향이 없다        variance ratio가 1보다 뚜렷이 작거나 근처
  2. 폭이 수수료를 덮는다  박스 높이가 왕복 비용의 몇 배 이상
  3. 경계가 실제로 눌렸다  상단·하단에 각각 여러 번 닿았다

3번이 핵심이다. 단순히 "최근 N봉의 고저"를 박스라고 부르면 **추세 구간도
전부 박스가 된다** — 한 방향으로 쭉 간 구간도 고저 폭은 있으니까.
경계에 반복해서 닿았는지를 봐야 진짜 박스다.

**인과성**: 판정 시점까지의 봉만 쓴다. 미래 봉으로 경계를 그리면
백테스트가 통째로 거짓이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Candle
from .regime import variance_ratio


@dataclass(frozen=True)
class Box:
    """확정된 박스 하나."""

    lower: float
    upper: float
    touches_lower: int
    touches_upper: int
    variance_ratio: float
    bars: int

    @property
    def height(self) -> float:
        return self.upper - self.lower

    @property
    def height_pct(self) -> float:
        mid = (self.upper + self.lower) / 2
        return self.height / mid if mid > 0 else 0.0

    @property
    def mid(self) -> float:
        return (self.upper + self.lower) / 2

    def position_of(self, price: float) -> float:
        """가격이 박스 안 어디쯤인가. 0=하단, 1=상단. 밖이면 0 미만/1 초과."""
        if self.height <= 0:
            return 0.5
        return (price - self.lower) / self.height


def detect(
    candles: list[Candle],
    *,
    lookback: int = 30,
    min_touches: int = 2,
    touch_zone: float = 0.15,
    min_height_pct: float = 0.01,
    max_variance_ratio: float = 1.05,
    vr_k: int = 4,
) -> Box | None:
    """마지막 봉 기준으로 박스를 찾는다. 없으면 None.

    lookback        박스를 그릴 봉 수
    min_touches     상·하단에 각각 최소 몇 번 닿아야 하는가
    touch_zone      경계에서 이 비율 안쪽이면 '닿았다' (0.15 = 박스 높이의 15%)
    min_height_pct  박스 높이가 이보다 좁으면 수수료를 못 덮는다
    max_variance_ratio  이보다 크면 추세다 (1.0 근처와 그 아래만 박스로 본다)
    """
    if lookback < 4 or len(candles) < lookback:
        return None
    window = candles[-lookback:]

    upper = max(c.high for c in window)
    lower = min(c.low for c in window)
    height = upper - lower
    mid = (upper + lower) / 2
    if height <= 0 or mid <= 0:
        return None
    if height / mid < min_height_pct:
        return None

    vr = variance_ratio([c.close for c in window], vr_k)
    if vr > max_variance_ratio:
        return None

    zone = height * touch_zone
    touches_upper = sum(1 for c in window if c.high >= upper - zone)
    touches_lower = sum(1 for c in window if c.low <= lower + zone)
    if touches_upper < min_touches or touches_lower < min_touches:
        return None

    return Box(
        lower=lower, upper=upper,
        touches_lower=touches_lower, touches_upper=touches_upper,
        variance_ratio=vr, bars=lookback,
    )
