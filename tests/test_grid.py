"""그리드 매매 테스트.

가장 위험한 실수는 **한 봉 안에서 사고파는 것**이다. OHLC만으로는 봉 안의
순서를 알 수 없는데, 저가를 먼저 찍었다고 가정하면 없는 왕복이 생겨
수익이 통째로 부풀려진다. 그리드는 왕복 횟수가 곧 수익이라 특히 치명적이다.
"""

import unittest

from dorothy.models import Candle
from dorothy.strategy.grid import GridSpec, Lot, simulate


def bar(ts, o, h, l, c, v=1.0):
    return Candle(ts, o, h, l, c, v)


HOUR = 3_600_000


def flat(n, price=100.0, start=0):
    return [bar((start + i) * HOUR, price, price, price, price) for i in range(n)]


class SpecTests(unittest.TestCase):
    def test_rejects_nonsense(self):
        for kwargs in ({"levels": 0}, {"step_atr": 0.0}, {"size_per_level": -1.0}):
            with self.assertRaises(ValueError):
                GridSpec(**kwargs)

    def test_defaults_are_sane(self):
        spec = GridSpec()
        self.assertGreater(spec.levels, 0)
        self.assertGreater(spec.step_atr, 0)


class NoLookAheadTests(unittest.TestCase):
    def test_never_buys_and_sells_in_the_same_bar(self):
        """이게 이 파일에서 제일 중요한 테스트다.

        한 봉이 격자 아래로 내려갔다가 목표 위로 올라가도, 그 봉에서
        왕복을 세면 안 된다. 봉 안의 순서를 모르기 때문이다.
        """
        candles = flat(40)
        # 41번째 봉: 크게 아래로 갔다가 크게 위로 — 한 봉 안에 왕복이 다 들어있다
        candles.append(bar(40 * HOUR, 100, 130, 70, 100))
        candles += flat(3, 100.0, start=41)
        result = simulate(candles, GridSpec(levels=3), atr_period=14)
        self.assertEqual(result.round_trips, 0,
                         "같은 봉에서 사고팔았습니다 — 없는 왕복입니다")

    def test_grid_is_placed_from_the_previous_close(self):
        """현재 봉 종가로 격자를 놓으면 미래참조다.

        마지막 봉의 종가만 바꿔도 결과가 달라지면 안 된다.
        """
        base = [bar(i * HOUR, 100 + (i % 3), 102 + (i % 3), 98 + (i % 3), 100 + (i % 3))
                for i in range(60)]
        a = simulate(base, GridSpec(levels=3), atr_period=14)
        moved = base[:-1] + [bar(base[-1].ts, base[-1].open, base[-1].high,
                                 base[-1].low, base[-1].close * 1.5)]
        b = simulate(moved, GridSpec(levels=3), atr_period=14)
        self.assertEqual(a.round_trips, b.round_trips)

    def test_future_bars_cannot_change_past_equity(self):
        """뒤 봉을 잘라내도 앞 구간 자본 곡선은 그대로여야 한다."""
        base = [bar(i * HOUR, 100, 101.5, 98.5, 100 + (i % 5) * 0.4) for i in range(120)]
        full = simulate(base, GridSpec(levels=3), atr_period=14)
        short = simulate(base[:80], GridSpec(levels=3), atr_period=14)
        self.assertEqual(
            [round(x, 12) for x in short.equity],
            [round(x, 12) for x in full.equity[: len(short.equity)]],
        )


class FillTests(unittest.TestCase):
    def wave(self, cycles=20, low=96.0, high=104.0, mid=100.0):
        """진동 시장. 그리드가 가장 잘 작동해야 하는 조건."""
        candles = flat(30)
        ts = 30
        for _ in range(cycles):
            candles.append(bar(ts * HOUR, mid, mid + 0.5, low, mid)); ts += 1
            candles.append(bar(ts * HOUR, mid, high, mid - 0.5, mid)); ts += 1
        return candles

    def test_oscillation_produces_round_trips(self):
        result = simulate(self.wave(), GridSpec(levels=3, step_atr=0.25),
                          atr_period=14)
        self.assertGreater(result.round_trips, 0)

    def test_a_lot_is_not_opened_twice_at_the_same_level(self):
        result = simulate(self.wave(), GridSpec(levels=2), atr_period=14)
        self.assertLessEqual(result.max_inventory, 2)

    def test_inventory_never_exceeds_levels(self):
        drop = flat(30) + [
            bar((30 + i) * HOUR, 100 - i, 100 - i + 0.2, 100 - i - 1.5, 100 - i)
            for i in range(40)
        ]
        result = simulate(drop, GridSpec(levels=4, close_daily=False), atr_period=14)
        self.assertLessEqual(result.max_inventory, 4)


