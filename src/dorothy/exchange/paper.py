"""페이퍼(모의) 거래소.

실제 주문을 내지 않고 시세만 실거래소에서 받아온다.
- feed 가 주어지면: 실시간 시세 + 가상 체결 (paper 모드)
- candles 가 주어지면: 과거 데이터 재생 (backtest 모드)

체결 가정: 시장가 즉시 체결 + 슬리피지 + 테이커 수수료.
낙관적으로 잡으면 백테스트 결과가 통째로 거짓말이 되므로 항상 불리한 쪽으로 민다.
"""

from __future__ import annotations

import time

from ..models import Account, Candle, Position, Side, Trade
from .base import Exchange, OrderError


class PaperExchange(Exchange):
    def __init__(
        self,
        *,
        equity: float = 1000.0,
        taker_fee: float = 0.0006,
        slippage: float = 0.0005,
        source: Exchange | None = None,
    ) -> None:
        self.equity = equity
        self.initial_equity = equity
        self.taker_fee = taker_fee
        self.slippage = slippage
        self.source = source          # 실시세를 빌려올 거래소 (없으면 수동 주입)
        self._position: Position | None = None
        self._price: float = 0.0
        self._candles: list[Candle] = []
        self.trades: list[Trade] = []
        self._reported = 0        # poll_closed_trades 커서
        self.equity_curve: list[tuple[int, float]] = []
        self._now_ms: int = 0

    # --- 백테스트용 시간/가격 주입 -------------------------------------
    def feed_candle(self, candle: Candle) -> None:
        """백테스트 엔진이 캔들을 한 개씩 밀어 넣는다."""
        self._candles.append(candle)
        self._price = candle.close
        self._now_ms = candle.ts
        self._check_stops(candle)
        self.equity_curve.append((candle.ts, self.total_equity()))

    def _now(self) -> int:
        return self._now_ms or int(time.time() * 1000)

    # --- Exchange 구현 -------------------------------------------------
    @property
    def name(self) -> str:
        return "paper"

    def fetch_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        if self.source is not None:
            candles = self.source.fetch_candles(symbol, timeframe, limit)
            if candles:
                # 실시세를 쓸 때도 스탑 판정은 돌아야 한다.
                # 이걸 빼먹으면 온라인 페이퍼 모드에서 손절이 영원히 체결되지 않는다.
                self._price = candles[-1].close
                self._now_ms = candles[-1].ts
                self._check_stops(candles[-1])
                self.equity_curve.append((candles[-1].ts, self.total_equity()))
            return candles
        return self._candles[-limit:]

    def fetch_price(self, symbol: str) -> float:
        if self.source is not None:
            self._price = self.source.fetch_price(symbol)
        return self._price

    def fetch_account(self) -> Account:
        return Account(
            equity=self.total_equity(),
            available=self.equity,
            positions=[self._position] if self._position else [],
        )

    def fetch_position(self, symbol: str) -> Position | None:
        return self._position

    def set_leverage(self, symbol: str, leverage: float, margin_mode: str) -> None:
        pass  # 모의 계좌에서는 기록만 하면 충분하다

    def total_equity(self) -> float:
        pnl = self._position.unrealized_pnl(self._price) if self._position else 0.0
        return self.equity + pnl

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
        if self._position is not None:
            raise OrderError("이미 포지션이 있습니다 (페이퍼는 심볼당 1개만 지원).")
        if size <= 0:
            raise OrderError(f"잘못된 주문 수량: {size}")

        fill = self._fill_price(side)
        self.equity -= fill * size * self.taker_fee
        self._position = Position(
            symbol=symbol,
            side=side,
            size=size,
            entry_price=fill,
            stop_loss=stop_loss,
            take_profit=take_profit,
            opened_at=self._now(),
            client_id=client_id,
        )
        return self._position

    def close_position(self, symbol: str, *, reason: str = "") -> float:
        pos = self._position
        if pos is None:
            raise OrderError("청산할 포지션이 없습니다.")
        fill = self._fill_price(pos.side.opposite)
        return self._settle(fill, reason)

    def cancel_all(self, symbol: str) -> None:
        pass

    def poll_closed_trades(self, symbol: str) -> list[Trade]:
        """_settle()이 쌓아둔 체결 중 아직 보고하지 않은 것."""
        fresh = self.trades[self._reported:]
        self._reported = len(self.trades)
        return list(fresh)

    # --- 내부 ----------------------------------------------------------
    def _fill_price(self, side: Side) -> float:
        """항상 불리한 방향으로 슬리피지를 적용한다.

        살 때(롱 진입/숏 청산)는 비싸게, 팔 때는 싸게 체결된다고 본다.
        """
        return self._price * (1 + self.slippage * side.sign)

    def _settle(self, exit_price: float, reason: str) -> float:
        pos = self._position
        assert pos is not None
        entry_fee = pos.entry_price * pos.size * self.taker_fee
        exit_fee = exit_price * pos.size * self.taker_fee
        gross = (exit_price - pos.entry_price) * pos.size * pos.side.sign
        self.equity += gross - exit_fee
        self.trades.append(
            Trade(
                symbol=pos.symbol,
                side=pos.side,
                size=pos.size,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                opened_at=pos.opened_at,
                closed_at=self._now(),
                fee=entry_fee + exit_fee,
                reason=reason,
            )
        )
        self._position = None
        return exit_price

    def _check_stops(self, candle: Candle) -> None:
        """캔들 고저가로 손절/익절 도달을 판정한다.

        같은 캔들 안에서 둘 다 닿았으면 손절이 먼저 체결된 것으로 본다(보수적 가정).
        """
        pos = self._position
        if pos is None:
            return
        if pos.stop_loss is not None:
            hit = candle.low <= pos.stop_loss if pos.side is Side.LONG else candle.high >= pos.stop_loss
            if hit:
                self._settle(pos.stop_loss, "stop_loss")
                return
        if pos.take_profit is not None:
            hit = candle.high >= pos.take_profit if pos.side is Side.LONG else candle.low <= pos.take_profit
            if hit:
                self._settle(pos.take_profit, "take_profit")


class ReplayExchange(PaperExchange):
    """미리 받아둔 캔들을 한 개씩 흘려보내는 모의 거래소.

    네트워크 없이 실시간 루프(TradingEngine)를 그대로 돌려보기 위한 것.
    paper 모드 --offline 에서 쓰며, 통합 테스트에도 유용하다.
    """

    def __init__(self, candles: list[Candle], **kwargs) -> None:
        super().__init__(**kwargs)
        self._pending = list(candles)
        self._cursor = 0

    @property
    def name(self) -> str:
        return "replay"

    @property
    def exhausted(self) -> bool:
        return self._cursor >= len(self._pending)

    def fetch_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        if not self.exhausted:
            self.feed_candle(self._pending[self._cursor])
            self._cursor += 1
        return self._candles[-limit:]
