"""Bitget USDT 무기한선물 클라이언트 (ccxt 기반).

ccxt는 live 모드에서만 필요하므로 모듈 최상단이 아니라 __init__ 안에서 임포트한다.
(백테스트/페이퍼만 쓰는 사람이 ccxt를 설치할 필요가 없도록)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..models import Account, Candle, Position, Side, Trade
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

        # poll_closed_trades 상태: 마지막으로 본 포지션과 그때의 '포지션 없는 자본'
        self._watched: Position | None = None
        self._equity_before: float = 0.0

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

    def market_limits(self, symbol: str) -> tuple[float, float]:
        """거래소가 알려주는 (최소 주문 수량, 수량 단위).

        설정 파일에 적어둔 값은 추측이고 거래소 정책은 바뀐다.
        실전에서는 반드시 이쪽을 써야 한다.
        """
        self._ensure_markets()
        market = self.client.market(symbol)
        limits = (market.get("limits") or {}).get("amount") or {}
        min_size = float(limits.get("min") or 0.0)

        precision = (market.get("precision") or {}).get("amount")
        if precision is None:
            step = min_size
        elif isinstance(precision, int) or float(precision).is_integer() and precision >= 1:
            # ccxt는 거래소에 따라 소수 자릿수(4) 또는 단위(0.0001)로 준다
            step = 10 ** -int(precision)
        else:
            step = float(precision)
        return min_size, (step or min_size)

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

    # --- 청산 감지 ------------------------------------------------------
    def poll_closed_trades(self, symbol: str) -> list[Trade]:
        """포지션이 사라졌는지 확인하고, 사라졌으면 매매 기록을 만든다.

        거래소 스탑으로 청산되면 봇은 주문을 낸 적이 없어 그 사실을 모른다.
        그래서 매 틱 포지션 유무를 비교하는 방식으로 청산을 감지한다.

        **손익은 체결 내역이 아니라 자본 변화에서 뽑는다.** 체결 파싱은
        부분체결·수수료·펀딩비 때문에 틀리기 쉽고 거래소마다 형식이 다르다.
        자본은 숫자 하나뿐이라 항상 맞다. 안전장치가 취약한 쪽에 의존하면 안 된다.
        """
        current = self.fetch_position(symbol)
        equity = self._equity()

        # 포지션을 처음 관측했다 (신규 진입이든, 봇 재시작 후든)
        if current is not None and self._watched is None:
            price = self.fetch_price(symbol)
            # 미실현 손익을 빼면 '진입 시점의 자본'이 나온다.
            # 재시작 직후에 관측해도 값이 맞는다는 게 이 방식의 장점이다.
            self._equity_before = equity - current.unrealized_pnl(price)
            self._watched = current
            log.info("포지션 관측 시작: %s %s (기준자본 %.2f)",
                     current.side.value, current.size, self._equity_before)
            return []

        if current is None and self._watched is not None:
            closed = self._build_trade(symbol, self._watched, equity)
            self._watched = None
            return [closed]

        # 방향이나 수량이 바뀌었다 = 청산 후 재진입으로 본다 (보수적)
        if (
            current is not None
            and self._watched is not None
            and (current.side is not self._watched.side
                 or abs(current.size - self._watched.size) > 1e-9)
        ):
            price = self.fetch_price(symbol)
            closed = self._build_trade(symbol, self._watched, equity - current.unrealized_pnl(price))
            self._equity_before = equity - current.unrealized_pnl(price)
            self._watched = current
            return [closed]

        return []

    def _build_trade(self, symbol: str, position: Position, equity_now: float) -> Trade:
        pnl = equity_now - self._equity_before
        exit_price = self._exit_price(symbol, position)
        log.info("청산 감지: %s %s @ %.4f · 손익 %+.2f",
                 symbol, position.side.value, exit_price, pnl)
        return Trade(
            symbol=symbol,
            side=position.side,
            size=position.size,
            entry_price=position.entry_price,
            exit_price=exit_price,
            opened_at=position.opened_at,
            closed_at=int(time.time() * 1000),
            # 수수료·펀딩비는 이미 자본 변화에 반영돼 있다.
            # 여기서 또 빼면 이중 계산이 되므로 0으로 둔다.
            fee=0.0,
            funding=0.0,
            realized_pnl=pnl,   # ← 가격 역산이 아니라 이 값이 쓰인다
            reason="거래소 청산 (스탑/익절/수동)",
        )

    def _exit_price(self, symbol: str, position: Position) -> float:
        """청산 가격. 기록용이며 손익 계산에는 쓰지 않는다.

        체결 내역에서 못 가져오면 현재가로 대체한다 — 손익은 자본에서
        이미 정확히 나왔으므로 이 값이 조금 어긋나도 안전장치는 멀쩡하다.
        """
        try:
            fills = self._call(
                self.client.fetch_my_trades, symbol, position.opened_at or None, 20, retries=1
            )
            if fills:
                last = fills[-1]
                price = last.get("price")
                if price:
                    return float(price)
        except Exception as exc:  # noqa: BLE001
            log.debug("체결 내역 조회 실패, 현재가로 대체: %s", exc)
        try:
            return self.fetch_price(symbol)
        except OrderError:
            return position.entry_price

    def _equity(self) -> float:
        bal = self._call(self.client.fetch_balance)
        return float((bal.get("USDT") or {}).get("total") or 0.0)
