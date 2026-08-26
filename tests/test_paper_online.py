import unittest

from dorothy.exchange.base import Exchange
from dorothy.exchange.paper import PaperExchange
from dorothy.models import Account, Candle, Side

SYM = "BTC/USDT:USDT"


class FakeSource(Exchange):
    """실거래소 대신 미리 정한 캔들을 돌려주는 시세 공급자."""

    def __init__(self, candles):
        self.candles = candles

    @property
    def name(self):
        return "fake"

    def fetch_candles(self, symbol, timeframe, limit=200):
        return self.candles[-limit:]

    def fetch_price(self, symbol):
        return self.candles[-1].close

    def fetch_account(self):
        return Account(equity=0, available=0)

    def fetch_position(self, symbol):
        return None

    def set_leverage(self, symbol, leverage, margin_mode):
        pass

    def open_position(self, symbol, side, size, **kwargs):
        raise NotImplementedError

    def close_position(self, symbol, *, reason=""):
        raise NotImplementedError

    def cancel_all(self, symbol):
        pass

    def poll_closed_trades(self, symbol):
        return []


class TestOnlinePaperStops(unittest.TestCase):
    def test_stop_triggers_from_live_feed(self):
        """실시세를 쓰는 페이퍼 모드에서도 손절이 체결되어야 한다.

        시세를 source에서 받아오면 캔들이 내부 버퍼에 쌓이지 않아,
        스탑 판정을 따로 걸어주지 않으면 손절이 영원히 발동하지 않는다.
        """
        source = FakeSource([Candle(0, 100, 100, 100, 100, 1)])
        px = PaperExchange(equity=1000, taker_fee=0.0, slippage=0.0, source=source)

        px.fetch_price(SYM)
        px.open_position(SYM, Side.LONG, 1.0, stop_loss=95.0)
        self.assertIsNotNone(px.fetch_position(SYM))

        # 다음 캔들이 손절가를 관통
        source.candles.append(Candle(1, 100, 100, 90, 92, 1))
        px.fetch_candles(SYM, "5m")

        self.assertIsNone(px.fetch_position(SYM), "손절이 체결되지 않았습니다")
        self.assertEqual(px.trades[0].reason, "stop_loss")
        self.assertAlmostEqual(px.trades[0].exit_price, 95.0)


if __name__ == "__main__":
    unittest.main()
