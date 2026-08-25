"""비교 하네스 · 워크포워드 검증 테스트.

여기서 가장 중요한 것은 **누수 방지**다.
검증 구간이 학습 구간과 겹치면 워크포워드는 그냥 백테스트가 되고,
과최적화를 잡아내는 기능이 조용히 사라진다.
"""

import unittest

from dorothy.backtest.compare import buy_and_hold, compare, comparison_report
from dorothy.backtest.walkforward import (
    DEFAULT_GRIDS,
    WalkForwardResult,
    _grid_combinations,
)
from dorothy.backtest import walkforward
from dorothy.config import Config
from dorothy.data.loader import synthetic
from dorothy.models import Candle
from dorothy.strategy.base import available, known_params
from dorothy.strategy.base import _REGISTRY


def config(equity=1000.0) -> Config:
    cfg = Config()
    cfg.mode = "backtest"
    cfg.initial_equity = equity
    cfg.strategy.params = {}
    return cfg


class TestBuyAndHold(unittest.TestCase):
    def test_doubling_price_roughly_doubles_equity(self):
        candles = [Candle(i * 1000, 100 + i, 100 + i, 100 + i, 100 + i, 1.0) for i in range(101)]
        cfg = config()
        cfg.exchange.taker_fee = 0.0
        m = buy_and_hold(candles, cfg)
        self.assertAlmostEqual(m.return_pct, 100.0, places=4)   # 100 → 200

    def test_falling_price_loses_money(self):
        candles = [Candle(i * 1000, 200 - i, 200 - i, 200 - i, 200 - i, 1.0) for i in range(101)]
        m = buy_and_hold(candles, config())
        self.assertLess(m.net_pnl, 0)

    def test_fees_are_charged(self):
        candles = [Candle(i * 1000, 100, 100, 100, 100, 1.0) for i in range(50)]
        cfg = config()
        cfg.exchange.taker_fee = 0.001
        self.assertLess(buy_and_hold(candles, cfg).net_pnl, 0)   # 횡보인데 수수료만큼 손실

    def test_drawdown_is_measured(self):
        prices = [100] * 10 + [150] * 10 + [80] * 10 + [120] * 10
        candles = [Candle(i * 1000, p, p, p, p, 1.0) for i, p in enumerate(prices)]
        cfg = config()
        cfg.exchange.taker_fee = 0.0
        self.assertGreater(buy_and_hold(candles, cfg).max_drawdown_pct, 40.0)


