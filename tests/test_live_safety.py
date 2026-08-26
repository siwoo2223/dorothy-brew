"""실전 모드 안전장치 회귀 테스트.

**이 파일이 막는 사고:**
거래소 스탑으로 청산되면 봇은 주문을 낸 적이 없어 그 사실을 모른다.
그러면 일일 손실 한도와 연속 손실 차단이 영원히 0을 보고 작동하지 않고,
포지션 카운터가 반납되지 않아 봇이 첫 매매 후 조용히 멈춘다.
실제로 그 상태였고, 페이퍼 모드에서만 멀쩡해서 테스트가 전부 통과했다.

그래서 여기서는 **페이퍼 거래소를 쓰지 않는다.**
실거래소처럼 '봇이 모르는 사이에 포지션이 사라지는' 가짜 거래소로 검증한다.
"""

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from dorothy.config import Config, RiskConfig
from dorothy.engine import TradingEngine
from dorothy.exchange.base import Exchange
from dorothy.journal.store import Journal
from dorothy.models import Account, Candle, Position, Side, Trade
from dorothy.notify.telegram import Notifier
from dorothy.risk.manager import RiskManager
from dorothy.strategy.base import get_strategy

SYM = "BTC/USDT:USDT"


class BrokerLikeExchange(Exchange):
    """실거래소 흉내. 포지션이 봇 모르게 사라진다.

    PaperExchange와 달리 `trades` 리스트를 들고 있지 않다 —
    실전 버그가 정확히 그 차이에서 나왔기 때문이다.
    """

    def __init__(self, candles, equity=1000.0):
        self.candles = candles
        self.cursor = 60
        self.equity = equity
        self.position: Position | None = None
        self._watched: Position | None = None
        self._equity_before = 0.0
        self.orders: list[tuple] = []

    @property
    def name(self):
        return "broker-like"

    def fetch_candles(self, symbol, timeframe, limit=200):
        self.cursor = min(self.cursor + 1, len(self.candles))
        return self.candles[max(0, self.cursor - limit) : self.cursor]

    def fetch_price(self, symbol):
        return self.candles[min(self.cursor, len(self.candles)) - 1].close

    def fetch_account(self):
        return Account(equity=self.equity, available=self.equity,
                       positions=[self.position] if self.position else [])

    def fetch_position(self, symbol):
        return self.position

    def set_leverage(self, symbol, leverage, margin_mode):
        pass

    def open_position(self, symbol, side, size, *, stop_loss=None,
                      take_profit=None, client_id=""):
        price = self.fetch_price(symbol)
        self.position = Position(symbol, side, size, price, stop_loss=stop_loss,
                                 take_profit=take_profit, opened_at=1, client_id=client_id)
        self.orders.append(("open", side.value, size))
        return self.position

    def close_position(self, symbol, *, reason=""):
        self.position = None
        self.orders.append(("close", reason))
        return self.fetch_price(symbol)

    def cancel_all(self, symbol):
        pass

    # --- 실거래소와 같은 방식으로 청산을 '감지'한다 ---
    def poll_closed_trades(self, symbol):
        if self.position is not None and self._watched is None:
            self._equity_before = self.equity
            self._watched = self.position
            return []
        if self.position is None and self._watched is not None:
            pos = self._watched
            self._watched = None
            # 실제 BitgetExchange와 같은 방식: 손익을 자본 변화에서 뽑는다
            return [Trade(symbol, pos.side, pos.size, pos.entry_price,
                          self.fetch_price(symbol), 1, 2,
                          realized_pnl=self.equity - self._equity_before,
                          reason="거래소 청산")]
        return []

    # --- 테스트가 쓰는 조작 ---
    def stop_out(self, loss: float):
        """거래소 스탑이 발동한 상황. 봇은 주문을 낸 적이 없다."""
        self.equity -= loss
        self.position = None


def make_engine(exchange, tmpdir, **risk_overrides):
    cfg = Config()
    cfg.mode = "paper"
    cfg.exchange.symbol = SYM
    cfg.poll_interval_sec = 0
    cfg.db_path = str(Path(tmpdir) / "t.db")
    cfg.kill_switch_file = str(Path(tmpdir) / "NOKILL")
    cfg.risk = RiskConfig(**risk_overrides) if risk_overrides else cfg.risk
    engine = TradingEngine(
        cfg, exchange, get_strategy("donchian", channel=10),
        journal=Journal(cfg.db_path), notifier=Notifier(enabled=False),
    )
    engine.risk.roll_day(exchange.equity)
    return engine


def flat_candles(n=200, price=100.0):
    return [Candle(i * 1000, price, price, price, price, 1.0) for i in range(n)]


