"""머신러닝 계층 테스트.

sklearn 없이도 도는 부분(라벨링·검증 분할·특징 인과성)이 사실 가장 중요하다.
모델 자체보다 **누수 방지**가 틀리면 결과 전체가 허구가 되기 때문이다.
"""

import unittest

from dorothy.data.loader import synthetic
from dorothy.ml.features import FEATURE_NAMES, compute_at, warmup
from dorothy.ml.labeling import Sample, triple_barrier
from dorothy.ml.validation import purged_walk_forward
from dorothy.models import Candle, Side


def bar(ts, o, h, l, c, v=1.0):
    return Candle(ts, o, h, l, c, v)


class TripleBarrierTests(unittest.TestCase):
    def test_target_hit_first(self):
        candles = [bar(i, 100, 100, 100, 100) for i in range(3)]
        candles[1] = bar(1, 100, 111, 99, 110)
        out = triple_barrier(candles, 0, Side.LONG, stop=95, target=110)
        self.assertEqual(out.label, 1)
        self.assertEqual(out.reason, "target")
        self.assertEqual(out.exit_index, 1)

    def test_stop_hit_first(self):
        candles = [bar(i, 100, 100, 100, 100) for i in range(3)]
        candles[1] = bar(1, 100, 101, 94, 96)
        out = triple_barrier(candles, 0, Side.LONG, stop=95, target=110)
        self.assertEqual(out.label, 0)
        self.assertEqual(out.reason, "stop")

    def test_stop_wins_ties(self):
        """한 봉에서 둘 다 닿으면 손절. 보수적 가정이자 백테스트 엔진과 같은 규칙."""
        candles = [bar(i, 100, 100, 100, 100) for i in range(3)]
        candles[1] = bar(1, 100, 115, 90, 105)
        out = triple_barrier(candles, 0, Side.LONG, stop=95, target=110)
        self.assertEqual(out.reason, "stop")
        self.assertEqual(out.label, 0)

    def test_short_side_mirrored(self):
        candles = [bar(i, 100, 100, 100, 100) for i in range(3)]
        candles[1] = bar(1, 100, 101, 89, 90)
        out = triple_barrier(candles, 0, Side.SHORT, stop=105, target=90)
        self.assertEqual(out.reason, "target")
        self.assertEqual(out.label, 1)

    def test_timeout_judged_by_direction(self):
        up = [bar(i, 100, 100 + i, 100, 100 + i) for i in range(10)]
        out = triple_barrier(up, 0, Side.LONG, stop=50, target=500, max_bars=5)
        self.assertEqual(out.reason, "timeout")
        self.assertEqual(out.label, 1)

        out = triple_barrier(up, 0, Side.SHORT, stop=500, target=50, max_bars=5)
        self.assertEqual(out.reason, "timeout")
        self.assertEqual(out.label, 0)

    def test_no_outcome_at_last_bar(self):
        candles = [bar(i, 100, 100, 100, 100) for i in range(3)]
        self.assertIsNone(triple_barrier(candles, 2, Side.LONG, 95, 110))

    def test_only_uses_bars_after_entry(self):
        """진입 이전 봉이 배리어를 건드려도 결과가 바뀌면 안 된다."""
        candles = [bar(i, 100, 100, 100, 100) for i in range(6)]
        candles[0] = bar(0, 100, 999, 1, 100)     # 진입 전 극단 봉
        candles[4] = bar(4, 100, 111, 99, 110)
        out = triple_barrier(candles, 2, Side.LONG, stop=95, target=110)
        self.assertEqual(out.exit_index, 4)
        self.assertEqual(out.reason, "target")


