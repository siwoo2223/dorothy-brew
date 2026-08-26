"""리스크 관리자.

이 프로젝트에서 가장 중요한 모듈이다. 전략은 틀려도 되지만
여기가 뚫리면 계좌가 사라진다. 모든 진입은 예외 없이 여기를 통과한다.

원칙:
1. 손절 없는 진입은 거부한다.
2. 수량은 "이번에 잃어도 되는 금액 ÷ 손절까지의 거리"로 역산한다.
3. 일일 손실 한도, 연속 손실, 킬스위치 중 하나라도 걸리면 신규 진입을 막는다.
   (청산은 언제나 허용한다 — 나가는 문은 절대 잠그지 않는다.)
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import RiskConfig
from ..models import Side, Trade

log = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    approved: bool
    size: float = 0.0
    reason: str = ""

    def __bool__(self) -> bool:
        return self.approved


@dataclass
class RiskState:
    """당일 누적 상태. 날짜가 바뀌면 리셋된다."""

    day: str = ""
    realized_pnl: float = 0.0
    consecutive_losses: int = 0
    day_start_equity: float = 0.0
    trades_today: int = 0
    halted_reason: str = ""
    open_positions: int = 0
    _history: list[Trade] = field(default_factory=list)


class RiskManager:
    def __init__(
        self,
        config: RiskConfig,
        *,
        kill_switch_file: str = "KILL",
        clock: Callable[[], int] | None = None,
    ) -> None:
        self.cfg = config
        self.kill_switch = Path(kill_switch_file)
        self.state = RiskState()
        # 백테스트는 캔들 시각을, 실전은 실제 시각을 쓴다.
        # 이걸 주입 가능하게 두지 않으면 백테스트 전 구간이 '하루'로 묶여
        # 일일 손실 한도가 한 번 걸린 뒤 영영 풀리지 않는다.
        self.clock: Callable[[], int] = clock or (lambda: int(time.time() * 1000))

    # --- 상태 관리 -------------------------------------------------------
    def _today(self) -> str:
        return datetime.fromtimestamp(self.clock() / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    def roll_day(self, equity: float) -> None:
        """날짜가 바뀌면 일일 카운터를 초기화한다."""
        today = self._today()
        if self.state.day != today:
            if self.state.day:
                log.info(
                    "일자 전환 %s → %s (당일 실현손익 %.2f, %d거래)",
                    self.state.day, today, self.state.realized_pnl, self.state.trades_today,
                )
            self.state.day = today
            self.state.realized_pnl = 0.0
            self.state.trades_today = 0
            self.state.day_start_equity = equity
            self.state.halted_reason = ""
            # 연속 손실 차단은 '영구 정지'가 아니라 하루 쿨다운이다.
            # 영구 정지로 두면 봇이 조용히 죽은 채로 방치되고,
            # 백테스트에서는 첫 연속 손실 이후 구간이 통째로 사라진다.
            self.state.consecutive_losses = 0

    def record_trade(self, trade: Trade) -> None:
        self.state.realized_pnl += trade.net_pnl
        self.state.trades_today += 1
        self.state._history.append(trade)
        if trade.net_pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0
        # open_positions는 sync_open_positions()가 실제 상태로 맞춘다.
        # 여기서도 줄이면 이중 감소가 된다.
        self.state.open_positions = max(0, self.state.open_positions - 1)

    # --- 차단 조건 -------------------------------------------------------
    def halt_reason(self, equity: float) -> str:
        """신규 진입을 막아야 하는 이유. 없으면 빈 문자열."""
        if self.kill_switch.exists():
            return f"킬스위치 파일({self.kill_switch}) 감지 — 신규 진입 중단"

        if self.state.consecutive_losses >= self.cfg.max_consecutive_losses:
            return (
                f"연속 {self.state.consecutive_losses}회 손실 — "
                "오늘은 중단합니다 (내일 자동 재개, 그 전에 전략 점검 권장)"
            )

        base = self.state.day_start_equity or equity
        if base > 0:
            # 두 가지로 재고 더 나쁜 쪽을 택한다.
            #
            # 1) 기록된 실현손익 — 정확하지만 '청산이 기록되었을 때'만 맞다.
            # 2) 자본 낙폭 — 거래소가 준 숫자 하나뿐이라 항상 맞다.
            #
            # 실전에서 청산 감지가 어긋나도 2번은 살아 있다.
            # 안전장치를 취약한 경로 하나에만 걸어두면 안 된다.
            by_trades = -self.state.realized_pnl / base
            by_equity = (base - equity) / base
            loss_pct = max(by_trades, by_equity)
            if loss_pct >= self.cfg.max_daily_loss_pct:
                source = "실현손익" if by_trades >= by_equity else "자본 낙폭"
                return (
                    f"일일 손실 한도 초과 ({loss_pct:.2%} ≥ "
                    f"{self.cfg.max_daily_loss_pct:.2%}, {source} 기준) — 오늘은 여기까지"
                )

        if self.state.open_positions >= self.cfg.max_open_positions:
            return f"동시 보유 한도 도달 ({self.state.open_positions}/{self.cfg.max_open_positions})"

        return ""

    # --- 사이징 ----------------------------------------------------------
    def evaluate_entry(
        self,
        *,
        equity: float,
        price: float,
        side: Side,
        stop_loss: float | None,
        leverage: float,
        min_size: float = 0.0,
    ) -> RiskDecision:
        self.roll_day(equity)

        reason = self.halt_reason(equity)
        if reason:
            self.state.halted_reason = reason
            return RiskDecision(False, reason=reason)

        if equity <= 0:
            return RiskDecision(False, reason="자본이 0 이하입니다.")
        if price <= 0:
            return RiskDecision(False, reason=f"잘못된 가격: {price}")
        if stop_loss is None:
            # 손절 없이 들어가면 최대 손실이 '전액'이 된다. 수량을 계산할 근거도 없다.
            return RiskDecision(False, reason="손절가 없는 진입은 허용하지 않습니다.")

        # 손절이 진입가 반대편에 있는지 확인 (롱인데 손절이 위에 있으면 즉시 체결된다)
        if side is Side.LONG and stop_loss >= price:
            return RiskDecision(False, reason=f"롱 손절가({stop_loss})가 현재가({price}) 이상입니다.")
        if side is Side.SHORT and stop_loss <= price:
            return RiskDecision(False, reason=f"숏 손절가({stop_loss})가 현재가({price}) 이하입니다.")

        stop_distance = abs(price - stop_loss)
        if stop_distance / price < 0.0005:
            # 손절이 너무 붙어 있으면 수량이 폭발하고 노이즈에 즉사한다.
            return RiskDecision(False, reason="손절폭이 너무 좁습니다 (0.05% 미만).")

        risk_amount = equity * self.cfg.risk_per_trade
        size = risk_amount / stop_distance

        # 명목가 상한: 레버리지를 감안해도 이 이상은 안 잡는다
        max_notional = equity * self.cfg.max_position_pct * min(leverage, self.cfg.max_leverage)
        if size * price > max_notional:
            size = max_notional / price
            log.info("명목가 상한으로 수량 축소: %.6f", size)

        # 증거금 상한: 가용 자본을 넘는 주문은 어차피 거절된다
        max_by_margin = equity * min(leverage, self.cfg.max_leverage) / price
        size = min(size, max_by_margin)

        if size <= 0 or (min_size and size < min_size):
            return RiskDecision(
                False,
                reason=f"계산된 수량({size:.8f})이 최소 주문 수량({min_size}) 미만입니다.",
            )

        self.state.open_positions += 1
        return RiskDecision(
            True,
            size=size,
            reason=(
                f"자본 {equity:.2f} × {self.cfg.risk_per_trade:.1%} = {risk_amount:.2f} 리스크, "
                f"손절폭 {stop_distance:.2f} → 수량 {size:.6f}"
            ),
        )

    def release(self) -> None:
        """진입이 실패했을 때 예약해둔 포지션 슬롯을 되돌린다."""
        self.state.open_positions = max(0, self.state.open_positions - 1)

    def sync_open_positions(self, actual: int) -> None:
        """실제 거래소 포지션 수로 카운터를 맞춘다.

        카운터를 자체적으로 증감시키면 어긋날 수 있고, 어긋나는 방향이
        하필 '한도 도달'이면 봇이 조용히 매매를 멈춘다(실제로 그랬다).
        매 틱 실제 상태로 덮어쓰면 그 실패 모드가 사라진다.
        """
        if actual != self.state.open_positions:
            log.debug("포지션 카운터 보정: %d → %d", self.state.open_positions, actual)
        self.state.open_positions = actual
