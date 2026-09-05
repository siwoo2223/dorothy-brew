import unittest

from dorothy.exchange.base import OrderError
from dorothy.exchange.paper import PaperExchange, ReplayExchange
from dorothy.models import Candle, Side

SYM = "BTC/USDT:USDT"


def c(ts, o, h, l, cl, v=1.0):
    return Candle(ts, o, h, l, cl, v)


class TestPaperFills(unittest.TestCase):
    def setUp(self):
        self.px = PaperExchange(equity=1000, taker_fee=0.001, slippage=0.01)
        self.px.feed_candle(c(0, 100, 100, 100, 100))

    def test_long_entry_pays_slippage_upward(self):
        pos = self.px.open_position(SYM, Side.LONG, 1.0, stop_loss=90)
        self.assertAlmostEqual(pos.entry_price, 101.0)

    def test_short_entry_receives_slippage_downward(self):
        pos = self.px.open_position(SYM, Side.SHORT, 1.0, stop_loss=110)
        self.assertAlmostEqual(pos.entry_price, 99.0)

    def test_fees_are_charged_on_both_sides(self):
        self.px.open_position(SYM, Side.LONG, 1.0, stop_loss=90)
        self.px.feed_candle(c(1, 100, 100, 100, 100))
        self.px.close_position(SYM, reason="test")
        trade = self.px.trades[0]
        # 진입 101×0.001 + 청산 99×0.001
        self.assertAlmostEqual(trade.fee, 0.101 + 0.099, places=6)
        self.assertLess(trade.net_pnl, trade.gross_pnl)

    def test_double_entry_is_rejected(self):
        self.px.open_position(SYM, Side.LONG, 1.0, stop_loss=90)
        with self.assertRaises(OrderError):
            self.px.open_position(SYM, Side.LONG, 1.0, stop_loss=90)

    def test_zero_size_is_rejected(self):
        with self.assertRaises(OrderError):
            self.px.open_position(SYM, Side.LONG, 0.0, stop_loss=90)

    def test_closing_without_position_raises(self):
        with self.assertRaises(OrderError):
            self.px.close_position(SYM)


class TestPaperStops(unittest.TestCase):
    def _fresh(self):
        px = PaperExchange(equity=1000, taker_fee=0.0, slippage=0.0)
        px.feed_candle(c(0, 100, 100, 100, 100))
        return px

    def test_long_stop_triggers_on_low(self):
        px = self._fresh()
        px.open_position(SYM, Side.LONG, 1.0, stop_loss=95)
        px.feed_candle(c(1, 100, 101, 94, 100))
        self.assertIsNone(px.fetch_position(SYM))
        self.assertEqual(px.trades[0].reason, "stop_loss")
        self.assertAlmostEqual(px.trades[0].exit_price, 95)

    def test_short_stop_triggers_on_high(self):
        px = self._fresh()
        px.open_position(SYM, Side.SHORT, 1.0, stop_loss=105)
        px.feed_candle(c(1, 100, 106, 99, 100))
        self.assertEqual(px.trades[0].reason, "stop_loss")

    def test_take_profit_triggers(self):
        px = self._fresh()
        px.open_position(SYM, Side.LONG, 1.0, stop_loss=95, take_profit=110)
        px.feed_candle(c(1, 100, 111, 99, 105))
        self.assertEqual(px.trades[0].reason, "take_profit")

    def test_stop_wins_when_both_hit_in_one_candle(self):
        # 한 봉 안에서 손절과 익절이 모두 닿으면 보수적으로 손절을 택한다.
        px = self._fresh()
        px.open_position(SYM, Side.LONG, 1.0, stop_loss=95, take_profit=110)
        px.feed_candle(c(1, 100, 115, 90, 105))
        self.assertEqual(px.trades[0].reason, "stop_loss")

    def test_equity_reflects_realized_loss(self):
        px = self._fresh()
        px.open_position(SYM, Side.LONG, 2.0, stop_loss=95)
        px.feed_candle(c(1, 100, 100, 90, 95))
        self.assertAlmostEqual(px.equity, 1000 - 10.0)   # (100-95)×2


class TestReplayExchange(unittest.TestCase):
    def test_replay_advances_one_candle_per_fetch(self):
        candles = [c(i, 100, 100, 100, 100) for i in range(3)]
        rx = ReplayExchange(candles, equity=1000)
        self.assertEqual(len(rx.fetch_candles(SYM, "5m")), 1)
        self.assertEqual(len(rx.fetch_candles(SYM, "5m")), 2)
        self.assertEqual(len(rx.fetch_candles(SYM, "5m")), 3)
        self.assertTrue(rx.exhausted)


if __name__ == "__main__":
    unittest.main()
