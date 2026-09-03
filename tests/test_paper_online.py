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


class OnlinePathChargesFundingTests(unittest.TestCase):
    """실시간 페이퍼(source 사용)가 오프라인과 같은 처리를 거치는가.

    **이 파일이 막는 사고:**
    온라인 경로가 _check_stops만 부르고 _apply_funding을 안 불렀다.
    실시간 페이퍼가 펀딩비를 한 푼도 내지 않았다는 뜻이다 —
    백테스트보다 좋게 나오는 방향이라 실전 직전에 딱 속는다.
    (예전에 cmd_paper가 펀딩을 안 넘겨 자본이 4.8% 부풀었던 것과 같은 종류.)
    """

    class Feed(Exchange):
        """실시세를 흉내내는 source. 같은 마감봉을 반복해서 준다."""

        def __init__(self, candles):
            self.candles = candles
            self.cursor = 1
            self.calls = 0

        @property
        def name(self):
            return "feed"

        def fetch_candles(self, symbol, timeframe, limit=200):
            self.calls += 1
            return self.candles[: self.cursor][-limit:]

        def fetch_price(self, symbol):
            return self.candles[self.cursor - 1].close

        def fetch_account(self):
            raise NotImplementedError

        def fetch_position(self, symbol):
            return None

        def set_leverage(self, symbol, leverage, margin_mode):
            pass

        def open_position(self, *a, **kw):
            raise NotImplementedError

        def close_position(self, symbol, *, reason=""):
            raise NotImplementedError

        def cancel_all(self, symbol):
            pass

        def poll_closed_trades(self, symbol):
            return []

    @staticmethod
    def _candles(n=10, price=100.0, step_ms=4 * 3_600_000):
        return [Candle(i * step_ms, price, price, price, price, 1.0) for i in range(n)]

    def _exchange(self, feed):
        return PaperExchange(
            equity=1000.0, taker_fee=0.0, slippage=0.0,
            funding_rate=0.001, funding_interval_hours=8, source=feed,
        )

    def test_funding_is_charged_on_the_online_path(self):
        feed = self.Feed(self._candles())
        ex = self._exchange(feed)
        ex.fetch_candles("BTC/USDT:USDT", "4h", 200)
        ex.open_position("BTC/USDT:USDT", Side.LONG, 1.0)
        before = ex.equity
        for _ in range(6):                       # 봉을 넘기며 펀딩 시각을 지난다
            feed.cursor += 1
            ex.fetch_candles("BTC/USDT:USDT", "4h", 200)
        self.assertLess(ex.equity, before,
                        "온라인 페이퍼가 펀딩비를 떼지 않았습니다")

    def test_polling_the_same_bar_does_not_charge_twice(self):
        feed = self.Feed(self._candles())
        ex = self._exchange(feed)
        ex.fetch_candles("BTC/USDT:USDT", "4h", 200)
        ex.open_position("BTC/USDT:USDT", Side.LONG, 1.0)
        feed.cursor += 1
        ex.fetch_candles("BTC/USDT:USDT", "4h", 200)
        once = ex.equity
        for _ in range(50):                      # 15초 폴링 흉내
            ex.fetch_candles("BTC/USDT:USDT", "4h", 200)
        self.assertAlmostEqual(ex.equity, once, places=12,
                               msg="같은 봉에서 펀딩이 여러 번 부과됐습니다")

    def test_the_equity_curve_does_not_grow_while_polling(self):
        """15초 폴링이면 4시간봉 하나에 960번 호출된다. 곡선이 그만큼 커지면 안 된다."""
        feed = self.Feed(self._candles())
        ex = self._exchange(feed)
        for _ in range(200):
            ex.fetch_candles("BTC/USDT:USDT", "4h", 200)
        self.assertEqual(len(ex.equity_curve), 1,
                         f"봉 하나에 자본 곡선이 {len(ex.equity_curve)}점 쌓였습니다")

    def test_stops_still_fire_on_the_online_path(self):
        """펀딩을 넣다가 스탑 판정을 깨뜨리지 않았는지."""
        candles = self._candles(3)
        candles.append(Candle(3 * 4 * 3_600_000, 100.0, 100.0, 80.0, 85.0, 1.0))
        feed = self.Feed(candles)
        ex = self._exchange(feed)
        ex.fetch_candles("BTC/USDT:USDT", "4h", 200)
        ex.open_position("BTC/USDT:USDT", Side.LONG, 1.0, stop_loss=90.0)
        feed.cursor = 4
        ex.fetch_candles("BTC/USDT:USDT", "4h", 200)
        self.assertIsNone(ex.fetch_position("BTC/USDT:USDT"), "손절이 체결되지 않았습니다")
        self.assertEqual(ex.trades[-1].reason, "stop_loss")
