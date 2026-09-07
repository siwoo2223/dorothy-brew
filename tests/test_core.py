import unittest

from dorothy_brew.core import (Candle, Series, blend, curvature, fib_level, line_at,
                               line_through, linreg, near, ratio_score)
from dorothy_brew import synth


def mk(prices, volume=100.0):
    return Series([Candle(i * 60, p, p + 1, p - 1, p, volume) for i, p in enumerate(prices)])


class TestMath(unittest.TestCase):
    def test_linreg_recovers_a_line(self):
        slope, intercept, r2 = linreg([1, 3, 5, 7, 9])
        self.assertAlmostEqual(slope, 2.0)
        self.assertAlmostEqual(intercept, 1.0)
        self.assertAlmostEqual(r2, 1.0)

    def test_linreg_flat(self):
        slope, _, r2 = linreg([5, 5, 5, 5])
        self.assertAlmostEqual(slope, 0.0)
        self.assertAlmostEqual(r2, 0.0)

    def test_curvature_sign(self):
        u = [(x - 5) ** 2 for x in range(11)]
        self.assertGreater(curvature(u), 0)
        self.assertLess(curvature([-v for v in u]), 0)

    def test_line_helpers(self):
        line = line_through((0, 10.0), (10, 20.0))
        self.assertAlmostEqual(line_at(line, 5), 15.0)

    def test_fib_level(self):
        self.assertAlmostEqual(fib_level(0.0, 100.0, 0.618), 38.2, places=6)
        self.assertAlmostEqual(fib_level(0.0, 100.0, -0.618), 161.8, places=6)

    def test_ratio_score_window_and_decay(self):
        self.assertEqual(ratio_score(0.886, 0.886, 0.886, 0.1), 1.0)
        self.assertGreater(ratio_score(0.92, 0.886, 0.886, 0.1), 0.0)
        self.assertEqual(ratio_score(2.0, 0.886, 0.886, 0.1), 0.0)

    def test_blend_is_weighted_and_clamped(self):
        self.assertAlmostEqual(blend((1.0, 1), (0.0, 1)), 0.5)
        self.assertAlmostEqual(blend((2.0, 1)), 1.0)
        self.assertAlmostEqual(blend(), 0.0)

    def test_near(self):
        self.assertTrue(near(100.0, 100.5, 0.01))
        self.assertFalse(near(100.0, 105.0, 0.01))


class TestSeries(unittest.TestCase):
    def test_candle_geometry(self):
        c = Candle(0, 10, 12, 9, 11, 5)
        self.assertEqual(c.body, 1)
        self.assertEqual(c.range, 3)
        self.assertTrue(c.bullish)
        self.assertEqual(c.upper_wick, 1)
        self.assertEqual(c.lower_wick, 1)

    def test_pivots_alternate(self):
        s = synth.double_bottom()
        piv = s.pivots(3, 3)
        self.assertGreater(len(piv), 3)
        for a, b in zip(piv, piv[1:]):
            self.assertNotEqual(a.kind, b.kind)
            self.assertLess(a.index, b.index)

    def test_atr_positive_and_zero_range(self):
        s = synth.bull_flag()
        self.assertGreater(s.atr(14), 0)
        flat = Series([Candle(i, 10, 10, 10, 10, 1) for i in range(30)])
        self.assertEqual(flat.atr(14), 0.0)

    def test_rsi_bounds(self):
        s = synth.random_walk(bars=120)
        self.assertTrue(0.0 <= s.rsi(14) <= 100.0)
        self.assertEqual(mk([float(i) for i in range(1, 31)]).rsi(14), 100.0)

    def test_slicing_keeps_identity(self):
        s = synth.bull_flag()
        sub = s[10:20]
        self.assertEqual(len(sub), 10)
        self.assertEqual(sub.symbol, s.symbol)

    def test_trend_labels(self):
        up = mk([100 + i for i in range(60)])
        self.assertEqual(up.trend(50), "up")
        self.assertEqual(mk([100 - i for i in range(60)]).trend(50), "down")
        self.assertEqual(mk([100.0] * 60).trend(50), "range")


if __name__ == "__main__":
    unittest.main()
