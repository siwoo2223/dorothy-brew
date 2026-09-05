"""지정가 진입 시뮬레이션 테스트.

여기서 틀리면 "메이커가 이득"이라는 결론이 통째로 뒤집힌다.
특히 조심할 것: 체결되지 않았어야 할 주문이 체결된 것으로 계산되면
비용은 깎이고 놓친 손실은 안 잡혀서, 지정가가 실제보다 훨씬 좋아 보인다.
"""

import unittest

from dorothy.backtest.maker_report import Leg, MakerComparison, analyse
from dorothy.config import Config
from dorothy.execution.maker import (
    FillOutcome,
    limit_price,
    round_trip_cost,
    simulate_limit_fill,
)
from dorothy.ml.labeling import Sample
from dorothy.models import Candle, Side


def bar(ts, o, h, l, c, v=1.0):
    return Candle(ts, o, h, l, c, v)


class LimitPriceTests(unittest.TestCase):
    def test_long_places_below_close(self):
        price = limit_price(bar(0, 100, 100, 100, 100), Side.LONG, atr_value=4.0, offset_atr=0.25)
        self.assertAlmostEqual(price, 99.0)

    def test_short_places_above_close(self):
        price = limit_price(bar(0, 100, 100, 100, 100), Side.SHORT, atr_value=4.0, offset_atr=0.25)
        self.assertAlmostEqual(price, 101.0)

    def test_zero_offset_sits_at_close(self):
        price = limit_price(bar(0, 100, 100, 100, 100), Side.LONG, atr_value=4.0, offset_atr=0.0)
        self.assertAlmostEqual(price, 100.0)


class FillTests(unittest.TestCase):
    def test_long_fills_when_price_comes_back(self):
        candles = [bar(0, 100, 100, 100, 100), bar(1, 100, 101, 98, 100)]
        out = simulate_limit_fill(candles, 0, Side.LONG, 99.0)
        self.assertTrue(out.filled)
        self.assertEqual(out.index, 1)
        self.assertAlmostEqual(out.price, 99.0)

    def test_long_misses_when_price_runs_away(self):
        """이게 핵심 케이스다. 돌파 후 안 돌아오면 못 잡는다."""
        candles = [bar(0, 100, 100, 100, 100)] + [
            bar(i, 100 + i * 5, 106 + i * 5, 100 + i * 5, 105 + i * 5) for i in range(1, 6)
        ]
        out = simulate_limit_fill(candles, 0, Side.LONG, 99.0, timeout_bars=3)
        self.assertFalse(out.filled)
        self.assertEqual(out.reason, "timeout")
        self.assertIsNone(out.price)

    def test_short_mirrored(self):
        candles = [bar(0, 100, 100, 100, 100), bar(1, 100, 102, 99, 100)]
        self.assertTrue(simulate_limit_fill(candles, 0, Side.SHORT, 101.0).filled)
        away = [bar(0, 100, 100, 100, 100), bar(1, 95, 96, 90, 91)]
        self.assertFalse(simulate_limit_fill(away, 0, Side.SHORT, 101.0).filled)

    def test_never_fills_on_the_signal_bar(self):
        """신호봉은 이미 종가가 찍힌 뒤다. 그 봉에서 체결됐다고 하면 미래참조다."""
        candles = [bar(0, 100, 120, 80, 100), bar(1, 100, 101, 99.5, 100)]
        out = simulate_limit_fill(candles, 0, Side.LONG, 99.0, timeout_bars=1)
        self.assertFalse(out.filled)

    def test_timeout_boundary_is_inclusive(self):
        candles = [bar(0, 100, 100, 100, 100),
                   bar(1, 100, 101, 100, 100),
                   bar(2, 100, 101, 100, 100),
                   bar(3, 100, 101, 98, 100)]
        self.assertFalse(simulate_limit_fill(candles, 0, Side.LONG, 99.0, timeout_bars=2).filled)
        self.assertTrue(simulate_limit_fill(candles, 0, Side.LONG, 99.0, timeout_bars=3).filled)

    def test_gap_through_limit_does_not_claim_a_better_price(self):
        """갭으로 훨씬 아래에서 열려도 지정가로 계산한다. 유리한 쪽을 취하지 않는다."""
        candles = [bar(0, 100, 100, 100, 100), bar(1, 90, 91, 88, 90)]
        out = simulate_limit_fill(candles, 0, Side.LONG, 99.0)
        self.assertAlmostEqual(out.price, 99.0)

    def test_no_bars_left(self):
        out = simulate_limit_fill([bar(0, 100, 100, 100, 100)], 0, Side.LONG, 99.0)
        self.assertFalse(out.filled)
        self.assertEqual(out.reason, "no_bars")

    def test_waited_counts_bars(self):
        candles = [bar(0, 100, 100, 100, 100),
                   bar(1, 100, 101, 100, 100),
                   bar(2, 100, 101, 98, 100)]
        self.assertEqual(simulate_limit_fill(candles, 0, Side.LONG, 99.0).waited, 2)


