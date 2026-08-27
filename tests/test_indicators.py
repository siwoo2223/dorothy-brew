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


class TimeframeTableTests(unittest.TestCase):
    """타임프레임 표는 하나여야 한다.

    전에는 loader와 resample이 각자 표를 들고 있었고 서로 달랐다.
    loader에 6h·12h가 없고 resample에 8h가 없어서, 같은 이름이 한쪽에서는
    되고 한쪽에서는 안 됐다. 실제로 8h 리샘플을 시도하다 터졌다.
    """

    def test_loader_and_resample_share_one_function(self):
        from dorothy.data.loader import timeframe_ms as from_loader
        from dorothy.data.resample import timeframe_ms as from_resample

        self.assertIs(from_loader, from_resample)

    def test_every_name_resolves_in_both_modules(self):
        from dorothy.data.loader import timeframe_ms as from_loader
        from dorothy.data.resample import TIMEFRAME_MS
        from dorothy.data.resample import timeframe_ms as from_resample

        for name in TIMEFRAME_MS:
            self.assertEqual(from_loader(name), from_resample(name), name)

    def test_values_are_strictly_increasing_by_name_order(self):
        """표가 시간순으로 정렬돼 있어야 읽는 사람이 실수하지 않는다."""
        from dorothy.data.resample import TIMEFRAME_MS

        values = list(TIMEFRAME_MS.values())
        self.assertEqual(values, sorted(values))

    def test_funding_interval_is_available(self):
        """8시간은 펀딩 주기다. 이게 없어서 스윕이 터진 적이 있다."""
        from dorothy.data.resample import timeframe_ms

        self.assertEqual(timeframe_ms("8h"), 8 * 3600_000)

    def test_unknown_name_lists_what_is_available(self):
        from dorothy.data.resample import timeframe_ms

        with self.assertRaises(ValueError) as ctx:
            timeframe_ms("7h")
        self.assertIn("가능", str(ctx.exception))

    def test_each_value_is_a_whole_number_of_minutes(self):
        from dorothy.data.resample import TIMEFRAME_MS

        for name, ms in TIMEFRAME_MS.items():
            self.assertEqual(ms % 60_000, 0, f"{name}={ms}")

    def test_resampling_to_8h_produces_valid_candles(self):
        from dorothy.data.loader import synthetic
        from dorothy.data.resample import resample, timeframe_ms

        source = synthetic(4000, seed=3)
        step = source[1].ts - source[0].ts
        eight = resample(source, timeframe_ms("8h"))

        # 기댓값을 상수로 박지 않는다. synthetic의 기본 간격이 바뀌면
        # 테스트가 조용히 무의미해지기 때문이다.
        expected = len(source) * step // timeframe_ms("8h")
        self.assertGreaterEqual(len(eight), expected - 2)
        self.assertLessEqual(len(eight), expected)
        self.assertGreater(len(eight), 5, "표본이 너무 적어 검사가 무의미합니다")

        for c in eight:
            self.assertLessEqual(c.low, min(c.open, c.close))
            self.assertGreaterEqual(c.high, max(c.open, c.close))
