import unittest

from dorothy.config import RiskConfig
from dorothy.exchange.paper import PaperExchange
from dorothy.execution.executor import Executor
from dorothy.models import Action, Candle, Signal, Side
from dorothy.risk.manager import RiskManager

SYM = "BTC/USDT:USDT"


class TestExecutor(unittest.TestCase):
    def setUp(self):
        self.ex = PaperExchange(equity=1000, taker_fee=0.0006, slippage=0.0)
        self.ex.feed_candle(Candle(0, 100, 100, 100, 100, 1))
        self.risk = RiskManager(RiskConfig(), kill_switch_file="/nonexistent")
        self.risk.roll_day(1000)
        self.executor = Executor(self.ex, self.risk, symbol=SYM, leverage=2)

    def test_entry_size_comes_from_risk_manager_not_signal(self):
        sig = Signal(Action.ENTER_LONG, "테스트", stop_loss=95.0)
        pos = self.executor.handle(sig, position=None, equity=1000, candle_ts=1)
        self.assertIsNotNone(pos)
        # 1000 × 1% = 10 리스크, 손절폭 5 → 2.0
        self.assertAlmostEqual(pos.size, 2.0, places=6)

    def test_entry_without_stop_is_blocked(self):
        sig = Signal(Action.ENTER_LONG, "손절 없음")
        self.assertIsNone(self.executor.handle(sig, position=None, equity=1000, candle_ts=1))
        self.assertIsNone(self.ex.fetch_position(SYM))

    def test_no_double_entry_within_same_candle(self):
        sig = Signal(Action.ENTER_LONG, "1차", stop_loss=95.0)
        pos = self.executor.handle(sig, position=None, equity=1000, candle_ts=42)
        self.assertIsNotNone(pos)
        self.ex.close_position(SYM, reason="수동")
        self.risk.record_trade(self.ex.trades[-1])
        again = self.executor.handle(sig, position=None, equity=1000, candle_ts=42)
        self.assertIsNone(again, "같은 캔들에서 재진입이 발생했습니다")

    def test_exit_signal_closes_position(self):
        self.executor.handle(
            Signal(Action.ENTER_LONG, "진입", stop_loss=95.0),
            position=None, equity=1000, candle_ts=1,
        )
        pos = self.ex.fetch_position(SYM)
        after = self.executor.handle(
            Signal(Action.EXIT, "청산"), position=pos, equity=1000, candle_ts=2
        )
        self.assertIsNone(after)
        self.assertIsNone(self.ex.fetch_position(SYM))

    def test_hold_signal_changes_nothing(self):
        out = self.executor.handle(
            Signal(Action.HOLD, "관망"), position=None, equity=1000, candle_ts=1
        )
        self.assertIsNone(out)
        self.assertIsNone(self.ex.fetch_position(SYM))

    def test_force_close_clears_position(self):
        self.executor.handle(
            Signal(Action.ENTER_SHORT, "숏", stop_loss=105.0),
            position=None, equity=1000, candle_ts=1,
        )
        self.assertIsNotNone(self.ex.fetch_position(SYM))
        self.executor.force_close("킬스위치")
        self.assertIsNone(self.ex.fetch_position(SYM))

    def test_risk_slot_is_released_when_order_fails(self):
        # 이미 포지션이 있는 상태에서 진입을 시도하면 거래소가 거부한다.
        # 이때 리스크 매니저가 잡아둔 슬롯이 반납되지 않으면 봇이 영구히 막힌다.
        self.ex.open_position(SYM, Side.LONG, 1.0, stop_loss=95)
        before = self.risk.state.open_positions
        self.executor._enter(
            Signal(Action.ENTER_LONG, "중복", stop_loss=95.0), equity=1000, candle_ts=9
        )
        self.assertEqual(self.risk.state.open_positions, before)


if __name__ == "__main__":
    unittest.main()
