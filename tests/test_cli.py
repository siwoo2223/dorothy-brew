import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from dorothy_brew import data, synth
from dorothy_brew.cli import main
from dorothy_brew.report import card, catalogue, render_report, signal_block
from dorothy_brew.engine import scan
from dorothy_brew.registry import BULL_FLAG, all_specs


def run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


class TestCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.csv = os.path.join(cls.tmp.name, "BULLFLAG.csv")
        data.write_csv(synth.bull_flag(), cls.csv)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_scan_text_output(self):
        code, out = run(["scan", self.csv, "--tf", "1H"])
        self.assertEqual(code, 0)
        self.assertIn("Bull Flag", out)
        self.assertIn("EXECUTABLE", out)
        self.assertIn("entry", out)

    def test_scan_json_output_has_plans(self):
        code, out = run(["scan", self.csv, "--json", "--tf", "1H"])
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertIn("signals", payload)
        self.assertIn("plans", payload)
        self.assertTrue(payload["signals"][0]["targets"])
        self.assertIn("score", payload["signals"][0])

    def test_scan_from_demo_scenario(self):
        code, out = run(["scan", "demo:double_bottom"])
        self.assertEqual(code, 0)
        self.assertIn("Double Bottom", out)

    def test_filters_reach_the_config(self):
        _, out = run(["scan", self.csv, "--pattern", "bull_flag", "--json"])
        payload = json.loads(out)
        self.assertTrue(all(s["pattern_id"] == "bull_flag" for s in payload["signals"]))
        _, out = run(["scan", self.csv, "--category", "harmonic", "--json",
                      "--min-confidence", "0.99"])
        for sig in json.loads(out)["signals"]:
            self.assertGreaterEqual(sig["confidence"], 0.99)

    def test_no_forming_flag(self):
        _, out = run(["scan", self.csv, "--no-forming", "--json"])
        for sig in json.loads(out)["signals"]:
            self.assertNotEqual(sig["status"], "forming")

    def test_equity_and_risk_change_size(self):
        _, out = run(["scan", self.csv, "--json", "--equity", "50000", "--risk", "2"])
        plan = json.loads(out)["plans"][0]
        self.assertAlmostEqual(plan["risk_amount"], 1000.0)

    def test_multi_timeframe_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = synth.bull_flag()
            paths = {}
            for tf, factor in (("15m", 1), ("1H", 2)):
                path = os.path.join(tmp, f"{tf}.csv")
                data.write_csv(base if factor == 1 else data.resample(base, factor), path)
                paths[tf] = path
            code, out = run(["multi", f"15m:{paths['15m']}", f"1H:{paths['1H']}", "--json"])
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(set(payload["timeframes"]), {"15m", "1H"})

    def test_patterns_catalogue(self):
        code, out = run(["patterns"])
        self.assertEqual(code, 0)
        for spec in all_specs():
            self.assertIn(spec.id, out)

    def test_patterns_json(self):
        _, out = run(["patterns", "--json"])
        payload = json.loads(out)
        self.assertEqual(len(payload), 30)
        self.assertIn("key_focus", payload[0])

    def test_explain_card(self):
        code, out = run(["explain", "bull_flag"])
        self.assertEqual(code, 0)
        for line in BULL_FLAG.key_focus:
            self.assertIn(line, out)
        self.assertIn("Hours - Days", out)

    def test_explain_unknown_pattern_exits(self):
        with self.assertRaises(SystemExit):
            run(["explain", "not_a_pattern"])

    def test_demo_runs_a_scenario(self):
        code, out = run(["demo", "cup_and_handle"])
        self.assertEqual(code, 0)
        self.assertIn("cup_and_handle", out)

    def test_unknown_demo_scenario_exits(self):
        with self.assertRaises(SystemExit):
            run(["scan", "demo:nope"])

    def test_missing_file_exits(self):
        with self.assertRaises((SystemExit, FileNotFoundError)):
            run(["scan", "/nonexistent/file.csv"])


class TestRendering(unittest.TestCase):
    def test_signal_block_shows_the_checklist(self):
        report = scan(synth.bull_flag())
        sig = report.signals[0]
        text = signal_block(sig)
        for name in sig.checks:
            self.assertIn(name, text)
        self.assertIn("TP1", text)

    def test_render_report_handles_empty_scan(self):
        from dorothy_brew.core import Series
        text = render_report(scan(Series([], "X", "1h")))
        self.assertIn("nothing triggered", text)

    def test_card_and_catalogue_cover_every_pattern(self):
        text = catalogue()
        for spec in all_specs():
            self.assertIn(spec.name, text)
            self.assertIn(spec.name_ko, card(spec))


if __name__ == "__main__":
    unittest.main()
