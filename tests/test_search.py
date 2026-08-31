"""탐색이 스스로를 속이지 않는가.

탐색은 이 저장소에서 가장 위험한 작업이다. 많이 재면 우연히 좋아 보이는
것이 반드시 나오고, 그걸 발견이라 부르면 실제 돈이 나간다.
"""

import unittest

from dorothy.analysis.concurrency import Outcome
from dorothy.analysis.search import Candidate, SearchReport, Trial, evaluate
from dorothy.analysis.concurrency import EdgeStats


def _stats(n, mean, spread, cost=0.0):
    """평균과 퍼짐을 지정한 표본. 부호를 번갈아 만들어 결정적이다."""
    out = []
    for i in range(n):
        out.append(mean + (spread if i % 2 else -spread))
    return EdgeStats(out, cost)


def _trial(label, is_stats, oos_stats):
    return Trial(Candidate(label, "12h", {}), is_stats, oos_stats)


class ShortlistTests(unittest.TestCase):
    def test_a_lone_test_uses_roughly_the_ordinary_bar(self):
        strong = _trial("a", _stats(60, 0.02, 0.03), _stats(60, 0.02, 0.03))
        self.assertEqual(len(SearchReport([strong]).shortlist), 1)

    def test_the_same_result_fails_once_it_is_one_of_many(self):
        """**핵심.** 같은 숫자라도 200번 중 하나면 근거가 약해진다."""
        strong = _trial("a", _stats(60, 0.012, 0.03), _stats(60, 0.012, 0.03))
        # 채우는 설정은 서로 달라야 한다 — 똑같으면 하나로 세어진다(DuplicateTests)
        filler = [
            _trial(f"f{i}", _stats(60, i * 1e-6, 0.03), _stats(60, i * 1e-6, 0.03))
            for i in range(1, 200)
        ]
        self.assertEqual(len(SearchReport([strong]).shortlist), 1)
        self.assertEqual(len(SearchReport([strong, *filler]).shortlist), 0)

    def test_a_losing_config_never_shortlists(self):
        losing = _trial("a", _stats(60, -0.05, 0.01), _stats(60, -0.05, 0.01))
        self.assertEqual(SearchReport([losing]).shortlist, [])

    def test_too_few_samples_are_excluded(self):
        tiny = _trial("a", _stats(8, 0.05, 0.001), _stats(8, 0.05, 0.001))
        self.assertEqual(SearchReport([tiny]).shortlist, [])
        self.assertEqual(SearchReport([tiny]).naive_passes, [])

    def test_a_tiny_out_of_sample_is_excluded_too(self):
        lopsided = _trial("a", _stats(60, 0.02, 0.03), _stats(5, 0.02, 0.001))
        self.assertEqual(SearchReport([lopsided]).shortlist, [])


class SurvivorTests(unittest.TestCase):
    def test_a_config_that_dies_out_of_sample_does_not_survive(self):
        overfit = _trial("a", _stats(60, 0.05, 0.02), _stats(60, -0.03, 0.05))
        rep = SearchReport([overfit])
        self.assertEqual(len(rep.shortlist), 1)
        self.assertEqual(rep.survivors, [])

    def test_a_config_that_holds_up_survives(self):
        real = _trial("a", _stats(60, 0.05, 0.02), _stats(60, 0.04, 0.02))
        self.assertEqual(len(SearchReport([real]).survivors), 1)

    def test_the_render_says_nothing_was_found_when_nothing_was(self):
        text = SearchReport([_trial("a", _stats(60, 0.0, 0.03), _stats(60, 0.0, 0.03))]).render()
        self.assertIn("찾은 것이 없습니다", text)

    def test_the_render_warns_even_about_survivors(self):
        real = _trial("a", _stats(60, 0.05, 0.02), _stats(60, 0.04, 0.02))
        self.assertIn("페이퍼", SearchReport([real]).render())


class NaivePassAccountingTests(unittest.TestCase):
    def test_it_counts_what_an_uncorrected_search_would_have_claimed(self):
        trials = [_trial("hit", _stats(60, 0.012, 0.03), _stats(60, 0.0, 0.03))]
        trials += [_trial(f"f{i}", _stats(60, i * 1e-6, 0.03), _stats(60, i * 1e-6, 0.03))
                   for i in range(1, 200)]
        rep = SearchReport(trials)
        self.assertEqual(len(rep.naive_passes), 1)
        self.assertIn("근거가 못 됩니다", rep.render())


