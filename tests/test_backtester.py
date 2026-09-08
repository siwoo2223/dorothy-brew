import unittest

from dorothy_brew import synth
from dorothy_brew.backtester import (BacktestConfig, Trade, _fillable, _weights,
                                   backtest)
from dorothy_brew.config import ScanConfig
from dorothy_brew.core import Candle, Series
from dorothy_brew.signals import CONFIRMED, LONG, SHORT, Signal


def a_signal(**kw):
    base = dict(pattern_id="bull_flag", direction=LONG, status=CONFIRMED,
                start_index=0, end_index=10, entry=100.0, stop=98.0,
                targets=[102.0, 104.0, 108.0], confidence=0.9)
    base.update(kw)
    return Signal(**base)


def straight_line(prices, ts0=1_700_000_000, step=3600, volume=100.0):
    return Series([Candle(ts0 + i * step, p, p + 0.05, p - 0.05, p, volume)
                   for i, p in enumerate(prices)], "LINE", "1h")


class TestHelpers(unittest.TestCase):
    def test_weights_always_sum_to_one(self):
        for n in range(1, 5):
            w = _weights([1.0] * n, (0.5, 0.3, 0.2))
            self.assertAlmostEqual(sum(w), 1.0)
            self.assertEqual(len(w), n)
        self.assertEqual(_weights([], (0.5, 0.5)), [])

    def test_fillable_rejects_tight_stops(self):
        bt = BacktestConfig(min_stop_distance_bps=10.0)
        self.assertTrue(_fillable(a_signal(), 100.0, bt))
        self.assertFalse(_fillable(a_signal(stop=99.995), 100.0, bt))

    def test_fillable_rejects_chasing(self):
        bt = BacktestConfig(max_entry_drift_r=0.5)
        # planned risk is 2.0, so a fill 1.5 above the signal entry is a chase
        self.assertFalse(_fillable(a_signal(), 101.5, bt))
        self.assertTrue(_fillable(a_signal(), 100.5, bt))

    def test_fillable_rejects_a_wrong_side_stop(self):
        bt = BacktestConfig()
        self.assertFalse(_fillable(a_signal(), 97.0, bt))
        self.assertFalse(_fillable(a_signal(direction=SHORT, stop=102.0,
                                            targets=[98.0]), 103.0, bt))

    def test_fillable_rejects_a_passed_target(self):
        bt = BacktestConfig(max_entry_drift_r=99)
        self.assertFalse(_fillable(a_signal(), 102.5, bt))


class TestExecution(unittest.TestCase):
    """Drive the loop with a hand-made signal so the outcome is known."""

    def _run(self, prices, sig, bt=None):
        series = straight_line(prices)
        bt = bt or BacktestConfig(warmup=2, window=50, fee_bps=0.0, slippage_bps=0.0,
                                  cooldown_bars=999)
        captured = {}

        def fake_scan(window, cfg=None, patterns=None, categories=None, debug=False):
            from dorothy_brew.engine import ScanReport
            report = ScanReport(window.symbol, window.timeframe, len(window))
            if not captured:
                captured["fired"] = True
                report.signals = [sig]
            return report

        import dorothy_brew.backtester as bt_mod
        original = bt_mod.scan
        bt_mod.scan = fake_scan
        try:
            return backtest(series, ScanConfig(), bt)
        finally:
            bt_mod.scan = original

    def test_target_sequence_and_breakeven_stop(self):
        prices = [100] * 4 + [101, 102, 103, 104, 105, 108, 108]
        result = self._run(prices, a_signal())
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.hit_targets, 3)
        self.assertTrue(trade.closed)
        self.assertEqual(trade.reason, "tp3")
        self.assertGreater(trade.pnl, 0)
        self.assertAlmostEqual(sum(f.qty for f in trade.fills if f.kind != "entry"),
                               trade.qty, places=8)

    def test_stop_out_costs_about_one_r(self):
        prices = [100] * 4 + [99, 98, 97, 96, 95]
        result = self._run(prices, a_signal())
        trade = result.trades[0]
        self.assertEqual(trade.reason, "stop")
        self.assertLess(trade.pnl, 0)
        self.assertAlmostEqual(trade.r_multiple, -1.0, places=2)

    def test_stop_gap_fills_at_the_open(self):
        prices = [100] * 4 + [100.2, 90]
        result = self._run(prices, a_signal())
        trade = result.trades[0]
        self.assertEqual(trade.reason, "stop")
        self.assertLess(trade.r_multiple, -1.0)      # gapped through, worse than 1R
        self.assertAlmostEqual(trade.fills[-1].price, 90.0, places=6)

    def test_breakeven_after_first_target(self):
        prices = [100] * 4 + [102, 101, 100, 99]
        result = self._run(prices, a_signal())
        trade = result.trades[0]
        self.assertEqual(trade.hit_targets, 1)
        self.assertEqual(trade.reason, "breakeven")
        self.assertAlmostEqual(trade.stop, trade.entry_price, places=8)

    def test_time_stop_closes_the_position(self):
        prices = [100] * 4 + [100.5] * 20
        bt = BacktestConfig(warmup=2, window=50, fee_bps=0.0, slippage_bps=0.0,
                            max_bars_in_trade=5)
        result = self._run(prices, a_signal(), bt)
        self.assertEqual(result.trades[0].reason, "time")

    def test_open_position_is_marked_out_at_the_end(self):
        prices = [100] * 4 + [100.5, 100.6]
        result = self._run(prices, a_signal())
        self.assertEqual(result.trades[0].reason, "eod")
        self.assertTrue(result.trades[0].closed)

    def test_short_trade_pnl_sign(self):
        prices = [100] * 4 + [99, 98, 96]
        sig = a_signal(direction=SHORT, stop=102.0, targets=[98.0, 96.0])
        result = self._run(prices, sig)
        trade = result.trades[0]
        self.assertEqual(trade.direction, SHORT)
        self.assertGreater(trade.pnl, 0)

    def test_entry_fills_at_the_next_open_not_the_signal_bar(self):
        prices = [100] * 4 + [101, 102, 103, 104, 105, 108]
        result = self._run(prices, a_signal())
        trade = result.trades[0]
        series = straight_line(prices)
        self.assertEqual(trade.entry_price, series[trade.entry_bar].open)

    def test_fees_reduce_pnl(self):
        prices = [100] * 4 + [101, 102, 103, 104, 105, 108, 108]
        free = self._run(prices, a_signal())
        costly = self._run(prices, a_signal(),
                           BacktestConfig(warmup=2, window=50, fee_bps=20.0,
                                          slippage_bps=5.0))
        self.assertGreater(free.trades[0].pnl, costly.trades[0].pnl)
        self.assertGreater(costly.trades[0].fees, 0)


