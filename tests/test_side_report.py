"""방향별 분해 테스트.

이 보고서가 낸 결론 하나로 전략 방향을 끄게 된다. 잘못된 판정은 비싸다.
특히 "한쪽이 갉아먹고 있다"와 "그냥 둘 다 별로다"를 구분해야 한다.
"""

import unittest

from dorothy.backtest.side_report import SideComparison, SideStats, analyse
from dorothy.config import Config
from dorothy.models import Action, Candle, Side, Signal
from dorothy.strategy.base import Strategy


def bar(ts, o, h, l, c, v=1.0):
    return Candle(ts, o, h, l, c, v)


class Always(Strategy):
    """지정한 방향으로 매 봉 신호를 내는 시험용 전략."""

    name = "always"

    def __init__(self, action=Action.ENTER_LONG, alternate=False):
        super().__init__()
        self.action = action
        self.alternate = alternate

    @property
    def warmup(self):
        return 1

    def generate(self, candles, position):
        price = candles[-1].close
        action = self.action
        if self.alternate:
            action = Action.ENTER_LONG if len(candles) % 2 else Action.ENTER_SHORT
        if action is Action.ENTER_LONG:
            return Signal(action, "테스트", stop_loss=price * 0.9, take_profit=price * 1.1)
        return Signal(action, "테스트", stop_loss=price * 1.1, take_profit=price * 0.9)


class SideStatsTests(unittest.TestCase):
    def test_gross_and_net(self):
        stats = SideStats(Side.LONG, [0.01, 0.01, 0.01], cost=0.0022)
        self.assertAlmostEqual(stats.gross, 1.0)
        self.assertAlmostEqual(stats.net, 1.0 - 0.22)

    def test_total_is_net_times_count(self):
        stats = SideStats(Side.LONG, [0.01] * 40, cost=0.0)
        self.assertAlmostEqual(stats.total, 40.0)

    def test_empty_is_safe(self):
        stats = SideStats(Side.SHORT, [], cost=0.0022)
        self.assertEqual(stats.count, 0)
        self.assertEqual(stats.gross, 0.0)
        self.assertEqual(stats.t_stat, 0.0)

    def test_t_stat_shrinks_with_noise(self):
        quiet = SideStats(Side.LONG, [0.01 + (0.001 if i % 2 else -0.001) for i in range(50)])
        noisy = SideStats(Side.LONG, [0.01 + (0.5 if i % 2 else -0.5) for i in range(50)])
        self.assertGreater(abs(quiet.t_stat), abs(noisy.t_stat))


class VerdictTests(unittest.TestCase):
    def build(self, long_rets, short_rets, cost=0.0022):
        return SideComparison(
            SideStats(Side.LONG, long_rets, cost),
            SideStats(Side.SHORT, short_rets, cost),
            cost,
        )

    def verdict(self, long_rets, short_rets):
        """판정 부분만 떼어낸다. 표 제목에도 '갉아먹고'가 들어 있어서,
        보고서 전체를 검사하면 늘 참이 되는 헛된 단언이 된다."""
        report = self.build(long_rets, short_rets).report()
        return report.rsplit("═" * 70, 1)[-1]

    def test_flags_a_side_that_eats_the_profit(self):
        """실제로 나온 경우. 돈치안40 롱 +0.402% / 숏 +0.001%."""
        verdict = self.verdict([0.006] * 100, [-0.004] * 100)
        self.assertIn("갉아먹고 있습니다", verdict)
        self.assertIn("숏이 이익을", verdict)

    def test_does_not_claim_long_is_always_better(self):
        """supertrend·mean_reversion은 숏이 나았다. 롱을 기본 정답으로 두면 안 된다."""
        verdict = self.verdict([-0.004] * 100, [0.006] * 100)
        self.assertIn("롱이 이익을 갉아먹고 있습니다", verdict)

    def test_small_gap_is_not_grounds_to_disable(self):
        verdict = self.verdict([0.0031] * 100, [0.0030] * 100)
        self.assertIn("방향을 끄는 근거가 못 됩니다", verdict)
        self.assertNotIn("갉아먹고", verdict)

    def test_both_positive_reports_the_gap_without_alarm(self):
        verdict = self.verdict([0.010] * 100, [0.004] * 100)
        self.assertIn("낫습니다", verdict)
        self.assertIn("흑자", verdict)
        self.assertNotIn("갉아먹고", verdict)

    def test_insignificant_winner_is_flagged(self):
        noisy = [0.05 if i % 2 else -0.044 for i in range(40)]
        self.assertIn("우연과 구별되지 않습니다", self.verdict(noisy, [-0.02] * 40))

    def test_one_sided_signal_cannot_be_compared(self):
        self.assertIn("비교할 수 없습니다", self.verdict([0.01] * 50, []))

    def test_combined_merges_both_sides(self):
        comparison = self.build([0.01] * 30, [0.02] * 20)
        self.assertEqual(comparison.combined.count, 50)

    def test_better_and_worse_pick_opposite_sides(self):
        comparison = self.build([0.01] * 30, [-0.01] * 30)
        self.assertIs(comparison.better.side, Side.LONG)
        self.assertIs(comparison.worse.side, Side.SHORT)

    def test_report_always_shows_both_rows(self):
        report = self.build([0.01] * 10, [0.01] * 10).report()
        self.assertIn("롱", report)
        self.assertIn("숏", report)
        self.assertIn("합쳐", report)


class AnalyseTests(unittest.TestCase):
    def setUp(self):
        self.up = [bar(i * 3600_000, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(300)]
        self.cfg = Config()

    def test_long_only_strategy_produces_no_short_rows(self):
        result = analyse(self.up, Always(Action.ENTER_LONG), self.cfg, max_bars=20)
        self.assertGreater(result.long.count, 0)
        self.assertEqual(result.short.count, 0)

    def test_rising_market_favours_long(self):
        result = analyse(self.up, Always(alternate=True), self.cfg, max_bars=20)
        self.assertGreater(result.long.gross, result.short.gross)

    def test_falling_market_favours_short(self):
        down = [bar(i * 3600_000, 400 - i, 401 - i, 399 - i, 400 - i) for i in range(300)]
        result = analyse(down, Always(alternate=True), self.cfg, max_bars=20)
        self.assertGreater(result.short.gross, result.long.gross)

    def test_cost_comes_from_config(self):
        cfg = Config()
        cfg.exchange.taker_fee = 0.001
        cfg.exchange.slippage = 0.001
        result = analyse(self.up, Always(), cfg, max_bars=20)
        self.assertAlmostEqual(result.cost, 0.004)

    def test_step_reduces_sample_count(self):
        dense = analyse(self.up, Always(), self.cfg, max_bars=20, step=1)
        sparse = analyse(self.up, Always(), self.cfg, max_bars=20, step=5)
        self.assertLess(sparse.long.count, dense.long.count)

    def test_report_renders_on_real_analysis(self):
        result = analyse(self.up, Always(alternate=True), self.cfg, max_bars=20)
        self.assertIn("방향별 분해", result.report())


if __name__ == "__main__":
    unittest.main()
