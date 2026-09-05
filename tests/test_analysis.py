"""분석 모듈 테스트.

가장 중요한 것은 인과성이다. 스윙·구조·FVG·파동 카운트는 전부
"지금까지의 캔들만으로 계산되었는가"를 만족해야 한다.
이게 깨지면 백테스트 수익률은 전부 허구다.
"""

import math
import unittest

from dorothy.analysis.elliott import analyze, measure_repainting
from dorothy.analysis.fibonacci import Leg, Zone, equal_within, ote_zone, zone_at
from dorothy.analysis.liquidity import (
    Bias,
    PoolKind,
    StructureEvent,
    detect_sweep,
    find_fvgs,
    find_pools,
    market_structure,
)
from dorothy.analysis.slope import linear_regression, measure, normalize_angle
from dorothy.analysis.swings import SwingKind, find_swings
from dorothy.data.indicators import atr as atr_ind
from dorothy.data.loader import synthetic
from dorothy.models import Candle


def bar(ts, o, h, l, c, v=1.0):
    return Candle(ts, o, h, l, c, v)


def atr_of(candles, period=14):
    return atr_ind(
        [c.high for c in candles], [c.low for c in candles], [c.close for c in candles], period
    )[-1]


# ==========================================================================
class TestSwingCausality(unittest.TestCase):
    """스윙 검출이 미래를 보지 않는가."""

    def setUp(self):
        self.candles = synthetic(400, seed=9)

    def test_as_of_matches_truncated_history(self):
        for k in (100, 200, 300, 399):
            with self.subTest(bar=k):
                self.assertEqual(
                    find_swings(self.candles[: k + 1]),
                    find_swings(self.candles, as_of=k),
                )

    def test_future_candles_cannot_change_past_swings(self):
        tampered = self.candles[:250] + [
            bar(c.ts, c.open * 5, c.high * 5, c.low * 5, c.close * 5) for c in self.candles[250:]
        ]
        self.assertEqual(
            find_swings(self.candles, as_of=240), find_swings(tampered, as_of=240)
        )

    def test_swings_are_confirmed_after_they_occur(self):
        for s in find_swings(self.candles):
            self.assertGreater(s.confirmed_index, s.index)
            self.assertEqual(s.lag, s.confirmed_index - s.index)

    def test_swings_alternate_high_and_low(self):
        swings = find_swings(self.candles)
        for a, b in zip(swings, swings[1:]):
            self.assertIsNot(a.kind, b.kind)

    def test_larger_right_means_later_confirmation(self):
        fast = find_swings(self.candles, right=1)
        slow = find_swings(self.candles, right=5)
        self.assertLess(
            sum(s.lag for s in fast) / len(fast), sum(s.lag for s in slow) / len(slow)
        )

    def test_handles_too_short_input(self):
        self.assertEqual(find_swings(self.candles[:3]), [])


# ==========================================================================
class TestSlope(unittest.TestCase):
    def test_angle_is_scale_invariant(self):
        """같은 비율로 움직이면 가격대가 달라도 같은 각도여야 한다."""

        def ramp(base, step, n=40):
            return [
                bar(i * 1000, base + i * step, (base + i * step) * 1.02,
                    (base + i * step) * 0.98, base + i * step)
                for i in range(n)
            ]

        cheap = measure(ramp(100, 1))
        pricey = measure(ramp(10_000, 100))
        self.assertAlmostEqual(cheap.degrees, pricey.degrees, places=6)

    def test_one_atr_per_bar_is_45_degrees(self):
        self.assertAlmostEqual(normalize_angle(1.0, 1.0), 45.0)
        self.assertAlmostEqual(normalize_angle(-1.0, 1.0), -45.0)
        self.assertAlmostEqual(normalize_angle(0.0, 1.0), 0.0)

    def test_angle_never_reaches_90(self):
        self.assertLess(normalize_angle(1e9, 1.0), 90.0)

    def test_zero_atr_is_safe(self):
        self.assertEqual(normalize_angle(5.0, 0.0), 0.0)

    def test_regression_on_straight_line(self):
        slope, intercept, r2 = linear_regression([0, 1, 2, 3, 4])
        self.assertAlmostEqual(slope, 1.0)
        self.assertAlmostEqual(intercept, 0.0)
        self.assertAlmostEqual(r2, 1.0)

    def test_noisy_series_has_low_r_squared(self):
        _, _, r2 = linear_regression([0, 10, 1, 9, 2, 8, 3])
        self.assertLess(r2, 0.5)

    def test_flat_series_has_zero_slope(self):
        slope, _, _ = linear_regression([5.0] * 10)
        self.assertAlmostEqual(slope, 0.0)