class TestClosedTradeDetection(unittest.TestCase):
    def test_exchange_stop_out_is_recorded(self):
        """거래소 스탑 청산이 저널과 리스크에 반영되어야 한다."""
        with TemporaryDirectory() as tmp:
            ex = BrokerLikeExchange(flat_candles())
            engine = make_engine(ex, tmp)

            ex.open_position(SYM, Side.LONG, 1.0, stop_loss=95.0)
            engine._sync_closed_trades()          # 관측 시작
            self.assertEqual(engine._reported_trades, 0)

            ex.stop_out(loss=30.0)                # 봇 모르게 청산
            engine._sync_closed_trades()

            self.assertEqual(engine._reported_trades, 1)
            self.assertEqual(len(engine.journal.recent_trades()), 1)

    def test_daily_loss_limit_triggers_from_exchange_stops(self):
        """봇이 청산 주문을 낸 적 없어도 일일 한도가 작동해야 한다."""
        with TemporaryDirectory() as tmp:
            ex = BrokerLikeExchange(flat_candles(), equity=1000.0)
            engine = make_engine(ex, tmp, max_daily_loss_pct=0.03, max_consecutive_losses=99)

            ex.open_position(SYM, Side.LONG, 1.0, stop_loss=95.0)
            engine._sync_closed_trades()
            ex.stop_out(loss=40.0)                # -4%
            engine._sync_closed_trades()

            self.assertIn("일일 손실 한도", engine.risk.halt_reason(ex.equity))

    def test_consecutive_losses_count_up_in_live_mode(self):
        with TemporaryDirectory() as tmp:
            ex = BrokerLikeExchange(flat_candles(), equity=10_000.0)
            engine = make_engine(ex, tmp, max_consecutive_losses=3, max_daily_loss_pct=0.99)

            for _ in range(3):
                ex.open_position(SYM, Side.LONG, 1.0, stop_loss=95.0)
                engine._sync_closed_trades()
                ex.stop_out(loss=10.0)
                engine._sync_closed_trades()

            self.assertEqual(engine.risk.state.consecutive_losses, 3)
            self.assertIn("연속", engine.risk.halt_reason(ex.equity))

    def test_a_win_resets_the_streak(self):
        with TemporaryDirectory() as tmp:
            ex = BrokerLikeExchange(flat_candles(), equity=10_000.0)
            engine = make_engine(ex, tmp)

            ex.open_position(SYM, Side.LONG, 1.0, stop_loss=95.0)
            engine._sync_closed_trades()
            ex.stop_out(loss=10.0)
            engine._sync_closed_trades()
            self.assertEqual(engine.risk.state.consecutive_losses, 1)

            ex.open_position(SYM, Side.LONG, 1.0, stop_loss=95.0)
            engine._sync_closed_trades()
            ex.stop_out(loss=-20.0)               # 이익
            engine._sync_closed_trades()
            self.assertEqual(engine.risk.state.consecutive_losses, 0)


class TestPositionCounterNeverSticks(unittest.TestCase):
    """봇이 첫 매매 후 조용히 멈추던 버그."""

    def test_counter_follows_reality(self):
        with TemporaryDirectory() as tmp:
            ex = BrokerLikeExchange(flat_candles())
            engine = make_engine(ex, tmp)

            engine.risk.state.open_positions = 1   # 어긋난 상태
            engine.risk.sync_open_positions(0)     # 실제로는 포지션 없음
            self.assertEqual(engine.risk.state.open_positions, 0)
            self.assertEqual(engine.risk.halt_reason(ex.equity), "")

    def test_bot_can_enter_again_after_an_exchange_stop(self):
        """스탑 청산 후에도 다음 진입이 승인되어야 한다."""
        with TemporaryDirectory() as tmp:
            ex = BrokerLikeExchange(flat_candles(), equity=10_000.0)
            engine = make_engine(ex, tmp, max_open_positions=1, max_daily_loss_pct=0.99)

            first = engine.risk.evaluate_entry(
                equity=10_000, price=100, side=Side.LONG, stop_loss=95, leverage=2
            )
            self.assertTrue(first.approved)

            ex.open_position(SYM, Side.LONG, 1.0, stop_loss=95.0)
            engine._sync_closed_trades()
            ex.stop_out(loss=10.0)
            engine._sync_closed_trades()
            engine.risk.sync_open_positions(0)     # 매 틱 하는 보정

            second = engine.risk.evaluate_entry(
                equity=9_990, price=100, side=Side.LONG, stop_loss=95, leverage=2
            )
            self.assertTrue(second.approved, f"재진입이 막혔습니다: {second.reason}")


class TestEquityFallback(unittest.TestCase):
    """청산 감지가 완전히 실패해도 자본 낙폭이 막아야 한다."""

    def test_daily_limit_works_with_no_trade_records_at_all(self):
        rm = RiskManager(RiskConfig(max_daily_loss_pct=0.03), kill_switch_file="/nonexistent")
        rm.roll_day(1000.0)
        self.assertEqual(rm.halt_reason(1000.0), "")
        reason = rm.halt_reason(960.0)             # 기록 0건, 자본만 -4%
        self.assertIn("일일 손실 한도", reason)
        self.assertIn("자본 낙폭", reason)

    def test_worse_of_the_two_measures_wins(self):
        rm = RiskManager(RiskConfig(max_daily_loss_pct=0.03), kill_switch_file="/nonexistent")
        rm.roll_day(1000.0)
        rm.record_trade(Trade("B", Side.LONG, 1, 100, 60, 0, 1))   # 기록상 -40
        # 자본은 아직 -10만 반영된 상태여도 기록 쪽이 더 나쁘면 그쪽으로 막는다
        self.assertIn("실현손익", rm.halt_reason(990.0))

    def test_profit_does_not_trigger_the_limit(self):
        rm = RiskManager(RiskConfig(max_daily_loss_pct=0.03), kill_switch_file="/nonexistent")
        rm.roll_day(1000.0)
        self.assertEqual(rm.halt_reason(1200.0), "")


if __name__ == "__main__":
    unittest.main()
