"""RSI 평균회귀 — 추세추종의 반대편.

과매도에서 사고 과매수에서 판다. 횡보장에서 벌고 추세장에서 크게 잃는,
돈치안과 정확히 반대 성격이다.

두 계열을 함께 비교해야 하는 이유:
그 시장·그 타임프레임이 추세형인지 회귀형인지를 데이터가 말해주기 때문이다.
둘 다 안 되면 그 구간에는 시간 편향 자체가 없다는 뜻이다.
"""

from __future__ import annotations

from ..data.indicators import rsi as rsi_indicator
from ..models import Action, Candle, Position, Side, Signal
from .base import Strategy, register
from .common import atr_at, entry_signal


@register
class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    retired = (
        "5년 실제 데이터에서 1,035거래 -74.17% (무작위 대조군 -33.12%보다 나쁨). 방향별로는 숏이 롱보다 낫다 (숏 +0.007%/회 vs 롱 -0.241%/회, 수수료 후)"
    )

    def __init__(
        self,
        rsi_period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
        exit_level: float = 50.0,
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,
        atr_target_mult: float = 3.0,
        allow_short: bool = True,
    ) -> None:
        super().__init__(
            rsi_period=rsi_period, oversold=oversold, overbought=overbought,
            exit_level=exit_level, atr_period=atr_period, atr_stop_mult=atr_stop_mult,
            atr_target_mult=atr_target_mult, allow_short=allow_short,
        )
        if not 0 < oversold < overbought < 100:
            raise ValueError("0 < oversold < overbought < 100 이어야 합니다.")
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.exit_level = exit_level
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.allow_short = allow_short

    @property
    def warmup(self) -> int:
        return max(self.rsi_period, self.atr_period) + 3

    def generate(self, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return Signal(Action.HOLD, "워밍업 부족")

        atr = atr_at(candles, self.atr_period)
        if atr is None:
            return Signal(Action.HOLD, "ATR 미계산")

        # 전체 히스토리로 매번 다시 계산하면 O(n²)다 (ema_cross와 같은 문제).
        # RSI도 지수 평활이라 기간의 20배면 충분하다.
        window = self.rsi_period * 20
        recent = candles[-window:] if len(candles) > window else candles
        line = rsi_indicator([c.close for c in recent], self.rsi_period)
        now, prev = line[-1], line[-2]
        if now is None or prev is None:
            return Signal(Action.HOLD, "RSI 미계산")
        price = candles[-1].close

        if position is not None:
            if position.side is Side.LONG and now >= self.exit_level:
                return Signal(Action.EXIT, f"RSI {now:.1f} 중립 복귀")
            if position.side is Side.SHORT and now <= self.exit_level:
                return Signal(Action.EXIT, f"RSI {now:.1f} 중립 복귀")
            return Signal(Action.HOLD, "포지션 유지")

        # 과매도 구간에서 '빠져나오는' 순간에 산다. 계속 떨어지는 칼날을 잡지 않기 위해서다.
        if prev <= self.oversold < now:
            return entry_signal(
                long=True, price=price, atr=atr,
                stop_mult=self.atr_stop_mult, target_mult=self.atr_target_mult,
                reason=f"RSI 과매도 탈출 ({prev:.1f}→{now:.1f})",
            )
        if prev >= self.overbought > now and self.allow_short:
            return entry_signal(
                long=False, price=price, atr=atr,
                stop_mult=self.atr_stop_mult, target_mult=self.atr_target_mult,
                reason=f"RSI 과매수 이탈 ({prev:.1f}→{now:.1f})",
            )
        return Signal(Action.HOLD, f"RSI {now:.1f} 중립")
