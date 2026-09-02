"""손절이 먼저인가 청산이 먼저인가.

**이 파일이 막는 사고:**
리스크 사이징을 쓰면 배율이 수량을 안 바꾼다. 그래서 "배율은 상관없다"고
착각하기 쉽다. 배율은 증거금을 정하고, 증거금이 청산가를 정한다.
손절폭이 청산 거리보다 멀면 **손절은 영원히 발동하지 않는다.**
"""

import unittest

from dorothy.models import Candle
from dorothy.risk.liquidation import (
    LeverageCheck,
    analyse,
    liquidation_distance,
    max_safe_leverage,
    render,
    stop_distances,
)


class LiquidationDistanceTests(unittest.TestCase):
    def test_ten_times_liquidates_about_ten_percent_away(self):
        self.assertAlmostEqual(liquidation_distance(10, 0.005), 0.095, places=9)

    def test_higher_leverage_moves_liquidation_closer(self):
        ds = [liquidation_distance(L) for L in (1, 2, 5, 10, 50)]
        self.assertEqual(ds, sorted(ds, reverse=True))

    def test_maintenance_margin_eats_into_the_distance(self):
        self.assertLess(liquidation_distance(10, 0.02), liquidation_distance(10, 0.005))

    def test_extreme_leverage_never_goes_negative(self):
        self.assertGreaterEqual(liquidation_distance(500, 0.005), 0.0)

    def test_zero_leverage_is_rejected(self):
        with self.assertRaises(ValueError):
            liquidation_distance(0)


class MaxSafeLeverageTests(unittest.TestCase):
    def test_a_wider_stop_forces_lower_leverage(self):
        self.assertGreater(max_safe_leverage(0.01), max_safe_leverage(0.05))

    def test_the_answer_actually_satisfies_the_safety_margin(self):
        """되돌려 확인한다 — 나온 배율에서 청산이 손절의 safety배 이상 멀어야."""
        for stop in (0.005, 0.02, 0.05, 0.15):
            for safety in (1.5, 2.0, 3.0):
                lev = max_safe_leverage(stop, safety=safety)
                self.assertGreaterEqual(
                    liquidation_distance(lev) + 1e-12, stop * safety,
                    f"손절 {stop} safety {safety} → {lev}배가 여유를 못 지킵니다",
                )

    def test_a_safety_below_one_is_rejected(self):
        with self.assertRaises(ValueError):
            max_safe_leverage(0.02, safety=0.5)

    def test_a_zero_stop_is_rejected(self):
        with self.assertRaises(ValueError):
            max_safe_leverage(0.0)

    def test_an_impossibly_wide_stop_falls_back_to_one(self):
        self.assertEqual(max_safe_leverage(0.9, safety=2.0), 1.0)


class LeverageCheckTests(unittest.TestCase):
    def test_no_trade_is_unsafe_at_low_leverage(self):
        c = LeverageCheck(2.0, liquidation_distance(2), [0.01, 0.02, 0.03])
        self.assertEqual(c.unsafe_share, 0.0)
        self.assertIn("✓", c.verdict)

    def test_a_stop_wider_than_liquidation_is_counted(self):
        c = LeverageCheck(20.0, liquidation_distance(20), [0.01, 0.02, 0.06, 0.10])
        self.assertAlmostEqual(c.unsafe_share, 50.0)
        self.assertIn("✗", c.verdict)

    def test_thin_margin_is_flagged_before_it_becomes_fatal(self):
        """청산은 아직 안 되지만 여유가 없는 구간도 잡아야 한다."""
        c = LeverageCheck(10.0, liquidation_distance(10), [0.06] * 10, safety=2.0)
        self.assertEqual(c.unsafe_share, 0.0)     # 0.06 < 0.095
        self.assertEqual(c.thin_share, 100.0)     # 0.06 × 2 > 0.095
        self.assertIn("?", c.verdict)

    def test_the_verdict_does_not_say_zero_percent_while_failing(self):
        """0.05%가 '0%'로 찍히면 ✗와 앞뒤가 안 맞는다."""
        c = LeverageCheck(20.0, liquidation_distance(20), [0.01] * 1999 + [0.9])
        self.assertGreater(c.unsafe_share, 0)
        self.assertNotIn("✗ 0%", c.verdict)

    def test_empty_input_does_not_divide_by_zero(self):
        c = LeverageCheck(10.0, liquidation_distance(10), [])
        self.assertEqual(c.unsafe_share, 0.0)
        self.assertEqual(c.thin_share, 0.0)


def _candles(n=200, price=50_000.0, range_pct=0.02):
    out = []
    for i in range(n):
        hi = price * (1 + range_pct / 2)
        lo = price * (1 - range_pct / 2)
        out.append(Candle(i * 3_600_000, price, hi, lo, price, 1.0))
    return out


class StopDistanceTests(unittest.TestCase):
    def test_a_wider_range_gives_a_wider_stop(self):
        narrow = stop_distances(_candles(range_pct=0.01))
        wide = stop_distances(_candles(range_pct=0.04))
        self.assertLess(sum(narrow) / len(narrow), sum(wide) / len(wide))

    def test_the_multiplier_scales_the_stop(self):
        one = stop_distances(_candles(), atr_stop_mult=1.0)
        two = stop_distances(_candles(), atr_stop_mult=2.0)
        self.assertAlmostEqual(sum(two) / sum(one), 2.0, places=6)

    def test_too_few_candles_yields_nothing_rather_than_crashing(self):
        self.assertEqual(stop_distances(_candles(3), atr_period=14), [])


class AnalyseTests(unittest.TestCase):
    def test_unsafe_share_rises_with_leverage(self):
        checks = analyse(_candles(range_pct=0.08), leverages=(2, 5, 10, 20, 50))
        shares = [c.unsafe_share for c in checks]
        self.assertEqual(shares, sorted(shares))

    def test_render_names_the_ceiling(self):
        text = render(analyse(_candles()))
        self.assertIn("배 이하", text)
        self.assertIn("손절폭 분포", text)

    def test_render_survives_candles_too_short_for_atr(self):
        self.assertIn("부족", render(analyse(_candles(3))))


class VerdictWordingTests(unittest.TestCase):
    """반올림이 판정을 거짓말로 만들면 안 된다."""

    def test_a_single_bad_trade_is_reported_as_a_count(self):
        c = LeverageCheck(20.0, liquidation_distance(20), [0.01] * 9999 + [0.9])
        self.assertEqual(c.unsafe_count, 1)
        self.assertLess(c.unsafe_share, 0.1)
        self.assertIn("1건", c.verdict)
        self.assertNotIn("0%", c.verdict)

    def test_a_large_share_is_reported_as_a_percentage(self):
        c = LeverageCheck(20.0, liquidation_distance(20), [0.9] * 5 + [0.01] * 5)
        self.assertIn("50%", c.verdict)
