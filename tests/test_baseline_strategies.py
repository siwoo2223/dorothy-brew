"""돈치안 / 평균회귀 / 무작위 대조군 테스트."""

import unittest

from dorothy.data.loader import synthetic
from dorothy.models import Action, Candle, Position, Side
from dorothy.strategy.base import available, get_strategy
from dorothy.strategy.common import entry_signal
from dorothy.strategy.random_entry import _unit_random

CANDLES = synthetic(3000, seed=5, timeframe="15m")


def entries(strategy, candles, step=1):
    out = []
    for i in range(strategy.warmup, len(candles), step):
        sig = strategy.generate(candles[: i + 1], None)
        if sig.action is not Action.HOLD:
            out.append((i, sig))
    return out


class TestRegistry(unittest.TestCase):
    """자동 탐색이 동작하는지 본다.

    전략 목록 전체를 하드코딩하면 전략을 추가할 때마다 이 테스트가 깨진다
    (실제로 세 번 깨졌다). 그건 자동 탐색을 도입한 취지에 어긋난다.
    대신 '반드시 있어야 할 것'과 '탐색된 것이 모두 정상인가'를 본다.
    """

    REQUIRED = {"donchian", "ema_cross", "random"}

    def test_core_strategies_are_discovered(self):
        self.assertTrue(self.REQUIRED <= set(available()))

    def test_control_group_is_always_available(self):
        """대조군이 사라지면 비교의 근거가 사라진다."""
        self.assertIn("random", available())

    def test_every_discovered_strategy_can_be_built(self):
        for name in available():
            with self.subTest(strategy=name):
                self.assertIsNotNone(get_strategy(name))

    def test_every_strategy_registers_under_its_own_name(self):
        for name in available():
            with self.subTest(strategy=name):
                self.assertEqual(get_strategy(name).name, name)

    def test_every_strategy_declares_a_warmup(self):
        for name in available():
            with self.subTest(strategy=name):
                self.assertGreater(get_strategy(name).warmup, 0)


class TestSharedEntryHelper(unittest.TestCase):
    def test_long_stop_below_target_above(self):
        sig = entry_signal(
            long=True, price=100.0, atr=2.0, stop_mult=2.0, target_mult=3.0, reason="t"
        )
        self.assertIs(sig.action, Action.ENTER_LONG)
        self.assertAlmostEqual(sig.stop_loss, 96.0)
        self.assertAlmostEqual(sig.take_profit, 106.0)

    def test_short_stop_above_target_below(self):
        sig = entry_signal(
            long=False, price=100.0, atr=2.0, stop_mult=2.0, target_mult=3.0, reason="t"
        )
        self.assertIs(sig.action, Action.ENTER_SHORT)
        self.assertAlmostEqual(sig.stop_loss, 104.0)
        self.assertAlmostEqual(sig.take_profit, 94.0)


class TestDonchian(unittest.TestCase):
    def setUp(self):
        self.strategy = get_strategy("donchian", channel=10, exit_channel=5)

    def test_breakout_above_channel_goes_long(self):
        flat = [Candle(i * 1000, 100, 101, 99, 100, 1.0) for i in range(30)]
        breakout = flat + [Candle(30_000, 100, 110, 100, 109, 1.0)]
        sig = self.strategy.generate(breakout, None)
        self.assertIs(sig.action, Action.ENTER_LONG)
        self.assertLess(sig.stop_loss, 109)

    def test_breakdown_below_channel_goes_short(self):
        flat = [Candle(i * 1000, 100, 101, 99, 100, 1.0) for i in range(30)]
        breakdown = flat + [Candle(30_000, 100, 100, 90, 91, 1.0)]
        self.assertIs(self.strategy.generate(breakdown, None).action, Action.ENTER_SHORT)

    def test_inside_channel_holds(self):
        flat = [Candle(i * 1000, 100, 105, 95, 100, 1.0) for i in range(30)]
        self.assertIs(self.strategy.generate(flat, None).action, Action.HOLD)

    def test_current_bar_is_excluded_from_the_channel(self):
        """현재 봉을 채널에 포함하면 자기 자신이 항상 최고가라 돌파가 성립하지 않는다."""
        flat = [Candle(i * 1000, 100, 101, 99, 100, 1.0) for i in range(30)]
        breakout = flat + [Candle(30_000, 100, 110, 100, 109, 1.0)]
        self.assertIsNot(self.strategy.generate(breakout, None).action, Action.HOLD)

    def test_channel_must_be_at_least_two(self):
        with self.assertRaises(ValueError):
            get_strategy("donchian", channel=1)

    def test_short_can_be_disabled(self):
        strategy = get_strategy("donchian", channel=10, allow_short=False)
        for _, sig in entries(strategy, CANDLES, step=3):
            self.assertIsNot(sig.action, Action.ENTER_SHORT)


