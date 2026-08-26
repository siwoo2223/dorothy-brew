"""실시간 매매 루프 (paper / live 공용).

paper와 live의 차이는 주입되는 Exchange 구현 하나뿐이다.
같은 코드로 돌아야 페이퍼에서 검증한 것이 실전에서도 그대로 동작한다.

루프 한 번:
  1. 캔들 조회 → 새 캔들이 없으면 아무것도 하지 않는다 (봉당 1회 판단)
  2. 포지션/자본 조회
  3. 전략 → 신호
  4. 리스크 검증 → 주문
  5. 기록 + 알림
"""

from __future__ import annotations

import logging
import signal as signal_module
import time
from pathlib import Path

from .config import Config
from .exchange.base import Exchange
from .execution.executor import Executor
from .journal.store import Journal
from .models import Action
from .notify.telegram import Notifier
from .risk.manager import RiskManager
from .strategy.base import Strategy

log = logging.getLogger(__name__)


class TradingEngine:
    def __init__(
        self,
        config: Config,
        exchange: Exchange,
        strategy: Strategy,
        *,
        journal: Journal | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.cfg = config
        self.exchange = exchange
        self.strategy = strategy
        self.journal = journal or Journal(config.db_path)
        self.notifier = notifier or Notifier(
            config.telegram_token, config.telegram_chat_id, enabled=config.notify.telegram_enabled
        )
        self.risk = RiskManager(config.risk, kill_switch_file=config.kill_switch_file)
        self.executor = Executor(
            exchange,
            self.risk,
            symbol=config.exchange.symbol,
            leverage=config.exchange.leverage,
            min_size=config.exchange.min_order_size,
            size_step=config.exchange.size_step,
        )
        self._running = False
        self._last_candle_ts = 0
        self._reported_trades = 0

    # --- 수명주기 --------------------------------------------------------
    def _install_signal_handlers(self) -> None:
        def handler(signum, frame):  # noqa: ARG001
            log.warning("종료 신호 수신 — 루프를 정리합니다. (포지션은 유지됩니다)")
            self._running = False

        signal_module.signal(signal_module.SIGINT, handler)
        signal_module.signal(signal_module.SIGTERM, handler)

    def start(self) -> None:
        errors = self.cfg.validate()
        if errors:
            for e in errors:
                log.error("설정 오류: %s", e)
            raise SystemExit(1)

        # 재시작 시 연속 손실 카운터 복구 (재시작으로 한도를 초기화하면 안 된다)
        self.risk.state.consecutive_losses = self.journal.consecutive_losses()

        account = self.exchange.fetch_account()
        self.risk.roll_day(account.equity)
        log.info(
            "시작: mode=%s exchange=%s symbol=%s tf=%s lev=%s equity=%.2f (연속손실 %d)",
            self.cfg.mode, self.exchange.name, self.cfg.exchange.symbol,
            self.cfg.exchange.timeframe, self.cfg.exchange.leverage,
            account.equity, self.risk.state.consecutive_losses,
        )
        self.notifier.send(
            f"🤖 dorothy-brew 시작\n"
            f"모드: {self.cfg.mode} / {self.cfg.exchange.symbol} {self.cfg.exchange.timeframe}\n"
            f"자본: {account.equity:,.2f} USDT"
        )

        # 설정 파일의 최소 수량은 추측이다. 실전에서는 거래소가 알려주는 값으로 덮어쓴다.
        limits = getattr(self.exchange, "market_limits", None)
        if limits is not None:
            try:
                min_size, step = limits(self.cfg.exchange.symbol)
                if min_size > 0:
                    self.executor.min_size = min_size
                    self.executor.size_step = step
                    log.info("거래소 주문 제약: 최소 %s, 단위 %s", min_size, step)
                    notional = min_size * self.exchange.fetch_price(self.cfg.exchange.symbol)
                    if notional > account.equity * self.cfg.exchange.leverage * 0.5:
                        log.warning(
                            "자본 대비 최소 주문이 큽니다 (최소 명목가 %.2f, 자본 %.2f). "
                            "대부분의 신호가 수량 미달로 거부될 수 있습니다.",
                            notional, account.equity,
                        )
            except Exception:  # noqa: BLE001
                log.warning("거래소 주문 제약 조회 실패 — 설정값을 사용합니다", exc_info=True)

        if self.cfg.mode == "live":
            self.exchange.set_leverage(
                self.cfg.exchange.symbol,
                min(self.cfg.exchange.leverage, self.cfg.risk.max_leverage),
                self.cfg.exchange.margin_mode,
            )

        self._install_signal_handlers()
        self._running = True
        self.run_forever()

    def start_offline_replay(self) -> None:
        """저장된 캔들이 소진될 때까지 tick()을 반복한다 (오프라인 검증용).

        실시간 루프와 같은 코드 경로를 타므로, 여기서 터지는 버그는 실전에서도 터진다.
        """
        self.risk.state.consecutive_losses = self.journal.consecutive_losses()
        account = self.exchange.fetch_account()
        self.risk.roll_day(account.equity)
        log.info("오프라인 리플레이 시작 (자본 %.2f)", account.equity)

        self._running = True
        while self._running and not getattr(self.exchange, "exhausted", True):
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("리플레이 중 예외")
                break

        position = self.exchange.fetch_position(self.cfg.exchange.symbol)
        if position is not None:
            self.executor.force_close("리플레이 종료")
            self._sync_closed_trades()
        log.info("리플레이 종료 — 최종 자본 %.2f", self.exchange.fetch_account().equity)

    def run_forever(self) -> None:
        while self._running:
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                # 한 번의 예외로 봇이 죽으면 포지션이 방치된다. 로그만 남기고 계속 돈다.
                log.exception("루프 예외 — 계속 진행합니다")
                self.notifier.send("⚠️ 루프 예외 발생 (로그 확인 필요)")
            time.sleep(self.cfg.poll_interval_sec)
        log.info("루프 종료")
        self.notifier.send("🛑 dorothy-brew 종료")

    # --- 한 사이클 -------------------------------------------------------
    def tick(self) -> None:
        symbol = self.cfg.exchange.symbol
        candles = self.exchange.fetch_candles(
            symbol, self.cfg.exchange.timeframe, limit=max(self.strategy.warmup + 50, 100)
        )
        if not candles:
            log.warning("캔들 없음 — 이번 틱 건너뜀")
            return

        last = candles[-1]
        position = self.exchange.fetch_position(symbol)

        # 킬스위치는 새 캔들 여부와 무관하게 매 틱 확인한다
        if Path(self.cfg.kill_switch_file).exists() and position is not None:
            log.warning("킬스위치 감지 — 보유 포지션을 정리합니다")
            self.executor.force_close("kill_switch")
            self.notifier.send("🚨 킬스위치 작동 — 포지션 정리")
            self._running = False
            return

        if last.ts == self._last_candle_ts:
            return   # 아직 새 캔들 없음. 봉 하나에 판단은 한 번만.
        self._last_candle_ts = last.ts

        account = self.exchange.fetch_account()
        self.risk.roll_day(account.equity)
        self.journal.record_equity(last.ts, account.equity)

        # 카운터를 자체 증감에 맡기지 않고 실제 포지션 수로 맞춘다.
        self.risk.sync_open_positions(1 if position is not None else 0)

        sig = self.strategy.generate(candles, position)
        if sig.action is not Action.HOLD:
            log.info("신호: %s (%s)", sig.action.value, sig.reason)

        before = position
        after = self.executor.handle(
            sig, position=position, equity=account.equity, candle_ts=last.ts
        )
        self._sync_closed_trades()

        if before is None and after is not None:
            self.notifier.send(
                f"📈 진입 {after.side.value.upper()} {symbol}\n"
                f"가격 {after.entry_price:,.2f} / 수량 {after.size:.6f}\n"
                f"손절 {after.stop_loss:,.2f}\n사유: {sig.reason}"
            )
        elif before is not None and after is None:
            self.notifier.send(f"📉 청산 {symbol}\n사유: {sig.reason}")

    def _sync_closed_trades(self) -> None:
        """청산된 매매를 리스크 매니저와 저널에 반영한다.

        일일 손실 한도와 연속 손실 차단은 여기가 돌아야 작동한다.
        거래소 스탑으로 청산되면 봇은 주문을 낸 적이 없어 그 사실을 모르므로,
        거래소 구현이 포지션 소멸을 감지해 알려준다.
        """
        for trade in self.exchange.poll_closed_trades(self.cfg.exchange.symbol):
            self.risk.record_trade(trade)
            self.journal.record_trade(trade)
            self._reported_trades += 1
            emoji = "✅" if trade.net_pnl > 0 else "❌"
            self.notifier.send(
                f"{emoji} 청산 {trade.symbol} {trade.side.value}\n"
                f"손익 {trade.net_pnl:+,.2f} USDT ({trade.return_pct:+.2f}%)\n"
                f"사유: {trade.reason}"
            )
