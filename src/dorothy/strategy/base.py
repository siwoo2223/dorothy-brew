"""전략 인터페이스.

계약:
- 전략은 캔들 리스트를 받아 Signal 하나를 반환한다.
- 수량, 레버리지, 자본은 절대 건드리지 않는다 (리스크 매니저 담당).
- 마지막 캔들은 '완결된 캔들'이라고 가정한다. 진행 중인 봉을 넣으면
  같은 봉에서 신호가 켜졌다 꺼지는 리페인팅이 생긴다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Candle, Position, Signal


class Strategy(ABC):
    name: str = "base"

    def __init__(self, **params) -> None:
        self.params = params

    @property
    @abstractmethod
    def warmup(self) -> int:
        """신호를 내기 위해 필요한 최소 캔들 개수."""

    @abstractmethod
    def generate(self, candles: list[Candle], position: Position | None) -> Signal:
        """position이 None이면 진입 판단, 있으면 청산 판단을 한다."""


_REGISTRY: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    _REGISTRY[cls.name] = cls
    return cls


def get_strategy(name: str, **params) -> Strategy:
    from . import ema_cross  # noqa: F401  등록을 위한 임포트

    if name not in _REGISTRY:
        raise KeyError(f"알 수 없는 전략: {name} (사용 가능: {sorted(_REGISTRY)})")
    return _REGISTRY[name](**params)