class SplitTests(unittest.TestCase):
    """신호를 어떻게 가르는지가 결과를 바꾼다."""

    class _FakeStrategy:
        warmup = 1

        def generate(self, candles, position):  # pragma: no cover - 안 쓰임
            raise AssertionError

    def test_the_boundary_splits_by_signal_time_not_by_slicing_candles(self):
        """캔들을 먼저 자르면 검증기간 앞부분 신호가 워밍업에 먹힌다.

        evaluate()는 전체에서 신호를 한 번 만들고 진입 시점으로 가른다.
        여기서는 그 계약을 signal_outcomes를 가로채 확인한다.
        """
        import dorothy.analysis.search as mod

        seen = {}

        def fake_signal_outcomes(candles, strategy, *, max_bars=60, step=1, side_filter=None):
            seen["n"] = len(candles)
            from dorothy.models import Side
            return {
                Side.LONG: [Outcome(i, i + 1, 0.01) for i in range(0, 100, 2)],
                Side.SHORT: [],
            }

        original = mod.signal_outcomes
        mod.signal_outcomes = fake_signal_outcomes
        try:
            trial = evaluate(
                Candidate("fake", "12h", {}),
                list(range(100)),
                cost=0.0,
                split=0.6,
                strategy_factory=lambda name, **kw: self._FakeStrategy(),
            )
        finally:
            mod.signal_outcomes = original

        self.assertEqual(seen["n"], 100, "전체 구간에서 신호를 만들어야 합니다")
        # 경계 60: exit < 60 인 것이 탐색기간, entry >= 60 이 검증기간
        self.assertEqual(trial.in_sample.count, 30)
        self.assertEqual(trial.out_of_sample.count, 20)

    def test_a_signal_straddling_the_boundary_belongs_to_neither(self):
        """경계를 걸친 신호는 양쪽 어디에도 넣지 않는다 — 넣으면 누수다."""
        import dorothy.analysis.search as mod
        from dorothy.models import Side

        def fake(candles, strategy, *, max_bars=60, step=1, side_filter=None):
            return {Side.LONG: [Outcome(55, 70, 0.5)], Side.SHORT: []}

        original = mod.signal_outcomes
        mod.signal_outcomes = fake
        try:
            trial = evaluate(
                Candidate("fake", "12h", {}), list(range(100)), cost=0.0, split=0.6,
                strategy_factory=lambda name, **kw: self._FakeStrategy(),
            )
        finally:
            mod.signal_outcomes = original
        self.assertEqual(trial.in_sample.count, 0)
        self.assertEqual(trial.out_of_sample.count, 0)


class CandidateTests(unittest.TestCase):
    def test_label_is_stable_regardless_of_dict_order(self):
        a = Candidate("donchian", "12h", {"channel": 40, "allow_short": False})
        b = Candidate("donchian", "12h", {"allow_short": False, "channel": 40})
        self.assertEqual(a.label, b.label)


class DuplicateTests(unittest.TestCase):
    """전략이 무시하는 파라미터를 훑으면 같은 검정이 여러 번 센 것처럼 보인다.

    실제로 겪었다: signal_outcomes는 청산 규칙을 실행하지 않으므로
    exit_channel을 5/10/20/30으로 바꿔도 결과가 네 개 다 똑같았다.
    """

    def test_identical_results_count_as_one_test(self):
        same = [_trial(f"exit={v}", _stats(60, 0.02, 0.03), _stats(60, 0.01, 0.03))
                for v in (5, 10, 20, 30)]
        rep = SearchReport(same)
        self.assertEqual(rep.tested, 1)
        self.assertEqual(rep.duplicates, 3)

    def test_the_table_does_not_repeat_the_same_row(self):
        same = [_trial(f"exit={v}", _stats(60, 0.02, 0.03), _stats(60, 0.01, 0.03))
                for v in (5, 10, 20, 30)]
        self.assertEqual(SearchReport(same).render().count("+2.00%"), 1)

    def test_genuinely_different_configs_still_count_separately(self):
        different = [_trial(f"c{i}", _stats(60, 0.01 * i, 0.03), _stats(60, 0.0, 0.03))
                     for i in range(1, 5)]
        rep = SearchReport(different)
        self.assertEqual(rep.tested, 4)
        self.assertEqual(rep.duplicates, 0)

    def test_dedup_does_not_change_a_clean_search(self):
        one = [_trial("a", _stats(60, 0.05, 0.02), _stats(60, 0.04, 0.02))]
        self.assertEqual(len(SearchReport(one).survivors), 1)
