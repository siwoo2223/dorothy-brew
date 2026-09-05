"""무작위 진입 — 대조군(control group).

**이 전략이 이 저장소에서 가장 중요한 코드일 수 있다.**

대부분의 백테스트는 대조군이 없다. "내 전략이 +15% 났다"는 문장은
그 자체로는 아무것도 증명하지 않는다. 비교 대상이 없기 때문이다.
같은 손절·목표·포지션 사이징으로 **동전을 던져 진입**해도 +15%가 났다면,
그 분석 방법은 아무 기여도 하지 않은 것이다.

실제로 ATR 손절/목표 + 리스크 관리 조합만으로도 백테스트 성과는 꽤 나온다.
손익비를 3:1로 잡고 손실을 자르는 규칙 자체에 통계적 성질이 있기 때문이다.
그 성질을 '내 분석의 성과'로 착각하는 것이 가장 흔한 자기기만이다.

그래서 비교 실험(`dorothy compare`)에는 항상 이 대조군이 들어간다.
**본인 전략이 이걸 유의미하게 못 이기면, 그 분석은 값을 못 하는 것이다.**

무작위지만 **결정론적**이다. 캔들 시각과 시드에서 난수를 만들기 때문에
같은 데이터·같은 시드면 항상 같은 결과가 나온다. 재현 불가능한 대조군은
대조군이 아니다.
"""

from __future__ import annotations

import hashlib

from ..models import Action, Candle, Position, Signal
from .base import Strategy, register
from .common import atr_at, entry_signal


def _unit_random(seed: int, ts: int, salt: str = "") -> float:
    """(seed, ts)에서 0~1 난수를 만든다. 순수 함수라 언제 호출해도 같은 값이다."""
    digest = hashlib.sha256(f"{seed}:{ts}:{salt}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


@register
class RandomEntryStrategy(Strategy):
    name = "random"

    def __init__(
        self,
        entry_probability: float = 0.02,   # 봉당 진입 확률
        seed: int = 1234,
        hold_bars: int = 0,                # >0이면 N봉 후 시간 청산
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,
        atr_target_mult: float = 3.0,
        allow_short: bool = True,
    ) -> None:
        super().__init__(
            entry_probability=entry_probability, seed=seed, hold_bars=hold_bars,
            atr_period=atr_period, atr_stop_mult=atr_stop_mult,
            atr_target_mult=atr_target_mult, allow_short=allow_short,
        )
        if not 0 < entry_probability <= 1:
            raise ValueError("entry_probability는 0 초과 1 이하여야 합니다.")
        self.entry_probability = entry_probability
        self.seed = seed
        self.hold_bars = hold_bars
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.allow_short = allow_short

    @property
    def warmup(self) -> int:
        return self.atr_period + 3

    def generate(self, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return Signal(Action.HOLD, "워밍업 부족")

        atr = atr_at(candles, self.atr_period)
        if atr is None:
            return Signal(Action.HOLD, "ATR 미계산")

        candle = candles[-1]

        if position is not None:
            if self.hold_bars > 0:
                elapsed = sum(1 for c in candles if c.ts > position.opened_at)
                if elapsed >= self.hold_bars:
                    return Signal(Action.EXIT, f"{self.hold_bars}봉 경과")
            return Signal(Action.HOLD, "포지션 유지")

        if _unit_random(self.seed, candle.ts, "entry") >= self.entry_probability:
            return Signal(Action.HOLD, "무작위 미발동")

        long = _unit_random(self.seed, candle.ts, "side") < 0.5
        if not long and not self.allow_short:
            long = True

        return entry_signal(
            long=long, price=candle.close, atr=atr,
            stop_mult=self.atr_stop_mult, target_mult=self.atr_target_mult,
            reason="무작위 진입 (대조군)",
        )
