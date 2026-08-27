"""수퍼트렌드 추세추종.

거래 빈도가 낮은 것이 이 전략의 핵심 성질이다.
ATR 밴드가 추세 방향으로만 조여지기 때문에 잔파동에서 방향이 잘 바뀌지 않는다.

이 저장소에서 실측한 바로는, 같은 데이터·같은 리스크 설정에서
거래를 줄이는 것만으로 PF가 0.86 → 1.40으로 움직였다.
수수료가 총이익의 3배까지 나오는 구간이 있기 때문이다.
빈도가 낮다는 건 그 자체로 우위가 될 수 있다.
"""

from __future__ import annotations

from ..data.indicators import supertrend as supertrend_line
from ..models import Action, Candle, Position, Side, Signal
from .base import Strategy, register
from .common import atr_at, bounded, entry_signal


@register
class SupertrendStrategy(Strategy):
    name = "supertrend"

    def __init__(
        self,
        period: int = 10,
        multiplier: float = 3.0,
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,
        atr_target_mult: float = 3.0,
        exit_on_flip: bool = True,
        allow_short: bool = True,
        analysis_window: int = 500,
    ) -> None:
        super().__init__(
            period=period, multiplier=multiplier, atr_period=atr_period,
            atr_stop_mult=atr_stop_mult, atr_target_mult=atr_target_mult,
            exit_on_flip=exit_on_flip, allow_short=allow_short,
            analysis_window=analysis_window,
        )
        if multiplier <= 0:
            raise ValueError("multiplier는 0보다 커야 합니다.")
        self.period = period
        self.multiplier = multiplier
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.exit_on_flip = exit_on_flip
        self.allow_short = allow_short
        self.analysis_window = analysis_window

    @property
    def warmup(self) -> int:
        return max(self.period, self.atr_period) * 3 + 5

    def generate(self, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return Signal(Action.HOLD, "워밍업 부족")

        candles = bounded(candles, self.analysis_window)
        atr = atr_at(candles, self.atr_period)
        if atr is None:
            return Signal(Action.HOLD, "ATR 미계산")

        trend, line = supertrend_line(
            [c.high for c in candles], [c.low for c in candles],
            [c.close for c in candles], self.period, self.multiplier,
        )
        now, prev = trend[-1], trend[-2]
        if now is None or prev is None:
            return Signal(Action.HOLD, "지표 미계산")

        flipped_up = prev == -1 and now == 1
        flipped_down = prev == 1 and now == -1
        price = candles[-1].close

        if position is not None:
            if not self.exit_on_flip:
                return Signal(Action.HOLD, "포지션 유지")
            if position.side is Side.LONG and flipped_down:
                return Signal(Action.EXIT, "추세 하락 전환")
            if position.side is Side.SHORT and flipped_up:
                return Signal(Action.EXIT, "추세 상승 전환")
            return Signal(Action.HOLD, "포지션 유지")

        if flipped_up:
            return entry_signal(
                long=True, price=price, atr=atr,
                stop_mult=self.atr_stop_mult, target_mult=self.atr_target_mult,
                reason=f"수퍼트렌드 상승 전환 (선 {line[-1]:.2f})",
            )
        if flipped_down and self.allow_short:
            return entry_signal(
                long=False, price=price, atr=atr,
                stop_mult=self.atr_stop_mult, target_mult=self.atr_target_mult,
                reason=f"수퍼트렌드 하락 전환 (선 {line[-1]:.2f})",
            )
        return Signal(Action.HOLD, f"추세 유지 ({'상승' if now == 1 else '하락'})")
