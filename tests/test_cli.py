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


class TestBacktestCli(unittest.TestCase):
    def test_backtest_text_output(self):
        code, out = run(["backtest", "demo:trending_market", "--warmup", "120",
                         "--window", "240", "--trades", "3"])
        self.assertEqual(code, 0)
        self.assertIn("BACKTEST", out)
        self.assertIn("win rate", out)
        self.assertIn("expectancy", out)

    def test_backtest_json_output(self):
        _, out = run(["backtest", "demo:trending_market", "--warmup", "120",
                      "--window", "240", "--json"])
        payload = json.loads(out)
        self.assertIn("stats", payload)
        self.assertIn("equity_curve", payload)
        self.assertEqual(payload["stats"]["trades"], len(payload["trades"]))

    def test_execution_flags_reach_the_config(self):
        _, out = run(["backtest", "demo:trending_market", "--warmup", "120",
                      "--window", "240", "--no-shorts", "--fee-bps", "0",
                      "--risk", "2", "--json"])
        payload = json.loads(out)
        self.assertTrue(all(t["direction"] == "long" for t in payload["trades"]))
        self.assertEqual(payload["stats"]["fees_paid"], 0.0)

    def test_backtest_on_a_short_series_reports_nothing(self):
        code, out = run(["backtest", "demo:fvg_impulse", "--warmup", "150"])
        self.assertEqual(code, 1)
        self.assertIn("no trades", out)


class TestFeedCli(unittest.TestCase):
    """The Bitget commands, wired to a fake transport instead of the network."""

    def setUp(self):
        from dorothy_brew.feeds import BitgetClient, JsonHttp
        from tests.test_feeds import FakeBitget
        import dorothy_brew.cli as cli_mod
        self.fake = FakeBitget(bars=200)
        self.original = cli_mod.BitgetClient
        cli_mod.BitgetClient = lambda product="spot", timeout=10.0: BitgetClient(
            product, http=JsonHttp(transport=self.fake, sleeper=lambda s: None))
        self.cli_mod = cli_mod

    def tearDown(self):
        self.cli_mod.BitgetClient = self.original

    def test_fetch_writes_a_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "btc.csv")
            code, out = run(["fetch", "BTCUSDT", "--tf", "1h", "--bars", "120",
                             "--out", path])
            self.assertEqual(code, 0)
            self.assertIn("120 candles", out)
            series = data.load_csv(path)
        self.assertEqual(len(series), 120)

    def test_fetch_scans_when_no_output_file(self):
        code, out = run(["fetch", "BTCUSDT", "--tf", "1h", "--bars", "200"])
        self.assertEqual(code, 0)
        self.assertIn("net bias", out)

    def test_fetch_json(self):
        _, out = run(["fetch", "BTCUSDT", "--tf", "1h", "--bars", "200", "--json"])
        self.assertIn("signals", json.loads(out))

    def test_live_once_scans_and_exits(self):
        code, out = run(["live", "BTCUSDT", "--tf", "1h", "--once", "--window", "200"])
        self.assertEqual(code, 0)
        self.assertIn("primed", out)
        self.assertIn("net bias", out)

    def test_live_stops_after_max_polls(self):
        code, out = run(["live", "BTCUSDT", "--tf", "1h", "--window", "200",
                         "--max-polls", "1", "--poll-seconds", "1"])
        self.assertEqual(code, 0)

    def test_network_failure_is_a_clean_message(self):
        self.fake.fail_times = 99
        with self.assertRaises(SystemExit) as ctx:
            run(["fetch", "BTCUSDT", "--tf", "1h"])
        self.assertIn("could not reach", str(ctx.exception))


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