# ==========================================================================
class TestFibonacci(unittest.TestCase):
    def test_retracement_up_leg(self):
        leg = Leg(100.0, 200.0)
        self.assertAlmostEqual(leg.retracement(0.0), 200.0)
        self.assertAlmostEqual(leg.retracement(1.0), 100.0)
        self.assertAlmostEqual(leg.retracement(0.618), 138.2)

    def test_retracement_down_leg(self):
        leg = Leg(200.0, 100.0)
        self.assertAlmostEqual(leg.retracement(0.618), 161.8)
        self.assertFalse(leg.is_up)

    def test_extension(self):
        self.assertAlmostEqual(Leg(100.0, 200.0).extension(1.618), 261.8)

    def test_retracement_of_price(self):
        self.assertAlmostEqual(Leg(100.0, 200.0).retracement_of(150.0), 0.5)

    def test_ote_zone_is_between_62_and_79(self):
        zone = ote_zone(Leg(100.0, 200.0))
        self.assertAlmostEqual(zone.low, 121.0)
        self.assertAlmostEqual(zone.high, 138.0)
        self.assertTrue(zone.contains(130.0))
        self.assertFalse(zone.contains(150.0))

    def test_ote_zone_same_for_down_leg(self):
        zone = ote_zone(Leg(200.0, 100.0))
        self.assertLess(zone.low, zone.high)
        self.assertTrue(zone.contains(170.0))

    def test_tolerance_widens_the_zone(self):
        leg = Leg(100.0, 200.0)
        tight = zone_at(leg, 0.618, atr=4.0, tolerance_mult=0.0)
        loose = zone_at(leg, 0.618, atr=4.0, tolerance_mult=0.5)
        self.assertGreater(loose.width, tight.width)
        self.assertTrue(loose.contains(138.2))

    def test_zone_touched_by_wick(self):
        zone = Zone(100.0, 110.0)
        self.assertTrue(zone.touched_by(95.0, 105.0))    # 꼬리가 걸침
        self.assertTrue(zone.touched_by(105.0, 108.0))   # 완전히 안쪽
        self.assertFalse(zone.touched_by(80.0, 95.0))    # 아래로 비켜감

    def test_equal_within_uses_atr(self):
        self.assertTrue(equal_within(100.0, 100.5, atr=10.0, tolerance_mult=0.1))
        self.assertFalse(equal_within(100.0, 102.0, atr=10.0, tolerance_mult=0.1))


