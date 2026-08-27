"""돈치안 채널 돌파 — 추세추종의 원형.

터틀 트레이딩으로 알려진 방식이다. 파라미터가 사실상 하나(채널 길이)뿐이라
**과최적화할 여지가 거의 없다**. 이게 이 전략의 최대 장점이다.

여러 전략을 비교할 때 이걸 넣는 이유:
복잡한 전략이 이 단순한 것보다 못하다면, 그 복잡성은 값을 못 하는 것이다.
"""

from __future__ import annotations

from ..models import Action, Candle, Position, Side, Signal
from .base import Strategy, register
from .common import atr_at, entry_signal


@register
class DonchianBreakoutStrategy(Strategy):
    name = "donchian"
    retired = (
        "롱숏 대칭 기본 설정이 5년 실제 데이터에서 2,068거래 -84.89% (무작위 대조군 -33.12%보다 나쁨). 4시간봉·채널40·숏 끔으로 바꾸면 194거래 +23.49%가 되지만, 채널을 골라야 해서 워크포워드 효율이 0.02로 무너진다"
    )

    def __init__(
        self,
        channel: int = 20,
        exit_channel: int = 10,
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,
        atr_target_mult: float = 3.0,
        allow_short: bool = True,
    ) -> None:
        super().__init__(
            channel=channel, exit_channel=exit_channel, atr_period=atr_period,
            atr_stop_mult=atr_stop_mult, atr_target_mult=atr_target_mult,
            allow_short=allow_short,
        )
        if channel < 2:
            raise ValueError("channel은 2 이상이어야 합니다.")
        self.channel = channel
        self.exit_channel = exit_channel
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.allow_short = allow_short

    @property
    def warmup(self) -> int:
        return max(self.channel, self.atr_period) + 2

    def generate(self, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return Signal(Action.HOLD, "워밍업 부족")

        atr = atr_at(candles, self.atr_period)
        if atr is None:
            return Signal(Action.HOLD, "ATR 미계산")

        # 현재 봉을 제외한 과거 N봉의 고저 (현재 봉을 포함하면 항상 자기 자신이 최고가다)
        window = candles[-self.channel - 1 : -1]
        upper = max(c.high for c in window)
        lower = min(c.low for c in window)
        price = candles[-1].close

        if position is not None:
            exit_window = candles[-self.exit_channel - 1 : -1]
            if position.side is Side.LONG and price < min(c.low for c in exit_window):
                return Signal(Action.EXIT, f"{self.exit_channel}봉 저가 이탈")
            if position.side is Side.SHORT and price > max(c.high for c in exit_window):
                return Signal(Action.EXIT, f"{self.exit_channel}봉 고가 돌파")
            return Signal(Action.HOLD, "포지션 유지")

        if price > upper:
            return entry_signal(
                long=True, price=price, atr=atr,
                stop_mult=self.atr_stop_mult, target_mult=self.atr_target_mult,
                reason=f"{self.channel}봉 고가 돌파 ({upper:.2f})",
            )
        if price < lower and self.allow_short:
            return entry_signal(
                long=False, price=price, atr=atr,
                stop_mult=self.atr_stop_mult, target_mult=self.atr_target_mult,
                reason=f"{self.channel}봉 저가 이탈 ({lower:.2f})",
            )
        return Signal(Action.HOLD, "채널 내부")