class TestRandomControl(unittest.TestCase):
    """대조군은 재현 가능해야 대조군이다."""

    def test_same_seed_gives_identical_signals(self):
        a = get_strategy("random", seed=7)
        b = get_strategy("random", seed=7)
        for i in range(a.warmup, 800, 3):
            self.assertIs(
                a.generate(CANDLES[: i + 1], None).action,
                b.generate(CANDLES[: i + 1], None).action,
            )

    def test_different_seeds_give_different_signals(self):
        a = get_strategy("random", seed=1)
        b = get_strategy("random", seed=2)
        sig_a = [a.generate(CANDLES[: i + 1], None).action for i in range(a.warmup, 900)]
        sig_b = [b.generate(CANDLES[: i + 1], None).action for i in range(b.warmup, 900)]
        self.assertNotEqual(sig_a, sig_b)

    def test_entry_rate_approximates_the_probability(self):
        strategy = get_strategy("random", entry_probability=0.1, seed=99)
        bars = range(strategy.warmup, len(CANDLES))
        hits = sum(
            1 for i in bars if strategy.generate(CANDLES[: i + 1], None).action is not Action.HOLD
        )
        rate = hits / len(list(bars))
        self.assertGreater(rate, 0.05)
        self.assertLess(rate, 0.16)

    def test_random_is_uniform_enough(self):
        values = [_unit_random(1, ts) for ts in range(2000)]
        self.assertTrue(all(0.0 <= v < 1.0 for v in values))
        self.assertAlmostEqual(sum(values) / len(values), 0.5, delta=0.05)

    def test_probability_must_be_valid(self):
        with self.assertRaises(ValueError):
            get_strategy("random", entry_probability=0.0)
        with self.assertRaises(ValueError):
            get_strategy("random", entry_probability=1.5)

    def test_control_does_not_peek_at_the_future(self):
        """대조군도 인과성을 지켜야 한다. 아니면 비교가 불공정해진다."""
        strategy = get_strategy("random", seed=5)
        cut = 1000
        tampered = CANDLES[:cut] + [
            Candle(c.ts, c.open * 3, c.high * 3, c.low * 3, c.close * 3, c.volume)
            for c in CANDLES[cut:]
        ]
        for i in (800, 950, cut - 1):
            self.assertIs(
                strategy.generate(CANDLES[: i + 1], None).action,
                strategy.generate(tampered[: i + 1], None).action,
            )


if __name__ == "__main__":
    unittest.main()