# ==========================================================================
class TestLiquidity(unittest.TestCase):
    def test_equal_highs_are_grouped_into_one_pool(self):
        """같은 레벨을 두 번 때리면 등가 고점(EQH)으로 묶여야 한다."""
        candles = (
            [bar(i, 100, 101, 99, 100) for i in range(5)]
            + [bar(5, 100, 110, 99, 100)]                # 고점 1
            + [bar(i, 100, 102, 95, 100) for i in range(6, 11)]
            + [bar(11, 100, 110.2, 99, 100)]             # 고점 2 (사실상 같은 자리)
            + [bar(i, 100, 102, 95, 100) for i in range(12, 20)]
        )
        swings = find_swings(candles, left=1, right=1, min_atr_mult=0.0)
        pools = find_pools(swings, atr=5.0, tolerance_mult=0.15)
        eqh = [p for p in pools if p.kind is PoolKind.BUY_SIDE and p.is_equal_level]
        self.assertTrue(eqh, "등가 고점이 하나도 묶이지 않았습니다")
        self.assertEqual(eqh[0].label, "EQH")

    def test_sweep_requires_close_back_inside(self):
        """꼬리로 뚫고 되돌아와야 스윕이다. 종가가 밖이면 그냥 돌파다."""
        base = [bar(i, 100, 101, 99, 100) for i in range(10)]
        base[5] = bar(5, 100, 101, 90, 100)     # 저점 90 형성
        swings = find_swings(base, left=1, right=1, min_atr_mult=0.0)
        pools = find_pools(swings, atr=2.0, tolerance_mult=0.1)

        # 되돌아온 경우 → 스윕
        swept = base + [bar(10, 100, 101, 88, 100)]
        self.assertIsNotNone(
            detect_sweep(swept, pools, index=10, atr=2.0, min_penetration=0.1)
        )
        # 종가도 아래에 머문 경우 → 스윕 아님
        broke = base + [bar(10, 100, 101, 88, 88.5)]
        self.assertIsNone(
            detect_sweep(broke, pools, index=10, atr=2.0, min_penetration=0.1)
        )

    def test_sweep_direction_is_contrarian(self):
        """매도측(저점) 스윕은 상방 편향이다."""
        base = [bar(i, 100, 101, 99, 100) for i in range(10)]
        base[5] = bar(5, 100, 101, 90, 100)
        swings = find_swings(base, left=1, right=1, min_atr_mult=0.0)
        pools = find_pools(swings, atr=2.0, tolerance_mult=0.1)
        sweep = detect_sweep(base + [bar(10, 100, 101, 88, 100)], pools, index=10, atr=2.0)
        self.assertIs(sweep.pool.kind, PoolKind.SELL_SIDE)
        self.assertIs(sweep.direction, Bias.BULLISH)

    def test_sweep_ignores_pools_formed_later(self):
        candles = [bar(i, 100, 101, 99, 100) for i in range(10)]
        swings = find_swings(candles, left=1, right=1, min_atr_mult=0.0)
        pools = find_pools(swings, atr=2.0)
        future_only = [p for p in pools if p.last_index >= 5]
        self.assertIsNone(detect_sweep(candles, future_only, index=3, atr=2.0))

    def test_bullish_fvg_detection(self):
        candles = [
            bar(0, 100, 101, 99, 100),
            bar(1, 100, 115, 100, 114),    # 급등 봉
            bar(2, 114, 116, 105, 115),    # low(105) > candles[0].high(101) → 갭
        ]
        fvgs = find_fvgs(candles, atr=2.0, min_size_mult=0.1)
        self.assertEqual(len(fvgs), 1)
        self.assertIs(fvgs[0].direction, Bias.BULLISH)
        self.assertAlmostEqual(fvgs[0].zone.low, 101.0)
        self.assertAlmostEqual(fvgs[0].zone.high, 105.0)
        self.assertEqual(fvgs[0].confirmed_index, 2)

    def test_bearish_fvg_detection(self):
        candles = [
            bar(0, 100, 101, 99, 100),
            bar(1, 99, 100, 85, 86),
            bar(2, 86, 95, 84, 90),        # high(95) < candles[0].low(99) → 하락 갭
        ]
        fvgs = find_fvgs(candles, atr=2.0, min_size_mult=0.1)
        self.assertEqual(len(fvgs), 1)
        self.assertIs(fvgs[0].direction, Bias.BEARISH)

    def test_fvg_fill_detection(self):
        candles = [
            bar(0, 100, 101, 99, 100),
            bar(1, 100, 115, 100, 114),
            bar(2, 114, 116, 105, 115),
            bar(3, 115, 116, 100, 102),    # 갭 하단(101)까지 되돌림
        ]
        fvg = find_fvgs(candles, atr=2.0)[0]
        self.assertTrue(fvg.is_filled(candles, 3))
        self.assertFalse(fvg.is_filled(candles, 2))

    def test_tiny_gaps_are_ignored(self):
        candles = [
            bar(0, 100, 100.01, 99, 100),
            bar(1, 100, 100.5, 100, 100.4),
            bar(2, 100.4, 100.6, 100.02, 100.5),
        ]
        self.assertEqual(find_fvgs(candles, atr=10.0, min_size_mult=0.5), [])

    def test_structure_needs_close_not_wick(self):
        """구조 전환은 종가 기준이다. 꼬리 돌파는 스윕이지 전환이 아니다."""
        candles = [bar(i, 100, 101, 99, 100) for i in range(6)]
        candles[2] = bar(2, 100, 112, 99, 100)   # 스윙 고점 112
        candles += [bar(6, 100, 115, 99, 105)]   # 꼬리는 뚫었지만 종가는 아래
        swings = find_swings(candles, left=1, right=1, min_atr_mult=0.0)
        st = market_structure(candles, swings, upto=len(candles) - 1)
        self.assertIsNot(st.event, StructureEvent.BOS)

    def test_structure_neutral_without_enough_swings(self):
        candles = [bar(i, 100, 101, 99, 100) for i in range(5)]
        st = market_structure(candles, [], upto=4)
        self.assertIs(st.bias, Bias.NEUTRAL)
        self.assertIs(st.event, StructureEvent.NONE)


