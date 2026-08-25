"""Bitget USDT 무기한선물 클라이언트 (ccxt 기반).

ccxt는 live 모드에서만 필요하므로 모듈 최상단이 아니라 __init__ 안에서 임포트한다.
(백테스트/페이퍼만 쓰는 사람이 ccxt를 설치할 필요가 없도록)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..models import Account, Candle, Position, Side
from .base import Exchange, OrderError

log = logging.getLogger(__name__)

_RETRYABLE = ("NetworkError", "RequestTimeout", "ExchangeNotAvailable", "DDoSProtection")


class BitgetExchange(Exchange):
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_password: str,
        *,
        sandbox: bool = False,
        default_type: str = "swap",
    ) -> None:
        try:
            import ccxt  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ImportError("live/실시세 모드에는 ccxt가 필요합니다: pip install ccxt") from exc

        self._ccxt = ccxt
        self.client = ccxt.bitget(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "password": api_password,
                "enableRateLimit": True,
                "options": {"defaultType": default_type},
            }
        )
        if sandbox:
            self.client.set_sandbox_mode(True)
        self._markets_loaded = False

    @property
    def name(self) -> str:
        return "bitget"

    # --- 공통 재시도 래퍼 ------------------------------------------------
    def _call(self, fn, *args, retries: int = 3, **kwargs) -> Any:
        """일시적 네트워크 오류만 재시도한다.

        주문 거절(InsufficientFunds 등)은 재시도해봐야 같은 결과이고,
        중복 주문 위험만 커지므로 즉시 올린다.
        """
        delay = 1.0
        for attempt in range(retries):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                kind = type(exc).__name__
                if kind not in _RETRYABLE or attempt == retries - 1:
                    raise OrderError(f"{fn.__name__} 실패: {kind}: {exc}") from exc
                log.warning("%s 일시 오류(%s), %.0fs 후 재시도", fn.__name__, kind, delay)
                time.sleep(delay)
                delay *= 2
        raise OrderError(f"{fn.__name__}: 재시도 소진")

    def _ensure_markets(self) -> None:
        if not self._markets_loaded:
            self._call(self.client.load_markets)
            self._markets_loaded = True

    # --- 조회 -----------------------------------------------------------
    def fetch_candles(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        self._ensure_markets()
        rows = self._call(self.client.fetch_ohlcv, symbol, timeframe, None, limit)
        candles = [Candle.from_ccxt(r) for r in rows]
        # 마지막 캔들은 아직 진행 중이라 값이 계속 바뀐다.
        # 미완성 캔들로 신호를 내면 같은 봉에서 신호가 켜졌다 꺼지며 뇌동매매가 된다.
        return candles[:-1] if len(candles) > 1 else candles

    def fetch_price(self, symbol: str) -> float:
        self._ensure_markets()
        ticker = self._call(self.client.fetch_ticker, symbol)
        return float(ticker["last"])

    def fetch_account(self) -> Account:
        self._ensure_markets()
        bal = self._call(self.client.fetch_balance)
        usdt = bal.get("USDT", {})
        pos = self.fetch_position(symbol="")
        return Account(
            equity=float(usdt.get("total") or 0.0),
            available=float(usdt.get("free") or 0.0),
            positions=[pos] if pos else [],
        )

    def fetch_position(self, symbol: str) -> Position | None:
        self._ensure_markets()
        try:
            raw = self._call(self.client.fetch_positions, [symbol] if symbol else None)
        except OrderError:
            log.exception("포지션 조회 실패")
            raise
        for p in raw or []:
            contracts = float(p.get("contracts") or 0)
            if contracts == 0:
                continue
            return Position(
                symbol=p.get("symbol", symbol),
                side=Side.LONG if p.get("side") == "long" else Side.SHORT,
                size=contracts,
                entry_price=float(p.get("entryPrice") or 0),
                leverage=float(p.get("leverage") or 1),
                opened_at=int(p.get("timestamp") or 0),
            )
        return None

    def set_leverage(self, symbol: str, leverage: float, margin_mode: str) -> None:
        self._ensure_markets()
        try:
            self.client.set_margin_mode(margin_mode, symbol)
        except Exception as exc:  # noqa: BLE001
            # 이미 같은 모드면 거래소가 에러를 던진다. 치명적이지 않다.
            log.debug("set_margin_mode 무시된 오류: %s", exc)
        self._call(self.client.set_leverage, int(leverage), symbol)

    # --- 주문 -----------------------------------------------------------
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
        self._ensure_markets()
        if size <= 0:
            raise OrderError(f"잘못된 주문 수량: {size}")

        params: dict[str, Any] = {}
        if client_id:
            # 같은 clientOid로는 거래소가 두 번 체결하지 않는다.
            # 네트워크 타임아웃 후 재시도할 때 중복 진입을 막는 안전장치.
            params["clientOid"] = client_id
        # 손절은 주문과 함께 거래소에 등록한다. 봇이 죽어도 남아 있어야 한다.
        if stop_loss is not None:
            params["stopLossPrice"] = stop_loss
        if take_profit is not None:
            params["takeProfitPrice"] = take_profit

        order = self._call(
            self.client.create_order,
            symbol,
            "market",
            "buy" if side is Side.LONG else "sell",
            size,
            None,
            params,
            retries=1,   # 주문은 재시도하지 않는다. 아래에서 실제 체결을 확인한다.
        )
        log.info("진입 주문 전송: %s %s %s (id=%s)", symbol, side.value, size, order.get("id"))

        pos = self.fetch_position(symbol)
        if pos is None:
            raise OrderError("주문은 나갔으나 포지션이 확인되지 않습니다. 수동 확인이 필요합니다.")
        pos.stop_loss = stop_loss
        pos.take_profit = take_profit
        pos.client_id = client_id
        return pos

    def close_position(self, symbol: str, *, reason: str = "") -> float:
        self._ensure_markets()
        pos = self.fetch_position(symbol)
        if pos is None:
            raise OrderError("청산할 포지션이 없습니다.")
        self._call(
            self.client.create_order,
            symbol,
            "market",
            "sell" if pos.side is Side.LONG else "buy",
            pos.size,
            None,
            {"reduceOnly": True},
            retries=1,
        )
        self.cancel_all(symbol)   # 남은 스탑 주문 정리
        price = self.fetch_price(symbol)
        log.info("청산 완료: %s @ %s (%s)", symbol, price, reason)
        return price

    def cancel_all(self, symbol: str) -> None:
        try:
            self._call(self.client.cancel_all_orders, symbol)
        except OrderError as exc:
            log.warning("미체결 주문 정리 실패(무시): %s", exc)
