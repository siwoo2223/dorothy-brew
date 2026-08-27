"""멀티 타임프레임 필터 — 상위 봉으로 방향을 정하고 하위 봉에서 진입한다.

특정 전략이 아니라 **어떤 전략에도 씌울 수 있는 껍데기**로 만들었다.
전략마다 상위 타임프레임 로직을 다시 짜면 그만큼 버그와 파라미터가 늘어난다.

동작:
- 상위 타임프레임(기본 4시간)에서 종가가 EMA 위면 상승, 아래면 하락으로 본다
- 상승 국면에서는 롱만, 하락 국면에서는 숏만 받는다
- 방향이 안 맞는 진입 신호는 버린다 (청산 신호는 그대로 통과시킨다)

**청산을 막지 않는 것이 중요하다.** 필터는 들어가는 문만 좁히고
나가는 문은 건드리지 않는다. 상위 추세가 바뀌었다고 이미 잡은 포지션의
청산 신호를 무시하면 손실이 방치된다.

인과성: 상위 봉은 `resample(drop_incomplete=True)`로 만들어 **완성된 봉만** 쓴다.
진행 중인 4시간봉의 종가는 실시간에 알 수 없기 때문이다.
"""

from __future__ import annotations

from ..data.indicators import ema
from ..data.resample import infer_interval, resample, timeframe_ms
from ..models import Action, Candle, Position, Signal
from .base import Strategy, get_strategy, register


@register
class MtfFilterStrategy(Strategy):
    name = "mtf_filter"

    def __init__(
        self,
        base: str = "donchian",
        base_params: dict | None = None,
        higher_timeframe: str = "4h",
        filter_period: int = 50,
        require_trend: bool = True,
    ) -> None:
        super().__init__(
            base=base, base_params=base_params, higher_timeframe=higher_timeframe,
            filter_period=filter_period, require_trend=require_trend,
        )
        if base == "mtf_filter":
            raise ValueError("mtf_filter를 자기 자신에 씌울 수 없습니다.")
        self.base_name = base
        self.base_params = dict(base_params or {})
        self.base = get_strategy(base, **self.base_params)
        self.higher_timeframe = higher_timeframe
        self.higher_ms = timeframe_ms(higher_timeframe)
        self.filter_period = filter_period
        self.require_trend = require_trend

    @property
    def warmup(self) -> int:
        # 상위 봉 filter_period개를 만들려면 하위 봉이 그만큼 더 필요하다.
        # 배수를 모르는 시점이라 4배를 가정하고 넉넉히 잡는다.
        return max(self.base.warmup, self.filter_period * 4 + 10)

    def higher_bias(self, candles: list[Candle]) -> int:
        """+1 상승 / -1 하락 / 0 판정 불가."""
        source_ms = infer_interval(candles)
        if source_ms <= 0 or self.higher_ms < source_ms:
            return 0

        higher = resample(candles, self.higher_ms)
        if len(higher) < self.filter_period + 1:
            return 0

        closes = [c.close for c in higher]
        line = ema(closes, self.filter_period)
        reference = line[-1]
        if reference is None:
            return 0
        return 1 if closes[-1] > reference else -1

    def generate(self, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return Signal(Action.HOLD, "워밍업 부족")

        signal = self.base.generate(candles, position)

        # 청산과 관망은 그대로 통과시킨다. 나가는 문은 좁히지 않는다.
        if not signal.is_entry:
            return signal

        bias = self.higher_bias(candles)
        if bias == 0:
            if self.require_trend:
                return Signal(Action.HOLD, f"{self.higher_timeframe} 추세 판정 불가")
            return signal

        wanted = 1 if signal.action is Action.ENTER_LONG else -1
        if wanted != bias:
            direction = "상승" if bias == 1 else "하락"
            return Signal(
                Action.HOLD,
                f"{self.higher_timeframe} {direction} 국면과 불일치 — 진입 보류",
            )

        return Signal(
            signal.action,
            f"{signal.reason} [{self.higher_timeframe} {'상승' if bias == 1 else '하락'} 일치]",
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            meta={**signal.meta, "htf_bias": bias},
        )