class CostTests(unittest.TestCase):
    def test_maker_entry_has_no_slippage(self):
        cost = round_trip_cost(maker_fee=0.0002, taker_fee=0.0006, slippage=0.0005,
                               maker_entry=True)
        self.assertAlmostEqual(cost, 0.0002 + 0.0006 + 0.0005)

    def test_taker_entry_pays_fee_and_slippage_both_ways(self):
        cost = round_trip_cost(maker_fee=0.0002, taker_fee=0.0006, slippage=0.0005,
                               maker_entry=False)
        self.assertAlmostEqual(cost, 2 * (0.0006 + 0.0005))

    def test_maker_is_cheaper(self):
        kw = dict(maker_fee=0.0002, taker_fee=0.0006, slippage=0.0005)
        self.assertLess(round_trip_cost(maker_entry=True, **kw),
                        round_trip_cost(maker_entry=False, **kw))


class LegTests(unittest.TestCase):
    def test_net_subtracts_cost(self):
        leg = Leg("x", [0.01, 0.01, 0.01], cost=0.0013)
        self.assertAlmostEqual(leg.gross, 1.0)
        self.assertAlmostEqual(leg.net, 1.0 - 0.13)

    def test_missed_leg_has_no_net(self):
        self.assertIsNone(Leg("놓침", [0.01], cost=None).net)
        self.assertEqual(Leg("놓침", [0.01], cost=None).t_stat, 0.0)

    def test_empty_leg_is_safe(self):
        leg = Leg("x", [], cost=0.001)
        self.assertEqual(leg.count, 0)
        self.assertEqual(leg.gross, 0.0)
        self.assertEqual(leg.t_stat, 0.0)


class VerdictTests(unittest.TestCase):
    def build(self, maker_rets, missed_rets, taker_rets=None):
        taker = Leg("시장가 전량 진입", taker_rets or maker_rets, cost=0.0022)
        return MakerComparison(
            taker, Leg("지정가 체결분", maker_rets, cost=0.0013),
            Leg("놓친 신호 (참고)", missed_rets, cost=None),
            0.25, 3, 0.0022, 0.0013,
        )

    def test_verdict_uses_totals_not_just_averages(self):
        """1회 평균이 올라도 매매 수가 줄면 총액은 내려갈 수 있다.

        평균만 비교하면 '지정가가 낫다'는 반대 결론이 나온다. 실제로 처음 그랬다.
        """
        maker_rets = [0.004] * 20      # 1회 평균은 높지만 20건뿐
        taker_rets = [0.003] * 100     # 낮지만 100건
        comparison = self.build(maker_rets, [0.003] * 80, taker_rets=taker_rets)
        self.assertGreater(comparison.maker.net, comparison.taker.net)   # 평균은 지정가 승
        self.assertLess(comparison.maker.total, comparison.taker.total)  # 총액은 시장가 승
        report = comparison.report()
        self.assertIn("5년 합계", report)
        self.assertIn("지정가가 못합니다", report)

    def test_total_is_net_times_count(self):
        leg = Leg("x", [0.01] * 50, cost=0.0)
        self.assertAlmostEqual(leg.total, 50.0)     # 1%씩 50번 = 50%

    def test_flags_when_the_missed_ones_were_better(self):
        """지정가의 진짜 위험. 놓친 손실이 절감분보다 크면 손해라고 말해야 한다."""
        report = self.build([0.001] * 50, [0.05] * 50).report()
        self.assertIn("놓친 신호가 잡은 신호보다 좋았습니다", report)
        self.assertIn("지정가가 손해입니다", report)

    def test_small_selection_penalty_is_still_a_win(self):
        report = self.build([0.010] * 50, [0.0104] * 50).report()
        self.assertIn("비용 절감", report)
        self.assertNotIn("지정가가 손해입니다", report)

    def test_apples_to_apples_leg_appears_when_present(self):
        comparison = self.build([0.01] * 30, [0.02] * 10)
        comparison.taker_on_filled = Leg("시장가 — 체결분만", [0.009] * 30, cost=0.0022)
        self.assertIn("체결분만", comparison.report())

    def test_says_so_when_the_filter_helped(self):
        report = self.build([0.02] * 50, [-0.01] * 50).report()
        self.assertIn("놓친 신호가 오히려 나빴습니다", report)

    def test_warns_when_a_zero_offset_fills_almost_everything(self):
        """종가에 붙인 지정가가 99% 체결되는 건 대기열을 무시했기 때문이다."""
        comparison = self.build([0.01] * 199, [0.01])
        comparison.offset_atr = 0.0
        report = comparison.report()
        self.assertIn("낙관적이라는 신호", report)
        self.assertIn("호가 대기열", report)

    def test_no_queue_warning_when_the_limit_is_set_back(self):
        comparison = self.build([0.01] * 199, [0.01])
        comparison.offset_atr = 0.5
        self.assertNotIn("낙관적이라는 신호", comparison.report())

    def test_no_queue_warning_when_fills_are_realistic(self):
        comparison = self.build([0.01] * 60, [0.01] * 40)
        comparison.offset_atr = 0.0
        self.assertNotIn("낙관적이라는 신호", comparison.report())

    def test_fill_rate(self):
        comparison = self.build([0.01] * 60, [0.01] * 40)
        self.assertAlmostEqual(comparison.fill_rate, 60.0)

    def test_no_fills_is_reported_not_crashed(self):
        report = self.build([], [0.01] * 20).report()
        self.assertIn("체결된 신호가 없습니다", report)

    def test_profitable_but_noisy_maker_is_not_endorsed(self):
        noisy = [0.05 if i % 2 else -0.045 for i in range(40)]
        report = self.build(noisy, [0.0] * 10).report()
        self.assertIn("우연과 구별되지 않습니다", report)