# ==========================================================================
class TestElliott(unittest.TestCase):
    def setUp(self):
        self.candles = synthetic(600, seed=5)
        self.swings = find_swings(self.candles)

    def test_analyze_is_causal(self):
        """미래 캔들을 바꿔도 과거 시점의 카운트는 그대로여야 한다."""
        tampered = self.candles[:400] + [
            bar(c.ts, c.open * 4, c.high * 4, c.low * 4, c.close * 4)
            for c in self.candles[400:]
        ]
        a = analyze(self.candles, find_swings(self.candles, as_of=380), upto=380)
        b = analyze(tampered, find_swings(tampered, as_of=380), upto=380)
        self.assertEqual(a.direction, b.direction)
        self.assertEqual(a.current_wave, b.current_wave)
        self.assertEqual(a.valid, b.valid)

    def test_rule_wave2_cannot_fully_retrace_wave1(self):
        """2파가 1파를 100% 되돌리면 규칙 위반으로 표시되어야 한다."""
        from dorothy.analysis.elliott import _check_rules
        from dorothy.analysis.swings import Swing

        def sw(i, price, kind):
            return Swing(i, i * 1000, price, kind, i + 2)

        points = [
            sw(0, 100, SwingKind.LOW),
            sw(1, 200, SwingKind.HIGH),
            sw(2, 95, SwingKind.LOW),      # 1파 시작점보다 아래 = 위반
        ]
        self.assertTrue(_check_rules(points, up=True))

    def test_rule_wave4_cannot_overlap_wave1(self):
        from dorothy.analysis.elliott import RULE_WAVE4_NO_OVERLAP, _check_rules
        from dorothy.analysis.swings import Swing

        def sw(i, price, kind):
            return Swing(i, i * 1000, price, kind, i + 2)

        points = [
            sw(0, 100, SwingKind.LOW),
            sw(1, 200, SwingKind.HIGH),
            sw(2, 150, SwingKind.LOW),
            sw(3, 300, SwingKind.HIGH),
            sw(4, 190, SwingKind.LOW),     # 1파 고점(200) 아래로 침범 = 위반
        ]
        self.assertIn(RULE_WAVE4_NO_OVERLAP, _check_rules(points, up=True))

    def test_valid_impulse_passes_all_rules(self):
        from dorothy.analysis.elliott import _check_rules
        from dorothy.analysis.swings import Swing

        def sw(i, price, kind):
            return Swing(i, i * 1000, price, kind, i + 2)

        points = [
            sw(0, 100, SwingKind.LOW),
            sw(1, 200, SwingKind.HIGH),
            sw(2, 150, SwingKind.LOW),
            sw(3, 400, SwingKind.HIGH),    # 3파가 가장 길다
            sw(4, 320, SwingKind.LOW),     # 1파 영역 침범 없음
            sw(5, 450, SwingKind.HIGH),
        ]
        self.assertEqual(_check_rules(points, up=True), [])

    def test_insufficient_swings_returns_invalid(self):
        count = analyze(self.candles, self.swings[:2], upto=100)
        self.assertFalse(count.valid)
        self.assertEqual(count.current_wave, 0)

    def test_repaint_measurement_reports_instability(self):
        """엘리엇이 리페인팅한다는 사실 자체를 측정할 수 있어야 한다."""
        report = measure_repainting(self.candles, self.swings, start=200, step=5)
        self.assertGreater(report.bars_checked, 0)
        self.assertGreaterEqual(report.change_rate, 0.0)
        self.assertIn("안정성 측정", report.report())
        # 랜덤워크에서 카운트가 단 한 번도 안 바뀌면 그게 더 의심스럽다
        self.assertGreater(report.count_changes, 0)


if __name__ == "__main__":
    unittest.main()
