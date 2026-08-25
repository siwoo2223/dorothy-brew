import unittest

from dorothy.models import Action, Candle, Position, Side
from dorothy.strategy.base import get_strategy
from dorothy.strategy.ema_cross import EmaCrossStrategy


def series(prices):
    return [Candle(i * 1000, p, p * 1.001, p * 0.999, p, 1.0) for i, p in enumerate(prices)]


def first_signal(strat, prices, position=None):
    """캔들을 하나씩 늘려가며 처음으로 HOLD가 아닌 신호가 나온 지점을 찾는다.

    크로스는 특정 봉에서만 발생하므로, 마지막 봉이 그 봉이 되도록 맞춰야 한다.
    """
    candles = series(prices)
    for i in range(strat.warmup, len(candles)):
        sig = strat.generate(candles[: i + 1], position)
        if sig.action is not Action.HOLD:
            return sig, candles[i].close
    return None, None


class TestEmaCross(unittest.TestCase):
    def setUp(self):
        self.strat = EmaCrossStrategy(fast=3, slow=8, atr_period=5)

    def test_warmup_blocks_early_signals(self):
        sig = self.strat.generate(series([100] * 5), None)
        self.assertIs(sig.action, Action.HOLD)

    def test_golden_cross_enters_long_with_stop_below(self):
        prices = [100] * 20 + [100 + i * 2 for i in range(1, 20)]
        sig, price = first_signal(self.strat, prices)
        self.assertIsNotNone(sig, "골든크로스 신호가 나오지 않았습니다")
        self.assertIs(sig.action, Action.ENTER_LONG)
        self.assertIsNotNone(sig.stop_loss)
        self.assertLess(sig.stop_loss, price)
        self.assertGreater(sig.take_profit, price)

    def test_dead_cross_enters_short_with_stop_above(self):
        prices = [100] * 20 + [100 - i * 2 for i in range(1, 20)]
        sig, price = first_signal(self.strat, prices)
        self.assertIsNotNone(sig, "데드크로스 신호가 나오지 않았습니다")
        self.assertIs(sig.action, Action.ENTER_SHORT)
        self.assertGreater(sig.stop_loss, price)

    def test_short_disabled_yields_hold(self):
        strat = EmaCrossStrategy(fast=3, slow=8, atr_period=5, allow_short=False)
        prices = [100] * 20 + [100 - i * 2 for i in range(1, 20)]
        sig, _ = first_signal(strat, prices)
        self.assertIsNone(sig, "숏 비활성화인데 신호가 나왔습니다")

    def test_long_exits_on_dead_cross(self):
        prices = [100] * 20 + [100 + i * 2 for i in range(1, 15)] + [
            128 - i * 4 for i in range(1, 20)
        ]
        pos = Position("BTC/USDT:USDT", Side.LONG, 1.0, 100.0)
        candles = series(prices)
        # 보유 중에는 반대 크로스에서만 청산되어야 한다
        actions = [
            self.strat.generate(candles[: i + 1], pos).action
            for i in range(self.strat.warmup, len(candles))
        ]
        self.assertIn(Action.EXIT, actions)
        self.assertNotIn(Action.ENTER_LONG, actions)
        self.assertNotIn(Action.ENTER_SHORT, actions)

    def test_no_signal_in_flat_market(self):
        sig, _ = first_signal(self.strat, [100.0] * 60)
        self.assertIsNone(sig)

    def test_strategy_never_returns_a_size(self):
        # 수량 결정은 리스크 매니저 몫이다. Signal에 수량 필드가 있으면 안 된다.
        sig = self.strat.generate(series([100] * 40), None)
        self.assertFalse(hasattr(sig, "size"))

    def test_fast_must_be_less_than_slow(self):
        with self.assertRaises(ValueError):
            EmaCrossStrategy(fast=20, slow=10)

    def test_registry_lookup(self):
        self.assertIsInstance(get_strategy("ema_cross", fast=5, slow=9), EmaCrossStrategy)
        with self.assertRaises(KeyError):
            get_strategy("존재하지않는전략")


if __name__ == "__main__":
    unittest.main()
