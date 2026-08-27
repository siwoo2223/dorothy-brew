"""TMA 밴드 — 그리고 그 유명한 함정.

MT4/MT5에서 널리 쓰이는 'TMA 밴드'는 **중심이동(centered) 삼각이동평균**을 쓴다.
중심이동은 i 시점 값을 계산할 때 i 이후 캔들을 쓴다. 그래서:

- 차트에서 가격을 기가 막히게 따라가는 것처럼 보인다
- 백테스트 성과가 훌륭하게 나온다
- **실시간에는 그 값을 알 수 없다.** 새 캔들이 오면 과거 선이 다시 그려진다

세 가지 모드를 제공한다.

| mode | 설명 | 실거래 |
|---|---|---|
| `causal` | 일반 TMA (SMA 2회). 기본값 | ✅ |
| `delayed` | 중심이동 선을 쓰되 **계산이 끝난 지점만** 쓴다 | ✅ |
| `centered` | 중심이동 선의 현재 봉 값 | ❌ 신호가 아예 안 나온다 |

`delayed`가 핵심이다. 중심이동 TMA는 i 시점 값에 i+half 캔들이 필요하므로
현재 봉 값은 실시간에 존재하지 않는다. 하지만 **half봉 전 값은 이미 확정**돼 있다.
그걸 기준선으로 쓰면 중심이동의 매끄러움을 얻으면서 미래참조는 하지 않는다.
대가는 half봉만큼의 지연이다 — 공짜는 없고, 이게 정직한 가격표다.

`centered`는 함정을 실측해 보여주기 위한 옵션이다. 지표를 전체 구간에 미리
계산해두고 되돌아보면 승률 100%가 나오지만(`scripts/lookahead_demo.py` 참고),
실제 봇 경로에서는 현재 봉 값이 None이라 **진입 신호가 0건**이다.
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
    retired = (
        "5년 실제 데이터에서 1,759거래 -77.56% (무작위 대조군 -33.12%보다 나쁨). 방향을 나눠도 양쪽 다 음수라 살릴 방법이 없다 (롱 -0.327%/회, 숏 -0.239%/회, 수수료 후)"
    )

    def __init__(
        self,
        period: int = 20,
        band_atr_mult: float = 1.5,
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,
        atr_target_mult: float = 3.0,
        mode: str = "causal",           # causal | delayed | centered
        allow_short: bool = True,
        analysis_window: int = 500,
    ) -> None:
        super().__init__(
            period=period, band_atr_mult=band_atr_mult, atr_period=atr_period,
            atr_stop_mult=atr_stop_mult, atr_target_mult=atr_target_mult,
            mode=mode, allow_short=allow_short,
            analysis_window=analysis_window,
        )
        if mode not in ("causal", "delayed", "centered"):
            raise ValueError(f"알 수 없는 mode: {mode} (causal/delayed/centered)")
        self.period = period
        self.band_atr_mult = band_atr_mult
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.mode = mode
        self.allow_short = allow_short
        self.analysis_window = analysis_window
        if mode == "centered":
            log.warning(
                "tma_band(mode='centered')는 현재 봉 값이 존재하지 않아 "
                "실제 봇 경로에서는 신호가 나오지 않습니다. "
                "미래참조 함정을 보여주기 위한 옵션입니다 — 'delayed'를 쓰세요."
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
        if self.mode == "causal":
            mid = tma(closes, self.period)[-1]
        else:
            line = tma_centered(closes, self.period)
            if self.mode == "delayed":
                # 중심이동은 half봉 뒤 미래가 있어야 계산된다.
                # 현재 봉 값은 실시간에 존재하지 않으므로, 계산이 끝난
                # 가장 최근 지점(half봉 전)을 기준선으로 쓴다. 인과적이다.
                offset = self.period // 2
                mid = line[-(offset + 1)] if len(line) > offset else None
            else:                                   # centered
                mid = line[-1]                      # 실시간에는 항상 None이다
        if mid is None:
            return Signal(Action.HOLD, "지표 미계산 (mode=centered는 현재 봉 값이 없습니다)")

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