class BoundedWindowTests(unittest.TestCase):
    """분석 창을 제한해도 값이 바뀌면 안 된다.

    매 봉마다 전체 히스토리로 지표를 다시 계산하면 O(n²)가 되어 8년치
    시간봉에서 백테스트가 사실상 멈춘다. 그래서 최근 구간만 본다.
    **속도를 위해 값을 바꾸면 그건 최적화가 아니라 버그다.**
    """

    def candles(self, n=3000):
        from dorothy.data.loader import synthetic
        return synthetic(n, seed=17)

    def signals(self, strategy, candles, limit=None):
        from dorothy.models import Action
        out = []
        stop = limit or len(candles)
        for i in range(strategy.warmup, stop):
            sig = strategy.generate(candles[: i + 1], None)
            if sig.action is not Action.HOLD:
                out.append((i, sig.action,
                            round(sig.stop_loss, 6) if sig.stop_loss else None,
                            round(sig.take_profit, 6) if sig.take_profit else None))
        return out

    def test_ema_cross_signals_match_a_short_history(self):
        """긴 히스토리에서 낸 신호가, 같은 지점을 짧은 히스토리로 봐도 같아야 한다."""
        from dorothy.strategy.base import get_strategy

        candles = self.candles()
        strategy = get_strategy("ema_cross", fast=20, slow=50)
        index = len(candles) - 1

        full = strategy.generate(candles, None)
        # 창(slow*20=1000)보다 넉넉히 긴 꼬리만 남겨도 같은 답이어야 한다
        trimmed = strategy.generate(candles[-1500:], None)
        self.assertEqual(full.action, trimmed.action)
        if full.stop_loss and trimmed.stop_loss:
            self.assertAlmostEqual(full.stop_loss, trimmed.stop_loss, places=6)

    def test_ema_cross_is_not_quadratic(self):
        """봉 수를 4배로 늘려도 봉당 처리 시간이 크게 늘면 안 된다.

        O(n²)면 봉당 시간이 n에 비례해 늘어난다. 경계가 있으면 일정하다.
        """
        import time

        from dorothy.strategy.base import get_strategy

        candles = self.candles(4000)
        strategy = get_strategy("ema_cross", fast=20, slow=50)

        def per_bar(count):
            sample = candles[:count]
            start = max(strategy.warmup, count - 120)
            t0 = time.perf_counter()
            for i in range(start, count):
                strategy.generate(sample[: i + 1], None)
            return (time.perf_counter() - t0) / (count - start)

        small = per_bar(1000)
        large = per_bar(4000)
        # 4배 늘렸는데 봉당 시간이 2.5배를 넘으면 경계가 안 걸린 것이다
        self.assertLess(large, small * 2.5,
                        f"봉당 {small * 1e6:.0f}µs → {large * 1e6:.0f}µs — O(n²) 의심")

"""돈치안 / 평균회귀 / 무작위 대조군 테스트."""

import unittest

from dorothy.data.loader import synthetic
from dorothy.models import Action, Candle, Position, Side
from dorothy.strategy.base import available, get_strategy
from dorothy.strategy.common import entry_signal
from dorothy.strategy.random_entry import _unit_random

CANDLES = synthetic(3000, seed=5, timeframe="15m")


def entries(strategy, candles, step=1):
    out = []
    for i in range(strategy.warmup, len(candles), step):
        sig = strategy.generate(candles[: i + 1], None)
        if sig.action is not Action.HOLD:
            out.append((i, sig))
    return out


class TestRegistry(unittest.TestCase):
    """자동 탐색이 동작하는지 본다.

    전략 목록 전체를 하드코딩하면 전략을 추가할 때마다 이 테스트가 깨진다
    (실제로 세 번 깨졌다). 그건 자동 탐색을 도입한 취지에 어긋난다.
    대신 '반드시 있어야 할 것'과 '탐색된 것이 모두 정상인가'를 본다.
    """

    REQUIRED = {"donchian", "ema_cross", "random"}

    def test_core_strategies_are_discovered(self):
        self.assertTrue(self.REQUIRED <= set(available()))

    def test_control_group_is_always_available(self):
        """대조군이 사라지면 비교의 근거가 사라진다."""
        self.assertIn("random", available())

    def test_every_discovered_strategy_can_be_built(self):
        for name in available():
            with self.subTest(strategy=name):
                self.assertIsNotNone(get_strategy(name))

    def test_every_strategy_registers_under_its_own_name(self):
        for name in available():
            with self.subTest(strategy=name):
                self.assertEqual(get_strategy(name).name, name)

    def test_every_strategy_declares_a_warmup(self):
        for name in available():
            with self.subTest(strategy=name):
                self.assertGreater(get_strategy(name).warmup, 0)


