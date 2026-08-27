"""수수료 바닥선 테스트.

이 표를 보고 타임프레임을 정하게 된다. 손익분기 승률이 틀리면
불가능한 타임프레임을 가능하다고 말하게 된다.
"""

import math
import unittest

from dorothy.backtest.cost_floor import CostFloor, Row, analyse
from dorothy.config import Config
from dorothy.models import Candle


def bar(ts, o, h, l, c, v=1.0):
    return Candle(ts, o, h, l, c, v)


def series(closes, step=3600_000):
    return [bar(i * step, c, c, c, c) for i, c in enumerate(closes)]


class RowTests(unittest.TestCase):
    def test_breakeven_at_one_to_one(self):
        """평균 움직임 1%, 왕복 비용 0.22% → 0.5 + 0.22/2 = 61%."""
        row = Row("1h", 100, mean_move=1.0, median_move=1.0, cost=0.22)
        self.assertAlmostEqual(row.breakeven_win_rate, 61.0)

    def test_smaller_moves_need_higher_win_rate(self):
        big = Row("1d", 100, 2.0, 2.0, 0.22)
        small = Row("1h", 100, 0.4, 0.4, 0.22)
        self.assertGreater(small.breakeven_win_rate, big.breakeven_win_rate)

    def test_free_trading_needs_only_half(self):
        row = Row("1h", 100, 1.0, 1.0, cost=0.0)
        self.assertAlmostEqual(row.breakeven_win_rate, 50.0)

    def test_impossible_when_cost_exceeds_the_move(self):
        row = Row("1m", 100, mean_move=0.05, median_move=0.05, cost=0.22)
        self.assertGreater(row.breakeven_win_rate, 100.0)
        self.assertFalse(row.possible)

    def test_eaten_share(self):
        row = Row("1h", 100, mean_move=0.44, median_move=0.44, cost=0.22)
        self.assertAlmostEqual(row.eaten, 50.0)

    def test_zero_move_is_impossible_not_a_crash(self):
        row = Row("1h", 100, 0.0, 0.0, 0.22)
        self.assertEqual(row.breakeven_win_rate, 100.0)
        self.assertFalse(row.possible)

    def test_annual_cost_scales_with_bar_count(self):
        hourly = Row("1h", 100, 1.0, 1.0, 0.22)
        daily = Row("1d", 100, 1.0, 1.0, 0.22)
        self.assertGreater(hourly.annual_cost_if_always_trading,
                           daily.annual_cost_if_always_trading)


class ScalingTests(unittest.TestCase):
    def test_random_walk_gives_one_half(self):
        """움직임이 √시간에 비례하면 지수가 0.5여야 한다."""
        floor = CostFloor(cost=0.22)
        for tf, mult in (("1h", 1), ("4h", 4), ("1d", 24)):
            floor.rows.append(Row(tf, 1000, 0.4 * math.sqrt(mult), 0.0, 0.22))
        self.assertAlmostEqual(floor.scaling_exponent, 0.5, places=6)

    def test_linear_scaling_gives_one(self):
        floor = CostFloor(cost=0.22)
        for tf, mult in (("1h", 1), ("4h", 4), ("1d", 24)):
            floor.rows.append(Row(tf, 1000, 0.4 * mult, 0.0, 0.22))
        self.assertAlmostEqual(floor.scaling_exponent, 1.0, places=6)

    def test_single_row_cannot_estimate(self):
        floor = CostFloor(cost=0.22)
        floor.rows.append(Row("1h", 1000, 0.4, 0.0, 0.22))
        self.assertEqual(floor.scaling_exponent, 0.0)


