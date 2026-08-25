"""전략 공통 부품.

여러 전략을 **공정하게 비교**하려면 손절·목표 방식이 같아야 한다.
A 전략은 ATR 손절, B 전략은 고정 퍼센트 손절이면 성과 차이가
'분석 방법의 차이'인지 '손절 방식의 차이'인지 구분할 수 없다.

그래서 손절/목표 로직을 여기 모아 모든 전략이 공유한다.
전략 간에 다른 것은 **진입 시점 하나**뿐이 되도록 만드는 것이 목적이다.
"""

from __future__ import annotations

from ..data.indicators import atr as atr_indicator
from ..models import Action, Candle, Signal


def atr_at(candles: list[Candle], period: int) -> float | None:
    value = atr_indicator(
        [c.high for c in candles], [c.low for c in candles],
        [c.close for c in candles], period,
    )[-1]
    return value if value and value > 0 else None


def entry_signal(
    *,
    long: bool,
    price: float,
    atr: float,
    stop_mult: float,
    target_mult: float,
    reason: str,
    meta: dict | None = None,
) -> Signal:
    """ATR 기반 손절·목표를 붙인 진입 신호.

    모든 전략이 이걸 쓰므로, 비교 실험에서 손절 방식은 상수가 된다.
    """
    if long:
        return Signal(
            Action.ENTER_LONG, reason,
            stop_loss=price - atr * stop_mult,
            take_profit=price + atr * target_mult,
            meta=meta or {},
        )
    return Signal(
        Action.ENTER_SHORT, reason,
        stop_loss=price + atr * stop_mult,
        take_profit=price - atr * target_mult,
        meta=meta or {},
    )