class TestSharedEntryHelper(unittest.TestCase):
    def test_long_stop_below_target_above(self):
        sig = entry_signal(
            long=True, price=100.0, atr=2.0, stop_mult=2.0, target_mult=3.0, reason="t"
        )
        self.assertIs(sig.action, Action.ENTER_LONG)
        self.assertAlmostEqual(sig.stop_loss, 96.0)
        self.assertAlmostEqual(sig.take_profit, 106.0)

    def test_short_stop_above_target_below(self):
        sig = entry_signal(
            long=False, price=100.0, atr=2.0, stop_mult=2.0, target_mult=3.0, reason="t"
        )
        self.assertIs(sig.action, Action.ENTER_SHORT)
        self.assertAlmostEqual(sig.stop_loss, 104.0)
        self.assertAlmostEqual(sig.take_profit, 94.0)


class TestDonchian(unittest.TestCase):
    def setUp(self):
        self.strategy = get_strategy("donchian", channel=10, exit_channel=5)

    def test_breakout_above_channel_goes_long(self):
        flat = [Candle(i * 1000, 100, 101, 99, 100, 1.0) for i in range(30)]
        breakout = flat + [Candle(30_000, 100, 110, 100, 109, 1.0)]
        sig = self.strategy.generate(breakout, None)
        self.assertIs(sig.action, Action.ENTER_LONG)
        self.assertLess(sig.stop_loss, 109)

    def test_breakdown_below_channel_goes_short(self):
        flat = [Candle(i * 1000, 100, 101, 99, 100, 1.0) for i in range(30)]
        breakdown = flat + [Candle(30_000, 100, 100, 90, 91, 1.0)]
        self.assertIs(self.strategy.generate(breakdown, None).action, Action.ENTER_SHORT)

    def test_inside_channel_holds(self):
        flat = [Candle(i * 1000, 100, 105, 95, 100, 1.0) for i in range(30)]
        self.assertIs(self.strategy.generate(flat, None).action, Action.HOLD)

    def test_current_bar_is_excluded_from_the_channel(self):
        """현재 봉을 채널에 포함하면 자기 자신이 항상 최고가라 돌파가 성립하지 않는다."""
        flat = [Candle(i * 1000, 100, 101, 99, 100, 1.0) for i in range(30)]
        breakout = flat + [Candle(30_000, 100, 110, 100, 109, 1.0)]
        self.assertIsNot(self.strategy.generate(breakout, None).action, Action.HOLD)

    def test_channel_must_be_at_least_two(self):
        with self.assertRaises(ValueError):
            get_strategy("donchian", channel=1)

    def test_short_can_be_disabled(self):
        strategy = get_strategy("donchian", channel=10, allow_short=False)
        for _, sig in entries(strategy, CANDLES, step=3):
            self.assertIsNot(sig.action, Action.ENTER_SHORT)


class TestRandomControl(unittest.TestCase):
    """대조군은 재현 가능해야 대조군이다."""

    def test_same_seed_gives_identical_signals(self):
        a = get_strategy("random", seed=7)
        b = get_strategy("random", seed=7)
        for i in range(a.warmup, 800, 3):
            self.assertIs(
                a.generate(CANDLES[: i + 1], None).action,
                b.generate(CANDLES[: i + 1], None).action,
            )

    def test_different_seeds_give_different_signals(self):
        a = get_strategy("random", seed=1)
        b = get_strategy("random", seed=2)
        sig_a = [a.generate(CANDLES[: i + 1], None).action for i in range(a.warmup, 900)]
        sig_b = [b.generate(CANDLES[: i + 1], None).action for i in range(b.warmup, 900)]
        self.assertNotEqual(sig_a, sig_b)

    def test_entry_rate_approximates_the_probability(self):
        strategy = get_strategy("random", entry_probability=0.1, seed=99)
        bars = range(strategy.warmup, len(CANDLES))
        hits = sum(
            1 for i in bars if strategy.generate(CANDLES[: i + 1], None).action is not Action.HOLD
        )
        rate = hits / len(list(bars))
        self.assertGreater(rate, 0.05)
        self.assertLess(rate, 0.16)

    def test_random_is_uniform_enough(self):
        values = [_unit_random(1, ts) for ts in range(2000)]
        self.assertTrue(all(0.0 <= v < 1.0 for v in values))
        self.assertAlmostEqual(sum(values) / len(values), 0.5, delta=0.05)

    def test_probability_must_be_valid(self):
        with self.assertRaises(ValueError):
            get_strategy("random", entry_probability=0.0)
        with self.assertRaises(ValueError):
            get_strategy("random", entry_probability=1.5)

    def test_control_does_not_peek_at_the_future(self):
        """대조군도 인과성을 지켜야 한다. 아니면 비교가 불공정해진다."""
        strategy = get_strategy("random", seed=5)
        cut = 1000
        tampered = CANDLES[:cut] + [
            Candle(c.ts, c.open * 3, c.high * 3, c.low * 3, c.close * 3, c.volume)
            for c in CANDLES[cut:]
        ]
        for i in (800, 950, cut - 1):
            self.assertIs(
                strategy.generate(CANDLES[: i + 1], None).action,
                strategy.generate(tampered[: i + 1], None).action,
            )


