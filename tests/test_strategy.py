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


class RetiredStrategyTests(unittest.TestCase):
    """폐기 표시가 제 일을 하는지.

    실제 데이터에서 진 전략을 지우지 않고 표시만 하는 이유는, 새 전략이
    그보다 나은지 재려면 기준선이 남아 있어야 하기 때문이다. 대신 고를 때마다
    경고가 떠야 하고, '살아있는 전략' 목록에서는 빠져야 한다.
    """

    def test_retired_strategies_are_excluded_from_the_live_list(self):
        from dorothy.strategy.base import available, retired

        live = set(available(include_retired=False))
        for name in retired():
            self.assertNotIn(name, live)

    def test_retired_strategies_still_load(self):
        """기준선으로 쓰려면 여전히 만들어져야 한다."""
        from dorothy.strategy.base import get_strategy, retired

        for name in retired():
            self.assertIsNotNone(get_strategy(name))

    def test_choosing_a_retired_strategy_warns(self):
        from dorothy.strategy.base import get_strategy, retired

        name = next(iter(retired()))
        with self.assertLogs("dorothy.strategy.base", level="WARNING") as logs:
            get_strategy(name)
        self.assertIn("폐기", "\n".join(logs.output))

    def test_any_warning_names_an_actually_retired_strategy(self):
        """경고가 뜨면 반드시 폐기된 이름이 들어 있어야 한다.

        필터 전략은 기본 base가 donchian이라 만들 때 경고가 뜬다. 그건 맞는 동작이다 —
        폐기된 전략 위에 필터를 씌우고 있다는 걸 알아야 한다.
        """
        import logging

        from dorothy.strategy.base import available, get_strategy, retired

        names = set(retired())
        logger = logging.getLogger("dorothy.strategy.base")
        for name in available(include_retired=False):
            with self.assertLogs(logger, level="DEBUG") as logs:
                logger.debug("표시자")      # 경고가 하나도 없을 때를 대비한 것
                get_strategy(name)
            for line in logs.output:
                if "WARNING" not in line:
                    continue
                self.assertTrue(any(n in line for n in names), line)

    def test_every_retirement_states_its_evidence(self):
        """'별로였다'는 폐기 사유가 아니다. 숫자가 있어야 한다."""
        from dorothy.strategy.base import retired

        for name, reason in retired().items():
            self.assertIn("거래", reason, f"{name}: 거래 수가 없습니다")
            self.assertIn("%", reason, f"{name}: 수익률이 없습니다")

    def test_the_control_group_is_never_retired(self):
        """무작위 진입은 지는 게 정상이다. 폐기하면 비교 대상이 사라진다."""
        from dorothy.strategy.base import retired

        self.assertNotIn("random", retired())

    def test_available_includes_retired_by_default(self):
        from dorothy.strategy.base import available, retired

        everything = set(available())
        for name in retired():
            self.assertIn(name, everything)
