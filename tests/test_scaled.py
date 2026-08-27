"""분할 주문 테스트.

핵심은 **부분 체결**이다. 단일 지정가는 전부 잡히거나 전부 놓치거나인데,
분할 주문은 되돌림 깊이에 비례해 수량이 정해져야 한다.
그게 안 되면 분할할 이유가 없다.
"""

import unittest

from dorothy.execution.scaled import ScaledFill, ScaleSpec, simulate_scaled_entry
from dorothy.models import Candle, Side


def bar(ts, o, h, l, c, v=1.0):
    return Candle(ts, o, h, l, c, v)


class SpecTests(unittest.TestCase):
    def test_rejects_nonsense(self):
        with self.assertRaises(ValueError):
            ScaleSpec(orders=0)
        with self.assertRaises(ValueError):
            ScaleSpec(depth_atr=-1.0)

    def test_long_prices_go_down(self):
        prices = ScaleSpec(orders=5, depth_atr=1.0).prices(100.0, Side.LONG, 4.0)
        self.assertEqual(len(prices), 5)
        self.assertEqual(prices, sorted(prices, reverse=True))
        self.assertAlmostEqual(prices[0], 100.0)      # include_touch
        self.assertAlmostEqual(prices[-1], 96.0)      # 1 ATR 아래

    def test_short_prices_go_up(self):
        prices = ScaleSpec(orders=5, depth_atr=1.0).prices(100.0, Side.SHORT, 4.0)
        self.assertEqual(prices, sorted(prices))
        self.assertAlmostEqual(prices[0], 100.0)
        self.assertAlmostEqual(prices[-1], 104.0)

    def test_without_touch_nothing_sits_at_the_signal_price(self):
        prices = ScaleSpec(orders=4, depth_atr=1.0, include_touch=False).prices(
            100.0, Side.LONG, 4.0)
        self.assertTrue(all(p < 100.0 for p in prices), prices)

    def test_single_order_reduces_to_one_price(self):
        self.assertEqual(len(ScaleSpec(orders=1).prices(100.0, Side.LONG, 4.0)), 1)

    def test_prices_are_evenly_spaced(self):
        prices = ScaleSpec(orders=5, depth_atr=1.0).prices(100.0, Side.LONG, 4.0)
        gaps = [round(a - b, 9) for a, b in zip(prices, prices[1:])]
        self.assertEqual(len(set(gaps)), 1, gaps)


