"""슬리피지 스윕-되찾기 판정 테스트.

세 조건을 **동시에** 요구해야 한다. 하나라도 느슨해지면 그냥 변동성 큰 봉이
전부 신호가 되어 아무 의미가 없어진다.
"""

import unittest

from dorothy.analysis.reclaim import Reclaim, ReclaimSpec, detect
from dorothy.models import Candle, Side


def bar(ts, o, h, l, c, v=1.0):
    return Candle(ts, o, h, l, c, v)


def history(n=60, price=100.0, volume=1.0):
    """평범한 배경. 저점은 99, 고점은 101."""
    return [bar(i * 3600_000, price, price + 1, price - 1, price, volume)
            for i in range(n)]


def sweep_bar(ts, *, low=95.0, close=100.5, open_=100.0, high=100.8, volume=3.0):
    return bar(ts, open_, high, low, close, volume)


class SpecTests(unittest.TestCase):
    def test_rejects_nonsense(self):
        for kwargs in ({"wick_ratio": 0.0}, {"wick_ratio": 1.0}, {"volume_window": 1}):
            with self.assertRaises(ValueError):
                ReclaimSpec(**kwargs)


class DetectTests(unittest.TestCase):
    def setUp(self):
        self.spec = ReclaimSpec()
        self.atr = 2.0

    def make(self, **kw):
        candles = history()
        candles.append(sweep_bar(60 * 3600_000, **kw))
        return candles

    def test_detects_the_full_pattern(self):
        found = detect(self.make(), 60, self.atr, self.spec)
        self.assertIsNotNone(found)
        self.assertIs(found.side, Side.LONG)
        self.assertGreater(found.volume_mult, 1.5)

    def test_needs_a_long_wick(self):
        """꼬리가 짧으면 슬리피지가 아니다."""
        self.assertIsNone(detect(self.make(low=99.6), 60, self.atr, self.spec))

    def test_needs_volume(self):
        """유동성이 안 들어왔으면 그냥 얇은 구간을 지나간 것이다."""
        self.assertIsNone(detect(self.make(volume=1.0), 60, self.atr, self.spec))

    def test_needs_to_close_above_open(self):
        """되찾지 못하면 그냥 하락이다."""
        self.assertIsNone(
            detect(self.make(open_=100.0, close=99.0, high=100.1), 60,
                   self.atr, self.spec))

    def test_needs_to_actually_break_the_prior_low(self):
        """직전 저점을 안 뚫었으면 스윕이 아니다."""
        candles = history()
        candles.append(bar(60 * 3600_000, 100.0, 100.8, 99.2, 100.5, 3.0))
        self.assertIsNone(detect(candles, 60, self.atr, self.spec))

    def test_needs_to_close_back_above_the_swept_level(self):
        """뚫고 못 돌아오면 되찾기가 아니다."""
        candles = history()
        candles.append(bar(60 * 3600_000, 100.0, 100.2, 95.0, 98.5, 3.0))
        self.assertIsNone(detect(candles, 60, self.atr, self.spec))

    def test_short_side_is_mirrored(self):
        candles = history()
        candles.append(bar(60 * 3600_000, 100.0, 105.0, 99.2, 99.5, 3.0))
        found = detect(candles, 60, self.atr, self.spec, side=Side.SHORT)
        self.assertIsNotNone(found)
        self.assertIs(found.side, Side.SHORT)

    def test_wick_is_measured_against_atr_too(self):
        """비율만 보면 작은 봉의 긴 꼬리가 통과한다. 절대 크기도 봐야 한다."""
        candles = history()
        candles.append(bar(60 * 3600_000, 100.0, 100.05, 99.8, 100.02, 3.0))
        self.assertIsNone(detect(candles, 60, 2.0, self.spec))

    def test_zero_atr_is_rejected(self):
        self.assertIsNone(detect(self.make(), 60, 0.0, self.spec))

    def test_flat_bar_is_rejected(self):
        candles = history()
        candles.append(bar(60 * 3600_000, 100, 100, 100, 100, 5.0))
        self.assertIsNone(detect(candles, 60, self.atr, self.spec))

    def test_too_early_returns_none(self):
        self.assertIsNone(detect(self.make(), 5, self.atr, self.spec))

    def test_out_of_range_returns_none(self):
        candles = self.make()
        self.assertIsNone(detect(candles, len(candles), self.atr, self.spec))


class CausalityTests(unittest.TestCase):
    """가장 중요한 것. 이후 봉을 봐서는 안 된다."""

    def test_future_bars_do_not_change_the_verdict(self):
        candles = history()
        candles.append(sweep_bar(60 * 3600_000))
        alone = detect(candles, 60, 2.0, ReclaimSpec())

        extended = candles + [bar((61 + i) * 3600_000, 200, 210, 190, 205, 99.0)
                              for i in range(10)]
        with_future = detect(extended, 60, 2.0, ReclaimSpec())

        self.assertEqual(alone is None, with_future is None)
        if alone is not None:
            self.assertEqual(alone.wick_ratio, with_future.wick_ratio)
            self.assertEqual(alone.volume_mult, with_future.volume_mult)

    def test_volume_average_excludes_the_signal_bar(self):
        """신호봉 거래량을 평균에 넣으면 배수가 희석되어 판정이 느슨해진다."""
        candles = history(volume=1.0)
        candles.append(sweep_bar(60 * 3600_000, volume=1.6))
        found = detect(candles, 60, 2.0, ReclaimSpec(volume_mult=1.55))
        self.assertIsNotNone(found, "신호봉이 평균에 섞여 배수가 낮아졌습니다")
        self.assertAlmostEqual(found.volume_mult, 1.6, places=6)


if __name__ == "__main__":
    unittest.main()
