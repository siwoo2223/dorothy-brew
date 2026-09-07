"""Every detector must find its own structure, survive junk, and stay honest."""

import unittest

from dorothy_brew import synth
from dorothy_brew.config import ScanConfig
from dorothy_brew.core import Candle, Series
from dorothy_brew.engine import scan
from dorothy_brew.registry import REGISTRY, all_specs
from dorothy_brew.signals import CONFIRMED, FORMING, LONG, RETEST, SHORT

# scenario -> the pattern it is built to contain
EXPECTED = {
    "bull_flag": "bull_flag",
    "double_bottom": "double_bottom",
    "head_and_shoulders": "head_shoulders",
    "ascending_triangle": "ascending_triangle",
    "rectangle": "rectangle_breakout",
    "cup_and_handle": "cup_handle",
    "falling_wedge": "ending_wedge",
    "liquidity_sweep": "liquidity_sweep",
    "fvg_impulse": "fair_value_gap",
    "trend_with_bos": "bos",
    "harmonic_bat": "bat",
    "harmonic_butterfly": "butterfly",
    "harmonic_crab": "crab",
    "harmonic_deep_crab": "deep_crab",
    "harmonic_shark": "shark",
    "harmonic_abcd": "abcd",
}


class TestDetection(unittest.TestCase):
    def test_each_scenario_finds_its_pattern(self):
        for scenario, pattern_id in EXPECTED.items():
            with self.subTest(scenario=scenario):
                report = scan(synth.SCENARIOS[scenario]())
                hits = [s for s in report.signals if s.pattern_id == pattern_id]
                self.assertTrue(hits, f"{pattern_id} not detected in {scenario}")
                self.assertGreaterEqual(max(h.confidence for h in hits), 0.5)

    def test_direction_matches_the_playbook(self):
        long_only = {"bull_flag": "bull_flag", "ascending_triangle": "ascending_triangle",
                     "cup_and_handle": "cup_handle", "double_bottom": "double_bottom"}
        for scenario, pattern_id in long_only.items():
            with self.subTest(scenario=scenario):
                hits = [s for s in scan(synth.SCENARIOS[scenario]()).signals
                        if s.pattern_id == pattern_id]
                self.assertTrue(all(h.direction == LONG for h in hits))
        hns = [s for s in scan(synth.head_and_shoulders()).signals
               if s.pattern_id == "head_shoulders"]
        self.assertTrue(all(h.direction == SHORT for h in hns))

    def test_every_registered_pattern_can_fire(self):
        fired = set()
        for fn in synth.SCENARIOS.values():
            fired.update(s.pattern_id for s in scan(fn()).signals)
        missing = [spec.id for spec in all_specs() if spec.id not in fired]
        self.assertEqual(missing, [], f"never fired on any scenario: {missing}")

    def test_checks_mirror_the_playbook_card(self):
        for fn in synth.SCENARIOS.values():
            for sig in scan(fn()).signals:
                spec = REGISTRY[sig.pattern_id]
                with self.subTest(pattern=spec.id):
                    self.assertEqual(sorted(sig.checks), sorted(spec.key_focus))
                    self.assertTrue(all(0.0 <= v <= 1.0 for v in sig.checks.values()))