class AnalyseTests(unittest.TestCase):
    def setUp(self):
        # 완만히 오르는 시장 + 마지막에 되돌림 없는 급등
        self.candles = [bar(i * 3600_000, 100 + i * 0.1, 100.6 + i * 0.1,
                            99.4 + i * 0.1, 100 + i * 0.1) for i in range(400)]
        self.cfg = Config()

    def sample_at(self, index, side=Side.LONG):
        price = self.candles[index].close
        stop = price * 0.97 if side is Side.LONG else price * 1.03
        target = price * 1.03 if side is Side.LONG else price * 0.97
        return Sample(index, min(index + 20, len(self.candles) - 1), 0, [], 1, side,
                      stop=stop, target=target)

    def test_taker_leg_takes_every_signal(self):
        samples = [self.sample_at(i) for i in range(300, 340)]
        result = analyse(self.candles, samples, self.cfg)
        self.assertEqual(result.taker.count, len(samples))

    def test_maker_and_missed_partition_the_signals(self):
        samples = [self.sample_at(i) for i in range(300, 340)]
        result = analyse(self.candles, samples, self.cfg)
        self.assertEqual(result.maker.count + result.missed.count, len(samples))

    def test_keep_filters_to_a_subset(self):
        samples = [self.sample_at(i) for i in range(300, 340)]
        result = analyse(self.candles, samples, self.cfg, keep={0, 1, 2})
        self.assertEqual(result.taker.count, 3)

    def test_unfillable_limit_misses_everything(self):
        """지정가를 아주 멀리 두면 하나도 안 잡혀야 한다."""
        samples = [self.sample_at(i) for i in range(300, 340)]
        result = analyse(self.candles, samples, self.cfg, offset_atr=50.0)
        self.assertEqual(result.maker.count, 0)
        self.assertEqual(result.missed.count, len(samples))

    def test_generous_limit_fills_more_than_a_tight_one(self):
        samples = [self.sample_at(i) for i in range(300, 340)]
        tight = analyse(self.candles, samples, self.cfg, offset_atr=2.0)
        loose = analyse(self.candles, samples, self.cfg, offset_atr=0.0)
        self.assertGreater(loose.maker.count, tight.maker.count)

    def test_longer_wait_fills_at_least_as_many(self):
        samples = [self.sample_at(i) for i in range(300, 340)]
        short = analyse(self.candles, samples, self.cfg, timeout_bars=1)
        long_ = analyse(self.candles, samples, self.cfg, timeout_bars=10)
        self.assertGreaterEqual(long_.maker.count, short.maker.count)

    def test_maker_cost_is_lower_than_taker_cost(self):
        result = analyse(self.candles, [self.sample_at(300)], self.cfg)
        self.assertLess(result.maker_cost, result.taker_cost)

    def test_report_renders(self):
        samples = [self.sample_at(i) for i in range(300, 340)]
        self.assertIn("메이커 진입 검증", analyse(self.candles, samples, self.cfg).report())


if __name__ == "__main__":
    unittest.main()