class VerdictTests(unittest.TestCase):
    def build(self, moves):
        floor = CostFloor(cost=0.22)
        for tf, m in moves:
            floor.rows.append(Row(tf, 1000, m, m, 0.22))
        return floor

    def test_calls_out_impossible_timeframes(self):
        report = self.build([("1m", 0.05), ("1d", 2.0)]).report()
        self.assertIn("불가능", report)
        self.assertIn("1m", report.split("어떤 전략으로도 불가능")[0][-60:])

    def test_no_impossible_claim_when_all_are_feasible(self):
        report = self.build([("4h", 0.8), ("1d", 2.0)]).report()
        self.assertNotIn("어떤 전략으로도 불가능", report)

    def test_names_hardest_and_easiest(self):
        report = self.build([("1h", 0.4), ("1d", 2.3)]).report()
        self.assertIn("가장 불리 1h", report)
        self.assertIn("가장 유리 1d", report)

    def test_reports_random_walk_when_exponent_is_half(self):
        floor = self.build([("1h", 0.4), ("4h", 0.8), ("1d", 0.4 * math.sqrt(24))])
        self.assertIn("거의 정확히 랜덤워크", floor.report())

    def test_reports_trending_when_exponent_is_high(self):
        floor = self.build([("1h", 0.4), ("4h", 1.6), ("1d", 9.6)])
        self.assertIn("추세성", floor.report())

    def test_empty_is_safe(self):
        self.assertIn("잴 것이 없습니다", CostFloor(cost=0.22).report())


class AnalyseTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()

    def walk(self, n=6000, step=0.004, seed=12345):
        """결정적 유사난수 워크.

        주기적인 계단을 쓰면 안 된다. 주기가 상위 타임프레임과 공명해서
        12시간봉 움직임이 4시간봉보다 작아지는 인공물이 생긴다.
        실제로 그 데이터로 테스트를 짰다가 걸렸다.

        LCG의 **최하위 비트도 쓰면 안 된다.** 주기가 2라서 그냥 교대한다.
        이것도 실제로 걸렸다. 상위 비트를 쓴다.
        """
        closes, price, state = [], 100.0, seed
        for _ in range(n):
            state = (1103515245 * state + 12345) % (1 << 31)
            price *= 1 + (step if (state >> 20) & 1 else -step)
            closes.append(price)
        return series(closes)

    def test_the_test_data_is_actually_a_random_walk(self):
        """시험용 데이터가 랜덤워크가 아니면 아래 테스트들이 무의미해진다."""
        result = analyse(self.walk(), self.cfg, timeframes=("1h", "2h", "4h", "12h"))
        self.assertAlmostEqual(result.scaling_exponent, 0.5, delta=0.08)

    def test_cost_comes_from_config(self):
        cfg = Config()
        cfg.exchange.taker_fee = 0.001
        cfg.exchange.slippage = 0.001
        result = analyse(self.walk(), cfg, timeframes=("1h", "4h"))
        self.assertAlmostEqual(result.cost, 0.4)

    def test_longer_timeframes_move_more(self):
        result = analyse(self.walk(), self.cfg, timeframes=("1h", "4h", "12h"))
        moves = [r.mean_move for r in result.rows]
        self.assertEqual(moves, sorted(moves))

    def test_longer_timeframes_need_lower_win_rate(self):
        result = analyse(self.walk(), self.cfg, timeframes=("1h", "4h", "12h"))
        rates = [r.breakeven_win_rate for r in result.rows]
        self.assertEqual(rates, sorted(rates, reverse=True))

    def test_skips_timeframes_shorter_than_the_data(self):
        """1시간봉으로 5분봉을 만들 수는 없다. 조용히 지어내면 안 된다."""
        result = analyse(self.walk(200), self.cfg, timeframes=("5m", "1h", "4h"))
        self.assertNotIn("5m", [r.timeframe for r in result.rows])

    def test_skips_timeframes_with_too_few_bars(self):
        result = analyse(self.walk(200), self.cfg, timeframes=("1h", "3d"))
        self.assertNotIn("3d", [r.timeframe for r in result.rows])

    def test_rejects_unknown_timeframe(self):
        with self.assertRaises(ValueError):
            analyse(self.walk(500), self.cfg, timeframes=("7h",))

    def test_report_renders(self):
        result = analyse(self.walk(), self.cfg, timeframes=("1h", "4h"))
        self.assertIn("수수료 바닥선", result.report())


if __name__ == "__main__":
    unittest.main()
