"""국면 필터 — 내 전략에 맞지 않는 시장에서는 쉰다.

`mtf_filter`와 같은 껍데기 방식이다. 어떤 전략에도 씌울 수 있다.

전략을 바꾸는 것보다 **안 맞는 국면에서 쉬는 것**이 먼저다.
추세추종을 횡보장에서 돌리면 계속 털리는데, 그건 전략이 나빠서가 아니라
시장이 그 전략의 전제를 만족하지 않아서다.

`regime_report`로 어떤 국면에서 잃는지 먼저 확인하고,
그 국면을 여기서 제외하는 순서로 쓴다. 근거 없이 국면을 빼면
그냥 파라미터 하나가 더 늘어날 뿐이다.
"""

from __future__ import annotations

from ..analysis.regime import Trendiness, Volatility, classify
from ..models import Action, Candle, Position, Signal
from .base import Strategy, get_strategy, register

_TRENDINESS = {t.value: t for t in Trendiness}
_VOLATILITY = {v.value: v for v in Volatility}


@register
class RegimeFilterStrategy(Strategy):
    name = "regime_filter"

    def __init__(
        self,
        base: str = "donchian",
        base_params: dict | None = None,
        allow_trendiness: list[str] | None = None,   # None이면 전부 허용
        block_volatility: list[str] | None = None,   # 예: ["low"]
        window: int = 200,
    ) -> None:
        super().__init__(
            base=base, base_params=base_params, allow_trendiness=allow_trendiness,
            block_volatility=block_volatility, window=window,
        )
        if base == "regime_filter":
            raise ValueError("regime_filter를 자기 자신에 씌울 수 없습니다.")

        self.base_name = base
        self.base_params = dict(base_params or {})
        self.base = get_strategy(base, **self.base_params)
        self.window = window

        self.allow_trendiness = (
            None if allow_trendiness is None
            else {_require(_TRENDINESS, v, "trendiness") for v in allow_trendiness}
        )
        self.block_volatility = {
            _require(_VOLATILITY, v, "volatility") for v in (block_volatility or [])
        }

    @property
    def warmup(self) -> int:
        return max(self.base.warmup, 60)

    def generate(self, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return Signal(Action.HOLD, "워밍업 부족")

        signal = self.base.generate(candles, position)

        # 청산은 막지 않는다. 국면이 나빠도 나가는 문은 열려 있어야 한다.
        if not signal.is_entry:
            return signal

        regime = classify(candles, window=self.window)

        if self.allow_trendiness is not None and regime.trendiness not in self.allow_trendiness:
            return Signal(Action.HOLD, f"{regime.label} 국면 — 진입 보류")
        if regime.volatility in self.block_volatility:
            return Signal(Action.HOLD, f"{regime.label} 국면 — 변동성 제외 구간")

        return Signal(
            signal.action,
            f"{signal.reason} [{regime.label}]",
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            meta={**signal.meta, "regime": regime.label, "variance_ratio": regime.variance_ratio},
        )


def _require(table: dict, value: str, kind: str):
    if value not in table:
        raise ValueError(f"알 수 없는 {kind}: {value} (가능: {sorted(table)})")
    return table[value]
