"""전략 인터페이스.

계약:
- 전략은 캔들 리스트를 받아 Signal 하나를 반환한다.
- 수량, 레버리지, 자본은 절대 건드리지 않는다 (리스크 매니저 담당).
- 마지막 캔들은 '완결된 캔들'이라고 가정한다. 진행 중인 봉을 넣으면
  같은 봉에서 신호가 켜졌다 꺼지는 리페인팅이 생긴다.
"""

from __future__ import annotations

import inspect
import logging
from abc import ABC, abstractmethod

from ..models import Candle, Position, Signal

log = logging.getLogger(__name__)


class Strategy(ABC):
    name: str = "base"

    #: 실제 데이터에서 손실이 확인된 전략은 여기에 이유를 적는다.
    #: 코드를 지우지 않는 이유: 새 전략이 이것보다 나은지 재보려면 기준선이 필요하다.
    #: 대신 고를 때마다 경고가 뜨고, 기본 설정에서는 빠진다.
    retired: str = ""

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


def known_params(cls: type[Strategy]) -> set[str]:
    """전략이 받는 파라미터 이름들."""
    sig = inspect.signature(cls.__init__)
    return {n for n, p_ in sig.parameters.items() if n != "self" and p_.kind is not p_.VAR_KEYWORD}


def load_all() -> dict[str, type[Strategy]]:
    """strategy 패키지의 모든 모듈을 임포트해 레지스트리를 채운다.

    임포트 목록을 손으로 관리하면 파일을 추가하고 등록을 빠뜨린다
    (실제로 그랬다). 자동 탐색이면 @register만 붙이면 끝난다.
    """
    import importlib
    import pkgutil
    import sys

    package = __name__.rsplit(".", 1)[0]
    paths = list(getattr(sys.modules[package], "__path__", []))
    for info in pkgutil.iter_modules(paths):
        if info.name in ("base", "common"):
            continue
        importlib.import_module(f"{package}.{info.name}")
    return dict(_REGISTRY)


def available(include_retired: bool = True) -> list[str]:
    strategies = load_all()
    if include_retired:
        return sorted(strategies)
    return sorted(n for n, cls in strategies.items() if not cls.retired)


def retired() -> dict[str, str]:
    """폐기된 전략과 그 이유. 기준선으로만 쓴다."""
    return {n: cls.retired for n, cls in sorted(load_all().items()) if cls.retired}


def get_strategy(name: str, **params) -> Strategy:
    load_all()

    if name not in _REGISTRY:
        raise KeyError(f"알 수 없는 전략: {name} (사용 가능: {sorted(_REGISTRY)})")

    cls = _REGISTRY[name]
    if cls.retired:
        log.warning("전략 '%s'은(는) 폐기되었습니다 — %s", name, cls.retired)

    # 모르는 파라미터를 조용히 삼키면 설정 파일 오타가 영원히 드러나지 않는다.
    # (실제로 이것 때문에 제거 실험이 전 항목 동일한 결과를 냈다)
    unknown = set(params) - known_params(cls)
    if unknown:
        raise ValueError(
            f"전략 '{name}'이 모르는 파라미터: {sorted(unknown)}\n"
            f"  사용 가능: {sorted(known_params(cls))}"
        )
    return cls(**params)