class PurgedWalkForwardTests(unittest.TestCase):
    def spans(self, n, hold=5):
        return [(i * 10, i * 10 + hold) for i in range(n)]

    def test_train_is_always_before_test(self):
        splits = purged_walk_forward(self.spans(100), folds=4, embargo_bars=0)
        for split in splits:
            self.assertTrue(max(split.train) < min(split.test))

    def test_purges_overlapping_labels(self):
        """라벨 구간이 검증 구간까지 걸친 표본은 학습에서 빠져야 한다."""
        spans = [(i * 10, i * 10 + 45) for i in range(100)]   # 뒤쪽 4개와 겹침
        splits = purged_walk_forward(spans, folds=4, embargo_bars=0)
        for split in splits:
            test_first = min(spans[i][0] for i in split.test)
            for i in split.train:
                self.assertLess(spans[i][1], test_first)
            self.assertGreater(split.purged, 0)

    def test_refuses_when_everything_would_be_purged(self):
        """겹침이 심해 학습 표본이 하나도 안 남으면 조용히 넘어가면 안 된다.

        빈 학습셋으로 만든 모델이 '검증 통과'로 보고되는 게 최악의 결과다.
        """
        spans = [(i, i + 5000) for i in range(100)]   # 모든 라벨이 끝까지 걸침
        with self.assertRaises(ValueError):
            purged_walk_forward(spans, folds=4, embargo_bars=0)

    def test_embargo_removes_adjacent_samples(self):
        spans = self.spans(100, hold=1)
        no_embargo = purged_walk_forward(spans, folds=4, embargo_bars=0)
        with_embargo = purged_walk_forward(spans, folds=4, embargo_bars=50)
        for a, b in zip(no_embargo, with_embargo):
            self.assertLess(len(b.train), len(a.train))
            self.assertGreater(b.embargoed, 0)

    def test_test_sets_do_not_overlap(self):
        splits = purged_walk_forward(self.spans(200), folds=4, embargo_bars=0)
        seen = set()
        for split in splits:
            self.assertFalse(seen & set(split.test))
            seen |= set(split.test)

    def test_rejects_too_few_samples(self):
        with self.assertRaises(ValueError):
            purged_walk_forward(self.spans(5), folds=4)


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.candles = synthetic(600, seed=7)

    def test_returns_none_before_warmup(self):
        self.assertIsNone(compute_at(self.candles, 10))
        self.assertIsNone(compute_at(self.candles, warmup() - 1))
        self.assertIsNotNone(compute_at(self.candles, warmup()))

    def test_vector_length_matches_names(self):
        values = compute_at(self.candles, 400)
        self.assertEqual(len(values), len(FEATURE_NAMES))

    def test_all_values_finite(self):
        for i in range(warmup(), len(self.candles), 37):
            for name, value in zip(FEATURE_NAMES, compute_at(self.candles, i)):
                self.assertTrue(-1e6 < value < 1e6, f"{name}={value}")

    def test_causal_future_bars_cannot_change_features(self):
        """가장 중요한 테스트 — 미래 캔들을 잘라내도 값이 같아야 한다."""
        index = 400
        full = compute_at(self.candles, index)
        truncated = compute_at(self.candles[: index + 1], index)
        self.assertEqual(full, truncated)

    def test_causal_across_many_points(self):
        for index in (250, 300, 350, 450, 550):
            self.assertEqual(
                compute_at(self.candles, index),
                compute_at(self.candles[: index + 1], index),
                f"index {index}에서 미래 참조",
            )

    def test_out_of_range_index(self):
        self.assertIsNone(compute_at(self.candles, len(self.candles)))
        self.assertIsNone(compute_at(self.candles, len(self.candles) + 100))

    def test_flat_market_returns_none(self):
        """변동성이 0이면 ATR 정규화가 불가능하다. 0으로 나누는 대신 표본을 버린다."""
        flat = [bar(i * 3600_000, 100, 100, 100, 100, 0.0) for i in range(400)]
        self.assertIsNone(compute_at(flat, 300))

    def test_near_flat_market_does_not_explode(self):
        """변동성이 아주 작아도 값이 발산하면 안 된다."""
        quiet = [
            bar(i * 3600_000, 100, 100.001, 99.999, 100 + (i % 2) * 0.001, 1.0)
            for i in range(400)
        ]
        values = compute_at(quiet, 300)
        self.assertIsNotNone(values)
        for name, value in zip(FEATURE_NAMES, values):
            self.assertTrue(-1e6 < value < 1e6, f"{name}={value}")


class SampleTests(unittest.TestCase):
    def test_span_covers_entry_to_label(self):
        sample = Sample(10, 25, 0, [0.0], 1, Side.LONG)
        self.assertEqual(sample.span, (10, 25))


