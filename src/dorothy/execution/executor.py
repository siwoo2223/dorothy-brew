"""주문 실행기.

전략의 Signal → 리스크 검증 → 거래소 주문 → 기록/알림을 잇는다.
여기서 지키는 것:
- 진입은 반드시 RiskManager 승인을 거친다.
- 같은 캔들에서 두 번 진입하지 않는다 (client_id로 멱등성 확보).
- 청산은 실패해도 재시도한다. 나가지 못하는 것이 가장 위험하다.
"""

from __future__ import annotations

import logging

from ..exchange.base import Exchange, OrderError
from ..models import Position, Signal, Trade
from ..risk.manager import RiskManager
from .sizing import normalize

log = logging.getLogger(__name__)


class Executor:
    def __init__(
        self,
        exchange: Exchange,
        risk: RiskManager,
        *,
        symbol: str,
        leverage: float = 1.0,
        min_size: float = 0.0,
        size_step: float = 0.0,
    ) -> None:
        self.exchange = exchange
        self.risk = risk
        self.symbol = symbol
        self.leverage = leverage
        self.min_size = min_size
        self.size_step = size_step
        self._last_entry_candle_ts: int = 0

    def handle(
        self,
        signal: Signal,
        *,
        position: Position | None,
        equity: float,
        candle_ts: int,
    ) -> Position | None:
        """신호를 처리하고 처리 후의 포지션 상태를 반환한다."""
        if signal.is_entry and position is None:
            return self._enter(signal, equity=equity, candle_ts=candle_ts)
        if signal.action.value == "exit" and position is not None:
            self._exit(position, reason=signal.reason)
            return None
        return position

    # --- 진입 -----------------------------------------------------------
    def _enter(self, signal: Signal, *, equity: float, candle_ts: int) -> Position | None:
        if candle_ts and candle_ts == self._last_entry_candle_ts:
            log.debug("같은 캔들에서 중복 진입 시도 차단 (ts=%s)", candle_ts)
            return None

        side = signal.side
        assert side is not None
        price = self.exchange.fetch_price(self.symbol)

        decision = self.risk.evaluate_entry(
            equity=equity,
            price=price,
            side=side,
            stop_loss=signal.stop_loss,
            leverage=self.leverage,
            min_size=self.min_size,
        )
        if not decision:
            log.info("진입 거부: %s", decision.reason)
            return None

        # 거래소 단위로 내림한다. 내림 후 최소 수량에 못 미치면 주문하지 않는다.
        # 최소 수량까지 억지로 올리면 감수하기로 한 리스크를 넘게 된다.
        size = normalize(decision.size, min_size=self.min_size, step=self.size_step)
        if size <= 0:
            self.risk.release()
            log.info(
                "진입 거부: 계산 수량 %.8f이 거래소 최소(%.8f)에 미달합니다",
                decision.size, self.min_size,
            )
            return None

        client_id = f"db-{self.symbol.split('/')[0]}-{candle_ts}"
        try:
            pos = self.exchange.open_position(
                self.symbol,
                side,
                size,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                client_id=client_id,
            )
        except OrderError:
            self.risk.release()   # 예약해둔 포지션 슬롯 반납
            log.exception("진입 주문 실패")
            return None

        # 손절 없이 들고 있는 것은 이 모듈의 첫 원칙(손절 없는 진입은 거부)에
        # 어긋난다. 확인 결과 '없음'이면 경고로 끝내지 않고 되돌린다.
        # (None = 확인 불가는 정리하지 않는다 — 없다는 증거가 아니다.)
        if getattr(pos, "stop_verified", None) is False:
            log.error("손절 미등록 확인 — 방금 낸 포지션을 즉시 정리합니다")
            self._exit(pos, reason="손절 미등록")
            self.risk.release()
            return None

        self._last_entry_candle_ts = candle_ts
        log.info(
            "진입 %s %s @ %.4f size=%.6f SL=%s TP=%s | %s",
            self.symbol, side.value, pos.entry_price, pos.size,
            signal.stop_loss, signal.take_profit, signal.reason,
        )
        return pos

    # --- 청산 -----------------------------------------------------------
    def _exit(self, position: Position, *, reason: str) -> float | None:
        """청산은 포기하지 않는다. 실패하면 로그를 남기고 다음 루프에서 다시 시도한다."""
        try:
            price = self.exchange.close_position(self.symbol, reason=reason)
        except OrderError:
            log.exception("청산 실패 — 다음 루프에서 재시도합니다. 수동 확인 권장")
            return None
        log.info("청산 %s @ %.4f (%s)", self.symbol, price, reason)
        return price

    def force_close(self, reason: str = "manual") -> None:
        """킬스위치/종료 시 전량 정리."""
        pos = self.exchange.fetch_position(self.symbol)
        if pos is None:
            log.info("정리할 포지션 없음")
            return
        self._exit(pos, reason=reason)
        self.exchange.cancel_all(self.symbol)
