import unittest

from dorothy_brew import synth
from dorothy_brew.config import ScanConfig
from dorothy_brew.data import resample
from dorothy_brew.engine import build_plan, plans, sanitize, scan, scan_multi
from dorothy_brew.registry import CATEGORY_TITLES, REGISTRY, all_specs
from dorothy_brew.signals import CONFIRMED, FORMING, LONG, SHORT, Signal


def a_signal(**kw):
    base = dict(pattern_id="bull_flag", direction=LONG, status=CONFIRMED,
                start_index=0, end_index=10, entry=100.0, stop=95.0,
                targets=[105.0, 110.0], confidence=0.8)
    base.update(kw)
    return Signal(**base)


class TestRegistry(unittest.TestCase):
    def test_thirty_patterns_across_three_playbooks(self):
        specs = all_specs()
        self.assertEqual(len(specs), 30)
        for category in CATEGORY_TITLES:
            self.assertEqual(len(all_specs(category)), 10)

    def test_specs_are_complete(self):
        for spec in all_specs():
            with self.subTest(pattern=spec.id):
                self.assertTrue(spec.name and spec.name_ko and spec.hold)
                self.assertTrue(spec.analysis_tf and spec.entry_tf)
                self.assertEqual(len(spec.key_focus), 4)
                self.assertTrue(callable(spec.detector))
                self.assertIn("category_title", spec.to_dict())

    def test_numbers_are_unique_per_category(self):
        for category in CATEGORY_TITLES:
            numbers = sorted(s.number for s in all_specs(category))
            self.assertEqual(numbers, list(range(1, 11)))


class TestReport(unittest.TestCase):
    def test_sections_and_sorting(self):
        report = scan(synth.bull_flag())
        self.assertEqual(len(report.signals),
                         len(report.actionable()) + len(report.watchlist()))
        scores = [s.score() for s in report.signals]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_bias_is_normalised(self):
        report = scan(synth.bull_flag())
        bias = report.bias()
        self.assertTrue(-1.0 <= bias["net"] <= 1.0)
        self.assertGreater(bias["net"], 0.0)     # the scenario is bullish

    def test_by_category_covers_every_signal(self):
        report = scan(synth.random_walk(bars=260))
        grouped = report.by_category()
        self.assertEqual(sum(len(v) for v in grouped.values()), len(report.signals))

    def test_serialisation_is_json_safe(self):
        import json
        report = scan(synth.bull_flag())
        payload = json.dumps(report.to_dict())
        self.assertIn("bull_flag", payload)

    def test_metadata_is_attached(self):
        for sig in scan(synth.bull_flag()).signals:
            spec = REGISTRY[sig.pattern_id]
            self.assertEqual(sig.meta["pattern_name"], spec.name)
            self.assertEqual(sig.meta["hold"], spec.hold)
            self.assertEqual(sig.meta["entry_tf"], spec.entry_tf)


class TestSanitize(unittest.TestCase):
    def test_breached_stop_is_rejected(self):
        self.assertIsNone(sanitize(a_signal(stop=101.0)))
        self.assertIsNone(sanitize(a_signal(direction=SHORT, stop=99.0,
                                            targets=[95.0])))

    def test_met_targets_are_trimmed(self):
        sig = sanitize(a_signal(targets=[95.0, 105.0]))
        self.assertEqual(sig.targets, [105.0])
        self.assertIsNone(sanitize(a_signal(targets=[95.0, 90.0])))

    def test_targets_are_ordered_by_distance(self):
        sig = sanitize(a_signal(targets=[110.0, 105.0]))
        self.assertEqual(sig.targets, [105.0, 110.0])
        short = sanitize(a_signal(direction=SHORT, stop=105.0, targets=[90.0, 95.0]))
        self.assertEqual(short.targets, [95.0, 90.0])

    def test_zero_risk_is_rejected(self):
        self.assertIsNone(sanitize(a_signal(stop=100.0)))


class TestPlans(unittest.TestCase):
    def test_plan_sizing_and_scale_out(self):
        cfg = ScanConfig(equity=25_000, risk_pct=2.0)
        plan = build_plan(a_signal(), cfg)
        self.assertAlmostEqual(plan.risk_amount, 500.0)
        self.assertAlmostEqual(plan.size * 5.0, 500.0)
        self.assertAlmostEqual(sum(t["size_pct"] for t in plan.take_profits), 80.0)
        self.assertAlmostEqual(plan.take_profits[0]["rr"], 1.0)

    def test_plans_default_to_actionable_signals(self):
        report = scan(synth.bull_flag())
        self.assertEqual(len(plans(report)), len(report.actionable()))
        self.assertEqual(len(plans(report, actionable_only=False)), len(report.signals))


class TestMultiTimeframe(unittest.TestCase):
    def _frames(self):
        base = synth.bull_flag()
        return {"15m": base, "1H": resample(base, 2, "1H"), "4H": resample(base, 4, "4H")}

    def test_scan_multi_runs_every_frame(self):
        multi = scan_multi(self._frames(), respect_playbook_tf=False)
        self.assertEqual(set(multi.reports), {"15m", "1H", "4H"})
        self.assertTrue(multi.all_signals())
        self.assertTrue(-1.0 <= multi.bias()["net"] <= 1.0)

    def test_playbook_timeframes_are_respected(self):
        multi = scan_multi(self._frames(), respect_playbook_tf=True)
        for sig in multi.reports["15m"].signals:
            spec = REGISTRY[sig.pattern_id]
            self.assertTrue("15m" in spec.entry_tf
                            or not any(tf in multi.reports for tf in spec.entry_tf))

    def test_higher_timeframe_alignment_is_recorded(self):
        multi = scan_multi(self._frames(), respect_playbook_tf=False)
        lower = multi.reports["15m"].signals
        if lower:
            self.assertIn("htf_alignment", lower[0].meta)


if __name__ == "__main__":
    unittest.main()
