"""예시 전략: EMA 골든/데드크로스 + ATR 손절.

이 전략은 '동작 확인용 샘플'이지 수익 보장 전략이 아니다.
추세장에서는 먹고 횡보장에서는 계속 털리는 전형적인 추세추종이며,
수수료를 반영하면 대부분의 구간에서 마이너스가 난다.
자기 전략으로 갈아끼우기 위한 뼈대로만 쓸 것.
"""

from __future__ import annotations

from ..data.indicators import atr, ema
from ..models import Action, Candle, Position, Side, Signal
from .base import Strategy, register


@register
class EmaCrossStrategy(Strategy):
    name = "ema_cross"

    def __init__(
        self,
        fast: int = 20,
        slow: int = 50,
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,
        atr_target_mult: float = 3.0,
        allow_short: bool = True,
    ) -> None:
        super().__init__(
            fast=fast, slow=slow, atr_period=atr_period,
            atr_stop_mult=atr_stop_mult, atr_target_mult=atr_target_mult,
            allow_short=allow_short,
        )
        if fast >= slow:
            raise ValueError("fast는 slow보다 작아야 합니다.")
        self.fast = fast
        self.slow = slow
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.allow_short = allow_short

    @property
    def warmup(self) -> int:
        return max(self.slow, self.atr_period) + 2

    def generate(self, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return Signal(Action.HOLD, "워밍업 부족")

        closes = [c.close for c in candles]
        fast_line = ema(closes, self.fast)
        slow_line = ema(closes, self.slow)
        atr_line = atr([c.high for c in candles], [c.low for c in candles], closes, self.atr_period)

        f0, s0 = fast_line[-1], slow_line[-1]
        f1, s1 = fast_line[-2], slow_line[-2]
        a0 = atr_line[-1]
        if None in (f0, s0, f1, s1, a0):
            return Signal(Action.HOLD, "지표 미계산")

        cross_up = f1 <= s1 and f0 > s0
        cross_down = f1 >= s1 and f0 < s0
        price = closes[-1]

        # --- 보유 중: 반대 크로스에서만 청산 (손절/익절은 거래소 스탑이 처리) ---
        if position is not None:
            if position.side is Side.LONG and cross_down:
                return Signal(Action.EXIT, "데드크로스")
            if position.side is Side.SHORT and cross_up:
                return Signal(Action.EXIT, "골든크로스")
            return Signal(Action.HOLD, "포지션 유지")

        # --- 미보유: 크로스에서 진입 ---
        if cross_up:
            return Signal(
                Action.ENTER_LONG,
                f"골든크로스 EMA{self.fast}>{self.slow}",
                stop_loss=price - a0 * self.atr_stop_mult,
                take_profit=price + a0 * self.atr_target_mult,
            )
        if cross_down and self.allow_short:
            return Signal(
                Action.ENTER_SHORT,
                f"데드크로스 EMA{self.fast}<{self.slow}",
                stop_loss=price + a0 * self.atr_stop_mult,
                take_profit=price - a0 * self.atr_target_mult,
            )
        return Signal(Action.HOLD, "신호 없음")