class TestComparison(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candles = synthetic(3000, seed=5, timeframe="15m")
        cls.rows = compare(cls.candles, config(), entries={"donchian": {}, "random": {}})

    def test_baselines_are_included(self):
        names = {r.name for r in self.rows}
        self.assertIn("buy_and_hold", names)
        self.assertIn("random", names)

    def test_random_is_marked_as_a_baseline(self):
        random_row = next(r for r in self.rows if r.name == "random")
        self.assertTrue(random_row.is_baseline)

    def test_all_rows_produce_metrics(self):
        for row in self.rows:
            with self.subTest(strategy=row.name):
                self.assertIsNotNone(row.metrics, row.error)

    def test_report_mentions_the_control_group(self):
        text = comparison_report(self.rows)
        self.assertIn("기준선", text)
        self.assertIn("무작위", text)

    def test_broken_strategy_does_not_abort_the_comparison(self):
        rows = compare(
            self.candles, config(), entries={"donchian": {}, "없는전략": {}}
        )
        self.assertTrue(any(r.metrics for r in rows))
        self.assertTrue(any(r.error for r in rows))

    def test_comparison_is_deterministic(self):
        again = compare(self.candles, config(), entries={"donchian": {}, "random": {}})
        for a, b in zip(self.rows, again):
            self.assertAlmostEqual(a.metrics.net_pnl, b.metrics.net_pnl)


class TestWalkForwardSplitting(unittest.TestCase):
    """구간 분할이 새지 않는가 — 이 테스트가 워크포워드의 존재 이유다."""

    @classmethod
    def setUpClass(cls):
        cls.candles = synthetic(8000, seed=5, timeframe="15m")
        cls.result = walkforward.run(
            cls.candles, config(), strategy_name="donchian", folds=4
        )

    def test_test_window_always_follows_the_train_window(self):
        for fold in self.result.folds:
            with self.subTest(fold=fold.index):
                self.assertLessEqual(fold.train_range[1], fold.test_range[0])

    def test_train_and_test_windows_never_overlap(self):
        for fold in self.result.folds:
            train_start, train_end = fold.train_range
            test_start, test_end = fold.test_range
            with self.subTest(fold=fold.index):
                self.assertFalse(
                    set(range(train_start, train_end)) & set(range(test_start, test_end)),
                    "학습 구간과 검증 구간이 겹칩니다 — 누수",
                )

    def test_windows_are_non_empty(self):
        for fold in self.result.folds:
            self.assertGreater(fold.train_range[1] - fold.train_range[0], 0)
            self.assertGreater(fold.test_range[1] - fold.test_range[0], 0)

    def test_produces_the_requested_number_of_folds(self):
        self.assertEqual(len(self.result.folds), 4)

    def test_chosen_params_come_from_the_grid(self):
        grid = DEFAULT_GRIDS["donchian"]
        for fold in self.result.folds:
            for key, value in fold.best_params.items():
                with self.subTest(fold=fold.index, param=key):
                    self.assertIn(value, grid[key])

    def test_report_renders(self):
        self.assertIn("워크포워드", self.result.report())


class TestWalkForwardGuards(unittest.TestCase):
    def test_too_little_data_is_rejected(self):
        with self.assertRaises(ValueError):
            walkforward.run(synthetic(300), config(), strategy_name="donchian", folds=4)

    def test_invalid_train_ratio_is_rejected(self):
        candles = synthetic(4000, timeframe="15m")
        with self.assertRaises(ValueError):
            walkforward.run(candles, config(), strategy_name="donchian", train_ratio=0.95)

    def test_zero_folds_is_rejected(self):
        with self.assertRaises(ValueError):
            walkforward.run(synthetic(4000), config(), strategy_name="donchian", folds=0)


class TestGridDefinitions(unittest.TestCase):
    def test_combinations_are_the_cartesian_product(self):
        combos = _grid_combinations({"a": [1, 2], "b": [3, 4, 5]})
        self.assertEqual(len(combos), 6)
        self.assertIn({"a": 1, "b": 3}, combos)

    def test_empty_grid_yields_one_empty_combination(self):
        self.assertEqual(_grid_combinations({}), [{}])

    def test_every_grid_targets_real_parameters(self):
        """격자가 존재하지 않는 파라미터를 건드리면 탐색이 조용히 무효가 된다."""
        for name, grid in DEFAULT_GRIDS.items():
            with self.subTest(strategy=name):
                self.assertIn(name, available())
                supported = known_params(_REGISTRY[name])
                self.assertFalse(set(grid) - supported, f"{name}: 없는 파라미터 {set(grid) - supported}")


class TestEfficiencyMetric(unittest.TestCase):
    def _fold(self, in_pct, out_pct, trades=50):
        from dorothy.backtest.metrics import Metrics
        from dorothy.backtest.walkforward import Fold

        def m(pct):
            return Metrics(1000.0, 1000.0 * (1 + pct / 100), trades, trades // 2,
                           trades // 2, 100.0, 50.0, 5.0, 10.0, 3)

        return Fold(0, (0, 100), (100, 150), {}, m(in_pct), m(out_pct))

    def test_efficiency_is_out_over_in(self):
        result = WalkForwardResult("x", [self._fold(20.0, 10.0)])
        self.assertAlmostEqual(result.efficiency, 0.5, places=4)

    def test_negative_out_of_sample_gives_negative_efficiency(self):
        result = WalkForwardResult("x", [self._fold(20.0, -5.0)])
        self.assertLess(result.efficiency, 0)
        self.assertIn("과최적화", result.report())

    def test_unstable_parameters_are_flagged(self):
        from dorothy.backtest.metrics import Metrics
        from dorothy.backtest.walkforward import Fold

        def m():
            return Metrics(1000.0, 1100.0, 40, 20, 20, 100.0, 50.0, 5.0, 10.0, 3)

        folds = [
            Fold(i, (0, 10), (10, 20), {"channel": 10 + i * 10}, m(), m()) for i in range(4)
        ]
        result = WalkForwardResult("x", folds)
        self.assertFalse(result.params_are_stable)
        self.assertIn("노이즈", result.report())

    def test_stable_parameters_are_not_flagged(self):
        from dorothy.backtest.metrics import Metrics
        from dorothy.backtest.walkforward import Fold

        def m():
            return Metrics(1000.0, 1100.0, 40, 20, 20, 100.0, 50.0, 5.0, 10.0, 3)

        folds = [Fold(i, (0, 10), (10, 20), {"channel": 20}, m(), m()) for i in range(4)]
        self.assertTrue(WalkForwardResult("x", folds).params_are_stable)


if __name__ == "__main__":
    unittest.main()
