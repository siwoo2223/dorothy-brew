"""겹치는 신호를 독립 표본으로 세지 않는가.

이 저장소는 이 실수 하나로 "유일하게 검증을 통과한 전략"을 잘못 발표했다.
291건 t=2.67(통과)이 겹침을 빼니 115건 t=0.99(탈락)였다.
"""

import math
import unittest

from dorothy.analysis.concurrency import (
    EdgeStats,
    Outcome,
    analyze,
    drop_concurrent,
)


class DropConcurrentTests(unittest.TestCase):
    def test_non_overlapping_signals_all_survive(self):
        got = drop_concurrent([Outcome(0, 5, 0.1), Outcome(6, 10, 0.2)])
        self.assertEqual([o.entry_index for o in got], [0, 6])

    def test_a_signal_inside_an_open_position_is_dropped(self):
        got = drop_concurrent([Outcome(0, 10, 0.1), Outcome(3, 12, 0.2)])
        self.assertEqual([o.entry_index for o in got], [0])

    def test_a_signal_on_the_exit_bar_is_still_concurrent(self):
        """청산봉에 다시 신호가 나면 그 봉엔 아직 들고 있다."""
        self.assertEqual(len(drop_concurrent([Outcome(0, 10, 0.1), Outcome(10, 20, 0.2)])), 1)

    def test_the_bar_after_the_exit_is_free(self):
        self.assertEqual(len(drop_concurrent([Outcome(0, 10, 0.1), Outcome(11, 20, 0.2)])), 2)

    def test_a_long_signal_swallows_several_short_ones(self):
        out = [Outcome(0, 30, 0.1)] + [Outcome(i, i + 2, 0.2) for i in range(1, 25)]
        self.assertEqual(len(drop_concurrent(out)), 1)

    def test_order_of_the_input_does_not_matter(self):
        forward = [Outcome(0, 5, 0.1), Outcome(3, 9, 0.2), Outcome(6, 8, 0.3)]
        self.assertEqual(
            [o.entry_index for o in drop_concurrent(forward)],
            [o.entry_index for o in drop_concurrent(list(reversed(forward)))],
        )

    def test_empty_input(self):
        self.assertEqual(drop_concurrent([]), [])

    def test_an_exit_before_the_entry_is_rejected(self):
        with self.assertRaises(ValueError):
            Outcome(10, 3, 0.1)

    def test_a_same_bar_round_trip_is_allowed(self):
        self.assertEqual(len(drop_concurrent([Outcome(4, 4, 0.1), Outcome(5, 6, 0.2)])), 2)


class EdgeStatsTests(unittest.TestCase):
    def test_cost_is_subtracted_from_the_mean(self):
        s = EdgeStats([0.01, 0.01, 0.01, 0.01], cost=0.0022)
        self.assertAlmostEqual(s.net, 0.78, places=6)

    def test_a_profitable_but_noisy_edge_does_not_pass(self):
        s = EdgeStats([0.5, -0.4, 0.6, -0.45, 0.3], cost=0.0)
        self.assertGreater(s.net, 0)
        self.assertLess(abs(s.t_stat), 2)
        self.assertFalse(s.passes, "돈은 벌지만 우연과 구별이 안 되는데 통과시켰습니다")

    def test_a_significant_loss_does_not_pass(self):
        s = EdgeStats([-0.02] * 40, cost=0.0)
        self.assertLess(s.net, 0)
        self.assertFalse(s.passes)

    def test_zero_variance_does_not_divide_by_zero(self):
        self.assertEqual(EdgeStats([0.01] * 5, cost=0.0).t_stat, 0.0)

    def test_too_few_samples_report_zero(self):
        self.assertEqual(EdgeStats([0.01, 0.02], cost=0.0).t_stat, 0.0)


class OverlapReportTests(unittest.TestCase):
    @staticmethod
    def _noise(n):
        """결정적 잡음. random을 쓰면 테스트가 가끔 깨진다.

        하위 비트는 주기가 짧아서(LCG의 알려진 성질) 20번째 비트를 쓴다.
        """
        x, out = 12345, []
        for _ in range(n):
            x = (1103515245 * x + 12345) % (2 ** 31)
            out.append(((x >> 20) & 0x3FF) / 1023.0 - 0.5)
        return out

    @classmethod
    def _overlapping(cls, n=60, hold=20, edge=0.03, noise=0.0):
        """서로 겹치는 신호 n개. 실제로 잡히는 것은 몇 개뿐이다."""
        z = cls._noise(n)
        return [Outcome(i, i + hold, edge + noise * z[i]) for i in range(n)]

    def test_it_reports_how_many_could_not_be_taken(self):
        r = analyze(self._overlapping(), cost=0.0)
        self.assertEqual(r.raw.count, 60)
        self.assertLess(r.independent.count, 10)
        self.assertGreater(r.dropped_pct, 80)

    def test_inflation_is_the_square_root_of_the_sample_ratio(self):
        r = analyze(self._overlapping(), cost=0.0)
        self.assertAlmostEqual(
            r.inflation, math.sqrt(r.raw.count / r.independent.count), places=9
        )

    def test_the_verdict_names_overlap_when_that_is_what_carried_it(self):
        """이게 핵심이다. 겹쳐 세야만 통과하는 경우를 잡아내야 한다."""
        r = analyze(self._overlapping(n=200, hold=40, edge=0.02, noise=0.20), cost=0.0)
        self.assertTrue(r.raw.passes, "겹쳐 세면 통과하는 상황을 만들지 못했습니다")
        self.assertFalse(r.independent.passes)
        self.assertIn("겹침 때문에", r.verdict)

    def test_a_genuinely_independent_edge_still_passes(self):
        """과잉 차단도 버그다. 안 겹치는 신호는 그대로 통과해야 한다."""
        out = [
            Outcome(i * 10, i * 10 + 3, 0.05 if i % 3 else 0.01)
            for i in range(60)
        ]
        r = analyze(out, cost=0.0)
        self.assertEqual(r.dropped, 0)
        self.assertTrue(r.independent.passes)
        self.assertIn("✓", r.verdict)

    def test_the_render_shows_both_numbers(self):
        text = analyze(self._overlapping(), cost=0.0).render()
        self.assertIn("겹침 포함", text)
        self.assertIn("겹침 제거", text)

    def test_empty_input_does_not_crash(self):
        r = analyze([], cost=0.0)
        self.assertEqual(r.inflation, 1.0)
        self.assertIn("판단 불가", r.verdict)


class TheRealRegressionTests(unittest.TestCase):
    """실제로 틀렸던 그 숫자를 고정한다.

    12h 돈치안40 롱 전용, 삼중 장벽 60봉, 왕복 0.56%:
        겹침 포함 291건 t=2.67 → 통과
        겹침 제거 115건 t=0.99 → 탈락
    데이터가 저장소에 없으므로 비율만 재현한다.
    """

    def test_dropping_60_percent_of_signals_moves_t_by_that_much(self):
        r = analyze(
            [Outcome(i, i + 12, 0.02 + (0.03 if i % 3 == 0 else -0.015)) for i in range(291)],
            cost=0.0,
        )
        self.assertGreater(r.dropped_pct, 50, "겹침이 안 만들어졌습니다")
        self.assertGreater(
            r.raw.t_stat, r.independent.t_stat,
            "겹쳐 세는 쪽이 더 커야 합니다 — 그게 부풀림입니다",
        )