class TestSignalIntegrity(unittest.TestCase):
    def test_stop_and_targets_sit_on_the_right_side(self):
        for name, fn in synth.SCENARIOS.items():
            for sig in scan(fn()).signals:
                with self.subTest(scenario=name, pattern=sig.pattern_id):
                    if sig.direction == LONG:
                        self.assertLess(sig.stop, sig.entry)
                        self.assertTrue(all(t > sig.entry for t in sig.targets))
                    else:
                        self.assertGreater(sig.stop, sig.entry)
                        self.assertTrue(all(t < sig.entry for t in sig.targets))
                    self.assertGreater(sig.risk, 0)
                    self.assertTrue(sig.targets)

    def test_status_and_score_ranges(self):
        for fn in synth.SCENARIOS.values():
            for sig in scan(fn()).signals:
                self.assertIn(sig.status, (CONFIRMED, RETEST, FORMING))
                self.assertTrue(0.0 <= sig.confidence <= 1.0)
                self.assertTrue(0.0 <= sig.score() <= 1.0)
                self.assertGreaterEqual(sig.end_index, sig.start_index)

    def test_forming_signals_score_below_triggered_ones(self):
        sig_pairs = []
        for fn in synth.SCENARIOS.values():
            for sig in scan(fn()).signals:
                sig_pairs.append(sig)
        for sig in sig_pairs:
            twin = type(sig)(**{**sig.__dict__, "status": CONFIRMED})
            if sig.status == FORMING:
                self.assertLess(sig.score(), twin.score())

    def test_position_sizing_respects_risk_budget(self):
        for fn in synth.SCENARIOS.values():
            for sig in scan(fn()).signals:
                size = sig.position_size(10_000, 1.0)
                self.assertAlmostEqual(size * sig.risk, 100.0, places=6)


class TestRobustness(unittest.TestCase):
    def _scan_all(self, series):
        report = scan(series, debug=False)
        self.assertEqual(report.errors, {}, f"detector raised: {report.errors}")
        return report

    def test_empty_and_tiny_series(self):
        self.assertEqual(self._scan_all(Series([], "X", "1h")).signals, [])
        tiny = Series([Candle(i, 10, 11, 9, 10, 1) for i in range(9)], "X", "1h")
        self.assertEqual(self._scan_all(tiny).signals, [])

    def test_flat_and_zero_volume_series(self):
        flat = Series([Candle(i, 10, 10, 10, 10, 0) for i in range(200)], "FLAT", "1h")
        self.assertEqual(self._scan_all(flat).signals, [])
        s = synth.bull_flag()
        novol = Series([Candle(c.ts, c.open, c.high, c.low, c.close, 0.0) for c in s],
                       "NOVOL", "1h")
        self._scan_all(novol)

    def test_random_walks_never_raise(self):
        for seed in range(1, 12):
            self._scan_all(synth.random_walk(seed=seed, bars=260))

    def test_single_repeated_price_with_gaps(self):
        candles = []
        price = 100.0
        for i in range(120):
            price *= 1.0 if i % 2 else 1.0
            candles.append(Candle(i * 60, price, price, price, price, 10))
        self._scan_all(Series(candles, "STAIR", "1h"))

    def test_detectors_are_pure(self):
        """Scanning twice must give the same answer (no hidden state)."""
        series = synth.bull_flag()
        first = [s.to_dict() for s in scan(series).signals]
        second = [s.to_dict() for s in scan(series).signals]
        self.assertEqual(first, second)


class TestConfigEffects(unittest.TestCase):
    def test_min_confidence_filters(self):
        series = synth.random_walk(bars=280)
        loose = scan(series, ScanConfig(min_confidence=0.3))
        strict = scan(series, ScanConfig(min_confidence=0.85))
        self.assertGreaterEqual(len(loose.signals), len(strict.signals))
        self.assertTrue(all(s.confidence >= 0.85 for s in strict.signals))

    def test_include_forming_toggle(self):
        cfg = ScanConfig(include_forming=False)
        for fn in synth.SCENARIOS.values():
            for sig in scan(fn(), cfg).signals:
                self.assertNotEqual(sig.status, FORMING)

    def test_category_and_pattern_filters(self):
        series = synth.bull_flag()
        only = scan(series, patterns=["bull_flag"])
        self.assertTrue(all(s.pattern_id == "bull_flag" for s in only.signals))
        classic = scan(series, categories=["classic"])
        self.assertTrue(all(REGISTRY[s.pattern_id].category == "classic"
                            for s in classic.signals))


if __name__ == "__main__":
    unittest.main()