class FillTests(unittest.TestCase):
    def spec(self, **kw):
        return ScaleSpec(**{"orders": 5, "depth_atr": 1.0, "timeout_bars": 3, **kw})

    def test_shallow_pullback_fills_only_the_top(self):
        """조금만 되돌아오면 위쪽 몇 개만 채워져야 한다. 이게 분할의 핵심이다."""
        candles = [bar(0, 100, 100, 100, 100), bar(1, 100, 101, 99, 100)]
        fill = simulate_scaled_entry(candles, 0, Side.LONG, 100.0, 4.0, self.spec())
        self.assertGreater(fill.filled, 0)
        self.assertLess(fill.filled, 5)

    def test_deep_pullback_fills_everything(self):
        candles = [bar(0, 100, 100, 100, 100), bar(1, 100, 101, 95, 96)]
        fill = simulate_scaled_entry(candles, 0, Side.LONG, 100.0, 4.0, self.spec())
        self.assertEqual(fill.filled, 5)
        self.assertAlmostEqual(fill.ratio, 1.0)

    def test_runaway_still_fills_the_touch_order(self):
        """되돌림 없이 그대로 올라가도 신호가에 붙인 첫 주문은 잡힌다.

        다음 봉은 보통 직전 종가 근처에서 열리므로 저가가 신호가를 스친다.
        단일 지정가를 아래에 두면 이 경우 **전부 놓친다** — 그게 앞선
        메이커 검증에서 '놓친 신호가 5배 좋았던' 이유다.
        """
        candles = [bar(0, 100, 100, 100, 100)]
        price = 100.0
        for i in range(1, 5):
            candles.append(bar(i, price, price + 6, price, price + 5))
            price += 5
        fill = simulate_scaled_entry(candles, 0, Side.LONG, 100.0, 4.0, self.spec())
        self.assertEqual(fill.filled, 1)
        self.assertAlmostEqual(fill.avg_price, 100.0)

    def test_single_limit_below_misses_the_same_runaway(self):
        """같은 급등에서 단일 지정가(아래)는 아무것도 못 잡는다.
        분할이 해결하려는 문제가 정확히 이것이다."""
        candles = [bar(0, 100, 100, 100, 100)]
        price = 100.0
        for i in range(1, 5):
            candles.append(bar(i, price, price + 6, price, price + 5))
            price += 5
        alone = simulate_scaled_entry(candles, 0, Side.LONG, 100.0, 4.0,
                                      self.spec(orders=1, include_touch=False))
        self.assertEqual(alone.filled, 0)

    def test_a_gap_up_misses_orders_placed_below(self):
        candles = [bar(0, 100, 100, 100, 100), bar(1, 120, 125, 119, 124)]
        fill = simulate_scaled_entry(candles, 0, Side.LONG, 100.0, 4.0,
                                     self.spec(include_touch=False))
        self.assertEqual(fill.filled, 0)

    def test_deeper_pullback_fills_more(self):
        shallow = [bar(0, 100, 100, 100, 100), bar(1, 100, 101, 99.5, 100)]
        deeper = [bar(0, 100, 100, 100, 100), bar(1, 100, 101, 97.5, 98)]
        a = simulate_scaled_entry(shallow, 0, Side.LONG, 100.0, 4.0, self.spec())
        b = simulate_scaled_entry(deeper, 0, Side.LONG, 100.0, 4.0, self.spec())
        self.assertLess(a.filled, b.filled)

    def test_never_fills_on_the_signal_bar(self):
        """신호봉은 이미 끝났다. 거기서 체결되면 미래참조다."""
        candles = [bar(0, 100, 120, 80, 100), bar(1, 100, 100.1, 99.99, 100)]
        fill = simulate_scaled_entry(candles, 0, Side.LONG, 100.0, 4.0,
                                     self.spec(include_touch=False))
        self.assertEqual(fill.filled, 0)

    def test_timeout_stops_further_fills(self):
        candles = [bar(0, 100, 100, 100, 100)]
        candles += [bar(i, 100, 101, 99.9, 100) for i in range(1, 4)]
        candles.append(bar(4, 100, 101, 90, 91))       # 대기 끝난 뒤 급락
        fill = simulate_scaled_entry(candles, 0, Side.LONG, 100.0, 4.0,
                                     self.spec(timeout_bars=3))
        self.assertLess(fill.filled, 5)

    def test_each_order_fills_at_most_once(self):
        candles = [bar(0, 100, 100, 100, 100)] + [
            bar(i, 100, 101, 95, 100) for i in range(1, 4)
        ]
        fill = simulate_scaled_entry(candles, 0, Side.LONG, 100.0, 4.0, self.spec())
        self.assertEqual(fill.filled, 5)
        self.assertEqual(len(fill.prices), 5)

    def test_short_side_mirrored(self):
        candles = [bar(0, 100, 100, 100, 100), bar(1, 100, 105, 99, 104)]
        fill = simulate_scaled_entry(candles, 0, Side.SHORT, 100.0, 4.0, self.spec())
        self.assertEqual(fill.filled, 5)

    def test_no_atr_means_no_orders(self):
        candles = [bar(0, 100, 100, 100, 100), bar(1, 100, 101, 95, 96)]
        fill = simulate_scaled_entry(candles, 0, Side.LONG, 100.0, 0.0, self.spec())
        self.assertEqual(fill.filled, 0)

    def test_no_bars_after_signal(self):
        fill = simulate_scaled_entry([bar(0, 100, 100, 100, 100)], 0, Side.LONG,
                                     100.0, 4.0, self.spec())
        self.assertEqual(fill.filled, 0)


class FillResultTests(unittest.TestCase):
    def test_ratio_and_average(self):
        fill = ScaledFill(filled=2, total=5, prices=[100.0, 98.0])
        self.assertAlmostEqual(fill.ratio, 0.4)
        self.assertAlmostEqual(fill.avg_price, 99.0)

    def test_empty_is_safe(self):
        fill = ScaledFill(total=5)
        self.assertEqual(fill.ratio, 0.0)
        self.assertIsNone(fill.avg_price)
        self.assertFalse(fill.any_filled)

    def test_average_beats_the_signal_price_for_a_long(self):
        """분할이 의미가 있으려면 평균 진입가가 신호가보다 좋아야 한다."""
        fill = ScaledFill(filled=3, total=5, prices=[100.0, 99.0, 98.0])
        self.assertLess(fill.avg_price, 100.0)


if __name__ == "__main__":
    unittest.main()
