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
        # 리플레이에서는 캔들 시각이 '지금'이어야 한다. 벽시계를 쓰면 8.6년치를
        # 몇 초에 재생하는 동안 전체가 하루로 취급되어, 일일 손실 한도와 연속 손실이
        # 한 번 걸리면 영영 안 풀린다 — 실제로 체결이 142건에서 76건으로 줄었다.
        self._replay_clock = False
        self.risk = RiskManager(
            config.risk,
            kill_switch_file=config.kill_switch_file,
            clock=self._now_ms,
        )
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

    def _now_ms(self) -> int:
        """리스크 판정의 '지금'. 리플레이면 캔들 시각, 실시간이면 벽시계."""
        if self._replay_clock and self._last_candle_ts:
            return self._last_candle_ts
        return int(time.time() * 1000)

    # --- 재시작 복구 -----------------------------------------------------
    def _restore_state(self) -> None:
        """저널에서 재시작을 견뎌야 하는 상태를 복구한다.

        **하나라도 빠뜨리면 재시작이 안전장치를 초기화한다.** 여기 모아둔
        이유가 그것이다 — start()에 흩어놓으면 테스트가 start()를 못 부르고
        같은 코드를 베껴 쓰게 되고, 그러면 복구를 지워도 테스트가 통과한다.
        """
        # 연속 손실: 재시작으로 한도를 초기화하면 안 된다.
        self.risk.state.consecutive_losses = self.journal.consecutive_losses()
        # 고점: 안 하면 고점에서 15% 내려온 상태로 재시작했을 때
        # 그 자리가 새 고점이 되어 낙폭 한도가 영영 안 걸린다.
        self.risk.state.peak_equity = self.journal.peak_equity()
        # 판단 위치: 안 하면 재시작할 때마다 마지막 마감봉을 처음 보는 봉으로
        # 착각해서 다시 판단한다 — 방금 손절당한 그 봉에 다시 들어간다.
        self._last_candle_ts = self.journal.last_candle_ts()

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

        self._restore_state()

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
        # 리플레이는 캔들 시각을 '지금'으로 쓴다 (위 _now_ms 참고).
        self._replay_clock = True
        # 저장된 일지에서 연속 손실을 읽으면 **이전 실행 결과가 새어 들어온다.**
        # 리플레이는 매번 깨끗한 상태에서 시작해야 결과를 비교할 수 있다.
        self.risk.state.consecutive_losses = 0
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
        # 주문을 내기 **전에** 기록한다. 주문 도중에 죽으면 둘 중 하나인데,
        # 먼저 기록하면 최악이 '신호 한 번 놓침'이고, 나중에 기록하면
        # 최악이 '같은 자리에 두 번 진입'이다. 돈이 걸린 쪽을 피한다.
        if not self._replay_clock:
            self.journal.set_state("last_candle_ts", str(last.ts))

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
