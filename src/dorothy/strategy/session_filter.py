"""세션 필터 — 정해진 시간대에만 매매한다.

`mtf_filter`, `regime_filter`와 같은 껍데기 방식이다.

**반드시 `session_report`로 먼저 확인하고 쓸 것.**
시간대는 구간이 많아서(시간 24개, 요일 7개) 근거 없이 고르면
거의 확실하게 과최적화된다. 리포트의 p값이 0.05 미만일 때만
그 시간대를 제한할 근거가 있다.
"""

from __future__ import annotations

from ..analysis.sessions import (
    Killzone,
    Session,
    is_weekend,
    killzone_of,
    parse_killzones,
    parse_sessions,
    session_of,
)
from ..models import Action, Candle, Position, Signal
from .base import Strategy, get_strategy, register


@register
class SessionFilterStrategy(Strategy):
    name = "session_filter"

    def __init__(
        self,
        base: str = "donchian",
        base_params: dict | None = None,
        sessions: list[str] | None = None,        # None이면 전부 허용
        killzones: list[str] | None = None,       # None이면 전부 허용
        skip_weekend: bool = False,
    ) -> None:
        super().__init__(
            base=base, base_params=base_params, sessions=sessions,
            killzones=killzones, skip_weekend=skip_weekend,
        )
        if base == "session_filter":
            raise ValueError("session_filter를 자기 자신에 씌울 수 없습니다.")

        self.base_name = base
        self.base_params = dict(base_params or {})
        self.base = get_strategy(base, **self.base_params)
        self.sessions: set[Session] | None = parse_sessions(sessions) if sessions else None
        self.killzones: set[Killzone] | None = parse_killzones(killzones) if killzones else None
        self.skip_weekend = skip_weekend

    @property
    def warmup(self) -> int:
        return self.base.warmup

    def generate(self, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return Signal(Action.HOLD, "워밍업 부족")

        signal = self.base.generate(candles, position)

        # 청산은 시간대와 무관하게 통과시킨다.
        # 장이 한산하다고 손실 포지션을 방치할 이유가 없다.
        if not signal.is_entry:
            return signal

        ts = candles[-1].ts

        if self.skip_weekend and is_weekend(ts):
            return Signal(Action.HOLD, "주말 — 진입 보류")

        session = session_of(ts)
        if self.sessions is not None and session not in self.sessions:
            return Signal(Action.HOLD, f"{session.korean} 세션 — 진입 보류")

        killzone = killzone_of(ts)
        if self.killzones is not None and killzone not in self.killzones:
            return Signal(Action.HOLD, f"{killzone.korean} — 진입 보류")

        return Signal(
            signal.action,
            f"{signal.reason} [{session.korean}]",
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            meta={**signal.meta, "session": session.value, "killzone": killzone.value},
        )
