"""TMA 밴드 — 그리고 그 유명한 함정.

MT4/MT5에서 널리 쓰이는 'TMA 밴드'는 **중심이동(centered) 삼각이동평균**을 쓴다.
중심이동은 i 시점 값을 계산할 때 i 이후 캔들을 쓴다. 그래서:

- 차트에서 가격을 기가 막히게 따라가는 것처럼 보인다
- 백테스트 성과가 훌륭하게 나온다
- **실시간에는 그 값을 알 수 없다.** 새 캔들이 오면 과거 선이 다시 그려진다

이 전략은 기본적으로 **인과적(causal) TMA**를 쓴다.
`centered=True`는 함정을 실측해 보여주기 위한 옵션이며 실거래에 쓰면 안 된다.
켜면 백테스트 수익률이 뛰는데, 그건 실력이 아니라 미래를 본 결과다.
"""

from __future__ import annotations

import logging

from ..data.indicators import tma, tma_centered
from ..models import Action, Candle, Position, Side, Signal
from .base import Strategy, register
from .common import atr_at, bounded, entry_signal

log = logging.getLogger(__name__)


@register
class TmaBandStrategy(Strategy):
    name = "tma_band"

    def __init__(
        self,
        period: int = 20,
        band_atr_mult: float = 1.5,
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,
        atr_target_mult: float = 3.0,
        centered: bool = False,
        allow_short: bool = True,
        analysis_window: int = 500,
    ) -> None:
        super().__init__(
            period=period, band_atr_mult=band_atr_mult, atr_period=atr_period,
            atr_stop_mult=atr_stop_mult, atr_target_mult=atr_target_mult,
            centered=centered, allow_short=allow_short,
            analysis_window=analysis_window,
        )
        self.period = period
        self.band_atr_mult = band_atr_mult
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.centered = centered
        self.allow_short = allow_short
        self.analysis_window = analysis_window
        if centered:
            log.warning(
                "tma_band(centered=True)는 미래를 봅니다. "
                "백테스트 비교용이며 실거래에 쓰면 안 됩니다."
            )

    @property
    def warmup(self) -> int:
        return self.period * 3 + self.atr_period + 5

    def generate(self, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return Signal(Action.HOLD, "워밍업 부족")

        candles = bounded(candles, self.analysis_window)
        atr = atr_at(candles, self.atr_period)
        if atr is None:
            return Signal(Action.HOLD, "ATR 미계산")

        closes = [c.close for c in candles]
        line = tma_centered(closes, self.period) if self.centered else tma(closes, self.period)
        mid = line[-1]
        if mid is None:
            return Signal(Action.HOLD, "지표 미계산")

        band = atr * self.band_atr_mult
        upper, lower = mid + band, mid - band
        price = closes[-1]
        prev_price = closes[-2]

        if position is not None:
            # 중심선 복귀에서 청산 (평균회귀 성격)
            if position.side is Side.LONG and price >= mid:
                return Signal(Action.EXIT, "중심선 복귀")
            if position.side is Side.SHORT and price <= mid:
                return Signal(Action.EXIT, "중심선 복귀")
            return Signal(Action.HOLD, "포지션 유지")

        # 하단 밴드를 이탈했다 되돌아오면 롱 (반대는 숏)
        if prev_price <= lower < price:
            return entry_signal(
                long=True, price=price, atr=atr,
                stop_mult=self.atr_stop_mult, target_mult=self.atr_target_mult,
                reason=f"하단 밴드 반등 ({lower:.2f})",
            )
        if prev_price >= upper > price and self.allow_short:
            return entry_signal(
                long=False, price=price, atr=atr,
                stop_mult=self.atr_stop_mult, target_mult=self.atr_target_mult,
                reason=f"상단 밴드 이탈 ({upper:.2f})",
            )
        return Signal(Action.HOLD, "밴드 내부")
