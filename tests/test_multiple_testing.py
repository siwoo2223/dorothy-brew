"""여러 번 재고도 우연을 걸러내는가.

이 저장소는 "14번 시도해서 하나 양수"를 근거로 쓰지 않는다는 규칙을
문서에는 적어놨지만 코드에는 없었다. 여기가 그 코드다.
"""

import math
import unittest

from dorothy.analysis.multiple_testing import (
    bonferroni_threshold,
    expected_false_positives,
    t_pvalue,
    verdict,
)

try:
    from scipy import stats as _scipy_stats
except ImportError:
    _scipy_stats = None


class TPValueTests(unittest.TestCase):
    def test_zero_t_is_certainly_noise(self):
        self.assertEqual(t_pvalue(0.0, 50), 1.0)

    def test_bigger_t_means_smaller_p(self):
        ps = [t_pvalue(t, 50) for t in (0.5, 1.0, 2.0, 3.0, 5.0)]
        self.assertEqual(ps, sorted(ps, reverse=True))

    def test_sign_does_not_matter(self):
        self.assertAlmostEqual(t_pvalue(2.3, 40), t_pvalue(-2.3, 40), places=15)

    def test_p_stays_in_range(self):
        for t in (0.0, 0.1, 1.0, 10.0, 1e6):
            for df in (1, 3, 30, 5000):
                p = t_pvalue(t, df)
                self.assertTrue(0.0 <= p <= 1.0, f"t={t} df={df} → {p}")

    def test_a_small_sample_is_punished(self):
        """표본이 적으면 같은 t라도 덜 믿어야 한다."""
        self.assertGreater(t_pvalue(2.5, df=4), t_pvalue(2.5, df=400))

    def test_no_degrees_of_freedom_means_no_claim(self):
        self.assertEqual(t_pvalue(9.9, 0), 1.0)

    def test_infinite_t(self):
        self.assertEqual(t_pvalue(math.inf, 10), 0.0)

    @unittest.skipIf(_scipy_stats is None, "scipy 없음")
    def test_matches_scipy(self):
        """직접 구현했으므로 독립 구현과 맞춰본다."""
        for t in (0.3, 0.99, 1.55, 2.0, 2.87, 4.03, 7.5):
            for df in (2, 5, 22, 114, 290, 2000):
                mine, ref = t_pvalue(t, df), float(2 * _scipy_stats.t.sf(t, df))
                # p가 1e-10 수준까지 내려가므로 절대오차가 아니라 상대오차로 본다
                self.assertLess(
                    abs(mine - ref) / ref, 1e-9,
                    f"t={t} df={df}: 내것 {mine!r} vs scipy {ref!r}",
                )


class BonferroniTests(unittest.TestCase):
    def test_one_test_is_the_ordinary_bar(self):
        self.assertAlmostEqual(bonferroni_threshold(1, 200), 1.972, places=2)

    def test_more_tests_demand_a_higher_bar(self):
        ts = [bonferroni_threshold(n, 200) for n in (1, 10, 100, 1000)]
        self.assertEqual(ts, sorted(ts))

    def test_the_threshold_actually_hits_the_target_alpha(self):
        """뒤집기가 맞는지 확인한다 — 임계값의 p가 alpha/n이어야 한다."""
        for n in (1, 7, 190, 1000):
            thr = bonferroni_threshold(n, 150, alpha=0.05)
            self.assertAlmostEqual(t_pvalue(thr, 150), 0.05 / n, places=10)

    def test_a_190_config_search_needs_far_more_than_two(self):
        """이번 탐색 규모에서 |t|>=2가 얼마나 무의미한지 고정한다."""
        self.assertGreater(bonferroni_threshold(190, 100), 3.5)

    def test_no_samples_means_no_threshold(self):
        self.assertEqual(bonferroni_threshold(10, 0), float("inf"))


class VerdictTests(unittest.TestCase):
    def test_expected_false_positives_is_n_times_alpha(self):
        self.assertAlmostEqual(expected_false_positives(200, 0.05), 10.0)

    def test_finding_fewer_than_chance_is_not_a_finding(self):
        v = verdict(n_tests=200, n_survivors=5)
        self.assertIn("✗", v)
        self.assertIn("근거가 못 됩니다", v)

    def test_finding_exactly_chance_is_not_a_finding(self):
        self.assertIn("✗", verdict(n_tests=200, n_survivors=10))

    def test_finding_none_says_so(self):
        self.assertIn("0개 통과", verdict(n_tests=50, n_survivors=0))

    def test_finding_many_more_than_chance_is_still_not_a_promise(self):
        v = verdict(n_tests=20, n_survivors=15)
        self.assertIn("?", v)
        self.assertNotIn("✓", v)
