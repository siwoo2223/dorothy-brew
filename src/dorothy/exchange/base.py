"""거래소 추상화.

전략·리스크·실행 코드가 특정 거래소에 묶이지 않게 한다.
같은 인터페이스로 PaperExchange(모의)와 BitgetExchange(실전)를 바꿔 끼운다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Account, Candle, Position, Side


class OrderError(RuntimeError):
    """주문이 거절되었거나 실패했다."""


class Exchange(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def fetch_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        """최신순이 아니라 과거→현재 순으로 정렬된 캔들을 돌려준다."""

    @abstractmethod
    def fetch_price(self, symbol: str) -> float: ...

    @abstractmethod
    def fetch_account(self) -> Account: ...

    @abstractmethod
    def fetch_position(self, symbol: str) -> Position | None: ...

    @abstractmethod
    def set_leverage(self, symbol: str, leverage: float, margin_mode: str) -> None: ...

    @abstractmethod
    def open_position(
        self,
        symbol: str,
        side: Side,
        size: float,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        client_id: str = "",
    ) -> Position:
        """시장가 진입. stop_loss는 가능하면 거래소에 함께 등록한다.

        봇이 죽어도 손절이 살아 있어야 하므로, 손절을 봇 메모리에만 두면 안 된다.
        """

    @abstractmethod
    def close_position(self, symbol: str, *, reason: str = "") -> float:
        """포지션 전량 청산. 체결 가격을 반환한다."""

    @abstractmethod
    def cancel_all(self, symbol: str) -> None:
        """미체결 주문(스탑 포함) 정리. 청산 후 잔여 주문이 남는 사고를 막는다."""