if __name__ == "__main__":
    unittest.main()


class BoundedWindowTests(unittest.TestCase):
    """분석 창을 제한해도 값이 바뀌면 안 된다.

    매 봉마다 전체 히스토리로 지표를 다시 계산하면 O(n²)가 되어 8년치
    시간봉에서 백테스트가 사실상 멈춘다. 그래서 최근 구간만 본다.
    **속도를 위해 값을 바꾸면 그건 최적화가 아니라 버그다.**
    """

    def candles(self, n=3000):
        from dorothy.data.loader import synthetic
        return synthetic(n, seed=17)

    def signals(self, strategy, candles, limit=None):
        from dorothy.models import Action
        out = []
        stop = limit or len(candles)
        for i in range(strategy.warmup, stop):
            sig = strategy.generate(candles[: i + 1], None)
            if sig.action is not Action.HOLD:
                out.append((i, sig.action,
                            round(sig.stop_loss, 6) if sig.stop_loss else None,
                            round(sig.take_profit, 6) if sig.take_profit else None))
        return out

    def test_ema_cross_signals_match_a_short_history(self):
        """긴 히스토리에서 낸 신호가, 같은 지점을 짧은 히스토리로 봐도 같아야 한다."""
        from dorothy.strategy.base import get_strategy

        candles = self.candles()
        strategy = get_strategy("ema_cross", fast=20, slow=50)
        index = len(candles) - 1

        full = strategy.generate(candles, None)
        # 창(slow*20=1000)보다 넉넉히 긴 꼬리만 남겨도 같은 답이어야 한다
        trimmed = strategy.generate(candles[-1500:], None)
        self.assertEqual(full.action, trimmed.action)
        if full.stop_loss and trimmed.stop_loss:
            self.assertAlmostEqual(full.stop_loss, trimmed.stop_loss, places=6)

    def test_ema_cross_is_not_quadratic(self):
        """봉 수를 4배로 늘려도 봉당 처리 시간이 크게 늘면 안 된다.

        O(n²)면 봉당 시간이 n에 비례해 늘어난다. 경계가 있으면 일정하다.
        """
        import time

        from dorothy.strategy.base import get_strategy

        candles = self.candles(4000)
        strategy = get_strategy("ema_cross", fast=20, slow=50)

        def per_bar(count):
            sample = candles[:count]
            start = max(strategy.warmup, count - 120)
            t0 = time.perf_counter()
            for i in range(start, count):
                strategy.generate(sample[: i + 1], None)
            return (time.perf_counter() - t0) / (count - start)

        small = per_bar(1000)
        large = per_bar(4000)
        # 4배 늘렸는데 봉당 시간이 2.5배를 넘으면 경계가 안 걸린 것이다
        self.assertLess(large, small * 2.5,
                        f"봉당 {small * 1e6:.0f}µs → {large * 1e6:.0f}µs — O(n²) 의심")