class TestSizing(unittest.TestCase):
    def test_risk_per_trade_matches_the_budget(self):
        result = backtest(synth.trending_market(),
                          bt=BacktestConfig(warmup=120, window=240, risk_pct=1.0,
                                            max_notional_mult=1e9))
        self.assertTrue(result.trades)
        for trade in result.trades:
            planned = trade.qty * abs(trade.entry_price - trade.initial_stop)
            self.assertAlmostEqual(planned, trade.risk_amount, places=6)
            self.assertLess(trade.risk_amount, result.config.initial_equity * 0.5)

    def test_leverage_cap_limits_notional(self):
        bt = BacktestConfig(warmup=120, window=240, max_notional_mult=1.0)
        result = backtest(synth.trending_market(), bt=bt)
        for trade in result.trades:
            self.assertLessEqual(trade.qty * trade.entry_price,
                                 bt.initial_equity * 1.0 * 1.05)


class TestResults(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = backtest(synth.trending_market(),
                              bt=BacktestConfig(warmup=120, window=240))

    def test_no_lookahead_entry_is_after_the_signal_bar(self):
        for trade in self.result.trades:
            self.assertIsNotNone(trade.exit_bar)
            self.assertGreaterEqual(trade.exit_bar, trade.entry_bar)

    def test_stats_are_self_consistent(self):
        stats = self.result.stats()
        self.assertEqual(stats["trades"], stats["wins"] + stats["losses"])
        self.assertAlmostEqual(stats["win_rate"], stats["wins"] / stats["trades"], places=3)
        self.assertAlmostEqual(stats["total_r"],
                               round(sum(t.r_multiple for t in self.result.trades), 3),
                               places=2)
        self.assertLessEqual(stats["max_drawdown_pct"], 0.0)

    def test_equity_curve_tracks_the_return(self):
        stats = self.result.stats()
        final = self.result.equity_curve[-1][1]
        self.assertAlmostEqual(final / self.result.config.initial_equity - 1,
                               stats["total_return_pct"] / 100, places=4)

    def test_by_pattern_totals_match_the_trades(self):
        rows = self.result.by_pattern()
        self.assertEqual(sum(r["trades"] for r in rows.values()), len(self.result.trades))

    def test_json_round_trip(self):
        import json
        payload = json.loads(json.dumps(self.result.to_dict()))
        self.assertEqual(len(payload["trades"]), len(self.result.trades))
        self.assertIn("stats", payload)

    def test_trending_market_beats_a_random_walk(self):
        random = backtest(synth.random_walk(seed=5, bars=600),
                          bt=BacktestConfig(warmup=150, window=280))
        self.assertGreater(self.result.stats()["expectancy_r"],
                           random.stats()["expectancy_r"])

    def test_max_open_is_respected(self):
        bt = BacktestConfig(warmup=120, window=240, max_open=1, cooldown_bars=0)
        result = backtest(synth.trending_market(), bt=bt)
        for a in result.trades:
            overlapping = [b for b in result.trades
                           if b is not a and b.entry_bar <= a.entry_bar <= (b.exit_bar or 0)]
            self.assertEqual(overlapping, [])

    def test_short_series_returns_empty_result(self):
        tiny = straight_line([100] * 20)
        result = backtest(tiny, bt=BacktestConfig(warmup=150))
        self.assertEqual(result.trades, [])
        self.assertEqual(result.stats()["trades"], 0)

    def test_long_only_mode(self):
        result = backtest(synth.random_walk(seed=3, bars=400),
                          bt=BacktestConfig(warmup=120, window=240, allow_shorts=False))
        self.assertTrue(all(t.direction == LONG for t in result.trades))


if __name__ == "__main__":
    unittest.main()
