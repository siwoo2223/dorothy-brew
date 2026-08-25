import unittest

from dorothy.data.indicators import atr, ema, rsi, sma


class TestIndicators(unittest.TestCase):
    def test_sma_pads_front_with_none(self):
        out = sma([1, 2, 3, 4, 5], 3)
        self.assertEqual(out[:2], [None, None])
        self.assertAlmostEqual(out[2], 2.0)
        self.assertAlmostEqual(out[4], 4.0)
        self.assertEqual(len(out), 5)

    def test_ema_seeds_with_sma(self):
        values = [1.0] * 10
        out = ema(values, 5)
        self.assertIsNone(out[3])
        self.assertAlmostEqual(out[4], 1.0)
        self.assertAlmostEqual(out[-1], 1.0)

    def test_ema_reacts_faster_than_sma(self):
        values = [10.0] * 20 + [20.0] * 5
        self.assertGreater(ema(values, 10)[-1], sma(values, 10)[-1])

    def test_ema_returns_all_none_when_too_short(self):
        self.assertEqual(ema([1, 2], 5), [None] * 2)

    def test_atr_is_positive_and_padded(self):
        n = 40
        highs = [10 + i * 0.1 for i in range(n)]
        lows = [9 + i * 0.1 for i in range(n)]
        closes = [9.5 + i * 0.1 for i in range(n)]
        out = atr(highs, lows, closes, 14)
        self.assertIsNone(out[13])
        self.assertIsNotNone(out[14])
        self.assertGreater(out[-1], 0)

    def test_rsi_bounds(self):
        values = [float(i) for i in range(1, 60)]
        out = rsi(values, 14)
        self.assertAlmostEqual(out[-1], 100.0)   # 단조 상승이면 100
        for v in out:
            if v is not None:
                self.assertGreaterEqual(v, 0.0)
                self.assertLessEqual(v, 100.0)

    def test_invalid_period_raises(self):
        with self.assertRaises(ValueError):
            sma([1, 2, 3], 0)


if __name__ == "__main__":
    unittest.main()
