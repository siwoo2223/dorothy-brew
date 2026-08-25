"""도메인 모델. 전 모듈이 공유하는 자료구조."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        """롱은 +1, 숏은 -1. 손익 계산에 사용."""
        return 1 if self is Side.LONG else -1

    @property
    def opposite(self) -> "Side":
        return Side.SHORT if self is Side.LONG else Side.LONG


class Action(str, Enum):
    HOLD = "hold"
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT = "exit"


@dataclass(frozen=True)
class Candle:
    ts: int          # 캔들 시작 시각 (epoch ms)
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_ccxt(cls, row: list) -> "Candle":
        ts, o, h, l, c, v = row[:6]
        return cls(int(ts), float(o), float(h), float(l), float(c), float(v))


@dataclass(frozen=True)
class Signal:
    """전략의 출력. 가격/수량이 아니라 '의도'만 담는다.

    수량은 리스크 매니저가, 실제 주문은 실행기가 결정한다.
    전략이 수량을 정하면 리스크 한도를 우회할 수 있으므로 의도적으로 분리했다.
    """

    action: Action
    reason: str = ""
    stop_loss: float | None = None    # 손절 가격 (진입 신호일 때 필수에 가깝다)
    take_profit: float | None = None

    @property
    def is_entry(self) -> bool:
        return self.action in (Action.ENTER_LONG, Action.ENTER_SHORT)

    @property
    def side(self) -> Side | None:
        if self.action is Action.ENTER_LONG:
            return Side.LONG
        if self.action is Action.ENTER_SHORT:
            return Side.SHORT
        return None


@dataclass
class Position:
    symbol: str
    side: Side
    size: float              # 계약 수량 (베이스 자산 기준)
    entry_price: float
    leverage: float = 1.0
    stop_loss: float | None = None
    take_profit: float | None = None
    opened_at: int = 0       # epoch ms
    client_id: str = ""

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.entry_price) * self.size * self.side.sign

    def notional(self, price: float) -> float:
        return abs(self.size) * price


@dataclass
class Trade:
    """청산까지 끝난 한 건의 매매. 저널/백테스트 지표의 단위."""

    symbol: str
    side: Side
    size: float
    entry_price: float
    exit_price: float
    opened_at: int
    closed_at: int
    fee: float = 0.0
    funding: float = 0.0
    reason: str = ""

    @property
    def gross_pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.size * self.side.sign

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.fee - self.funding

    @property
    def return_pct(self) -> float:
        cost = self.entry_price * self.size
        return self.net_pnl / cost * 100 if cost else 0.0


@dataclass
class Account:
    equity: float
    available: float
    currency: str = "USDT"
    positions: list[Position] = field(default_factory=list)