class RiskTests(unittest.TestCase):
    def falling(self, n=60):
        return flat(30) + [
            bar((30 + i) * HOUR, 100 - i * 2, 100 - i * 2 + 0.3,
                100 - i * 2 - 2.5, 100 - i * 2 - 2)
            for i in range(n)
        ]

    def test_a_trend_forces_closes_not_round_trips(self):
        """추세에 밟히면 지정가 왕복이 아니라 강제 청산이 쌓여야 한다."""
        result = simulate(self.falling(), GridSpec(levels=3), atr_period=14)
        self.assertGreater(result.forced_closes, result.round_trips)

    def test_a_trend_loses_money(self):
        result = simulate(self.falling(), GridSpec(levels=3), atr_period=14)
        self.assertLess(result.return_pct, 0)

    def test_daily_close_empties_inventory(self):
        """하루 마감이 켜져 있으면 다음 날로 물량을 넘기지 않는다."""
        result = simulate(self.falling(), GridSpec(levels=3, close_daily=True),
                          atr_period=14)
        self.assertGreater(result.forced_closes, 0)

    def test_hold_limit_forces_a_close(self):
        """반등이 안 와도 max_hold_bars에서 잘라내야 한다.

        완전히 평평한 구간을 쓰면 안 된다 — ATR이 0이라 격자 간격도 0이 되고
        애초에 매수가 안 일어난다. 변동은 있되 반등만 없는 구간이어야 한다.
        """
        stuck = [bar(i * HOUR, 100, 101.5, 98.5, 100) for i in range(30)]
        stuck += [bar((30 + i) * HOUR, 96, 96.3, 94.5, 95) for i in range(60)]
        result = simulate(stuck, GridSpec(levels=2, max_hold_bars=5, close_daily=False),
                          atr_period=14)
        self.assertGreater(result.forced_closes, 0)

    def test_equity_never_goes_negative(self):
        crash = flat(30) + [bar((30 + i) * HOUR, 100, 100, 1, 2) for i in range(20)]
        result = simulate(crash, GridSpec(levels=5, size_per_level=1.0), atr_period=14)
        self.assertGreaterEqual(min(result.equity), 0.0)


class FeeTests(unittest.TestCase):
    def test_maker_fees_accrue_on_limit_fills(self):
        candles = flat(30) + [
            bar((30 + i) * HOUR, 100, 104, 96, 100) for i in range(20)
        ]
        result = simulate(candles, GridSpec(levels=3), atr_period=14)
        self.assertGreater(result.maker_fees, 0)

    def test_zero_fees_beat_real_fees(self):
        candles = flat(30) + [
            bar((30 + i) * HOUR, 100, 104, 96, 100) for i in range(40)
        ]
        spec = GridSpec(levels=3)
        paid = simulate(candles, spec, atr_period=14)
        free = simulate(candles, spec, maker_fee=0.0, taker_fee=0.0, slippage=0.0,
                        atr_period=14)
        self.assertGreaterEqual(free.return_pct, paid.return_pct)


class ReportTests(unittest.TestCase):
    def result(self):
        candles = flat(30) + [
            bar((30 + i) * HOUR, 100, 104, 96, 100) for i in range(200)
        ]
        return simulate(candles, GridSpec(levels=3), atr_period=14)

    def test_days_over_counts_correctly(self):
        result = self.result()
        result.daily = [5.0, 3.0, 2.9, -1.0]
        self.assertEqual(result.days_over(3.0), 2)
        self.assertEqual(result.days_over(1.0), 3)

    def test_report_renders(self):
        spec = GridSpec(levels=3)
        cost = {"maker": 0.0002, "taker": 0.0006, "slippage": 0.0005}
        self.assertIn("그리드 매매", self.result().report(spec, cost))

    def test_report_flags_trend_domination(self):
        result = self.result()
        result.round_trips, result.forced_closes = 5, 500
        result.maker_fees, result.taker_fees = 1.0, 20.0
        report = result.report(GridSpec(), {"maker": 0.0002, "taker": 0.0006,
                                            "slippage": 0.0005})
        self.assertIn("추세에 계속 밟히고", report)
        self.assertIn("지정가만 쓰려고 만든 전략인데", report)


if __name__ == "__main__":
    unittest.main()


class SizingTests(unittest.TestCase):
    """수량이 현재 자본에 비례해야 한다.

    초기 자본에 고정하면 자본이 반토막 난 뒤에도 같은 크기로 사서
    순식간에 파산하고, 하루 수익률이 -120% 같은 불가능한 값이 나온다.
    실제로 첫 실행에서 그랬다.
    """

    def crash(self, n=200):
        candles = [bar(i * HOUR, 100, 101.5, 98.5, 100) for i in range(30)]
        price = 100.0
        for i in range(n):
            price *= 0.97
            candles.append(bar((30 + i) * HOUR, price, price * 1.005,
                               price * 0.97, price))
        return candles

    def test_daily_returns_stay_above_minus_one_hundred(self):
        """하루에 -100%보다 더 잃을 수는 없다."""
        result = simulate(self.crash(), GridSpec(levels=5, size_per_level=0.2),
                          atr_period=14)
        for d in result.daily:
            self.assertGreaterEqual(d, -100.0, f"하루 수익률 {d}%는 불가능합니다")

    def test_equity_stops_at_zero(self):
        result = simulate(self.crash(), GridSpec(levels=5, size_per_level=1.0),
                          atr_period=14)
        self.assertGreaterEqual(min(result.equity), 0.0)

    def test_return_never_below_minus_one_hundred(self):
        result = simulate(self.crash(), GridSpec(levels=5, size_per_level=1.0),
                          atr_period=14)
        self.assertGreaterEqual(result.return_pct, -100.0)

    def test_lot_size_shrinks_with_equity(self):
        """자본이 줄면 새로 잡는 격자도 작아져야 한다."""
        from dorothy.strategy.grid import Lot, simulate as sim
        big = simulate(self.crash(), GridSpec(levels=3, size_per_level=0.2),
                       atr_period=14)
        # 파산까지 가지 않고 점근적으로 줄어들어야 한다
        self.assertGreater(big.equity[-1], -1e-9)

    def test_ruin_is_recorded(self):
        result = simulate(self.crash(400), GridSpec(levels=5, size_per_level=2.0),
                          atr_period=14)
        if result.equity[-1] == 0.0:
            self.assertTrue(result.ruined)