class EdgeVerdictTests(unittest.TestCase):
    """승률이 올라도 수수료를 못 넘으면 실패라고 말해야 한다."""

    def build(self, edges, lift=5.0):
        from dorothy.ml.meta import EdgeCheck, FoldResult, MetaResult

        result = MetaResult(round_trip_cost=0.0022)
        result.folds = [FoldResult(0, 100, 50, 40.0, 40.0 + lift, 20, lift)]
        result.edges = [EdgeCheck(label, n, gross, gross - 0.22) for label, n, gross in edges]
        return result

    def test_flags_failure_when_fees_eat_the_lift(self):
        report = self.build([("필터 없음", 2000, 0.028), ("임계 0.50", 400, 0.118)]).report()
        self.assertIn("비용을 넘지 못했습니다", report)
        self.assertNotIn("비용을 넘겼습니다", report)

    def test_win_rate_lift_alone_is_not_a_pass(self):
        """승률 +5%p인데도 손익이 음수면 '개선됐다'고 말하면 안 된다."""
        result = self.build([("필터 없음", 2000, 0.028), ("임계 0.50", 400, 0.118)], lift=5.0)
        self.assertGreater(result.mean_lift, 3)
        self.assertIn("✗", result.report())

    def test_names_the_real_cause(self):
        """1차 전략에 우위가 없으면 모델이 아니라 전략을 고치라고 해야 한다."""
        report = self.build([("필터 없음", 2000, 0.028), ("임계 0.50", 400, 0.118)]).report()
        self.assertIn("1차 전략의 수수료 전 우위", report)

    def test_warns_when_higher_threshold_is_worse(self):
        """확신이 높을수록 나빠지면 모델이 잡음을 배웠다는 뜻이다."""
        report = self.build([
            ("필터 없음", 2000, 0.028),
            ("임계 0.50", 400, 0.118),
            ("임계 0.70", 70, -0.307),
        ]).report()
        self.assertIn("잡음 학습", report)

    def test_passes_when_net_is_positive(self):
        report = self.build([("필터 없음", 2000, 0.10), ("임계 0.60", 300, 0.55)]).report()
        self.assertIn("비용을 넘겼습니다", report)
        self.assertNotIn("비용을 넘지 못했습니다", report)

    def test_without_candles_it_refuses_to_judge(self):
        from dorothy.ml.meta import FoldResult, MetaResult

        result = MetaResult()
        result.folds = [FoldResult(0, 100, 50, 40.0, 48.0, 20, 8.0)]
        report = result.report()
        self.assertIn("승률만으로는 판단할 수 없습니다", report)

    def test_survives_property(self):
        from dorothy.ml.meta import EdgeCheck

        self.assertTrue(EdgeCheck("x", 10, 0.5, 0.28).survives)
        self.assertFalse(EdgeCheck("x", 10, 0.5, -0.01).survives)


class GrossReturnTests(unittest.TestCase):
    def test_short_side_return_is_inverted(self):
        from dorothy.ml.meta import _gross_returns

        candles = [bar(0, 100, 100, 100, 100), bar(1, 110, 110, 110, 110)]
        long_sample = Sample(0, 1, 0, [], 1, Side.LONG)
        short_sample = Sample(0, 1, 0, [], 0, Side.SHORT)
        returns = _gross_returns(candles, [long_sample, short_sample])
        self.assertAlmostEqual(returns[0], 0.10)
        self.assertAlmostEqual(returns[1], -0.10)


class MetaTrainingTests(unittest.TestCase):
    """sklearn이 있을 때만 도는 통합 테스트."""

    def setUp(self):
        try:
            import numpy  # noqa: F401
            import sklearn  # noqa: F401
        except ImportError:
            self.skipTest("numpy/scikit-learn 미설치 — pip install -r requirements-ml.txt")

    def test_dataset_and_training_round_trip(self):
        from dorothy.ml.meta import build_dataset, train
        from dorothy.strategy.donchian import DonchianBreakoutStrategy

        candles = synthetic(2500, seed=11)
        samples = build_dataset(candles, DonchianBreakoutStrategy(channel=20), step=3)
        if len(samples) < 60:
            self.skipTest(f"합성 데이터에서 표본이 {len(samples)}개뿐")

        for sample in samples:
            self.assertLess(sample.index, sample.exit_index)
            self.assertIn(sample.label, (0, 1))
            self.assertEqual(len(sample.features), len(FEATURE_NAMES))

        result = train(samples, candles=candles, folds=3, threshold=0.5)
        self.assertTrue(result.folds)
        self.assertTrue(result.edges)
        self.assertEqual(result.edges[0].label, "필터 없음")
        self.assertAlmostEqual(result.round_trip_cost, 0.0022)
        self.assertIn("왕복 비용", result.report())
        self.assertEqual(len(result.feature_importance), len(FEATURE_NAMES))
        # 보고되는 성적은 전부 검증 구간 것이어야 한다
        self.assertLessEqual(result.total_taken, sum(f.test_size for f in result.folds))
        self.assertIn("검증 구간", result.report())

    def test_rejects_tiny_dataset(self):
        from dorothy.ml.meta import train

        with self.assertRaises(ValueError):
            train([Sample(i, i + 1, i, [0.0] * len(FEATURE_NAMES), i % 2, Side.LONG)
                   for i in range(10)])


if __name__ == "__main__":
    unittest.main()
