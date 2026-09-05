"""펀딩비 수취 시뮬레이터.

**이 파일이 막는 사고:**
델타 중립은 "가격 위험이 없다"로 읽히기 쉽다. 총자산은 그렇지만
**숏 다리는 따로 청산된다.** 그리고 펀딩은 명목가에 붙는데 자본은
현물과 증거금으로 쪼개지므로, 명목가 수익률을 계좌 수익률로 착각하면
실제의 두 배를 기대하게 된다.
"""

import unittest

from dorothy.backtest.carry import (
    CarryConfig,
    breakeven_rate,
    simulate,
    yield_on_capital,
)

DAY = 86_400_000
EIGHT_H = DAY // 3


def flat_prices(n_days=365, price=50_000.0):
    return [(i * EIGHT_H, price) for i in range(n_days * 3 + 1)]


def funding_series(n, rate, start=0):
    return [(start + i * EIGHT_H, rate) for i in range(n)]


def drifting_prices(n_periods, start=50_000.0, per_period=0.0):
    return [(i * EIGHT_H, start * (1 + per_period) ** i) for i in range(n_periods + 1)]


class NeutralityTests(unittest.TestCase):
    """가격이 움직여도 결과가 같아야 델타 중립이다."""

    def test_price_direction_does_not_change_the_result(self):
        cfg = CarryConfig(leverage=1.0)
        f = funding_series(90, 0.0001)
        up = simulate(drifting_prices(90, per_period=0.002), f, equity=10_000, cfg=cfg)
        down = simulate(drifting_prices(90, per_period=-0.002), f, equity=10_000, cfg=cfg)
        self.assertAlmostEqual(up.final_equity, down.final_equity, delta=up.final_equity * 0.02)

    def test_a_flat_market_with_zero_funding_only_loses_fees(self):
        cfg = CarryConfig(leverage=1.0)
        r = simulate(flat_prices(30), funding_series(90, 0.0), equity=10_000, cfg=cfg)
        self.assertLess(r.final_equity, 10_000)
        self.assertAlmostEqual(r.net_pnl, -r.fees, delta=1.0)


class FundingAccrualTests(unittest.TestCase):
    def test_positive_funding_is_collected(self):
        r = simulate(flat_prices(30), funding_series(90, 0.0001), equity=10_000,
                     cfg=CarryConfig(leverage=1.0))
        self.assertGreater(r.funding_collected, 0)
        self.assertEqual(r.funding_paid, 0)

    def test_negative_funding_is_paid_not_collected(self):
        """펀딩이 음수면 **내가 낸다.** 부호를 뒤집으면 손실이 수익으로 보인다."""
        r = simulate(flat_prices(30), funding_series(90, -0.0001), equity=10_000,
                     cfg=CarryConfig(leverage=1.0))
        self.assertEqual(r.funding_collected, 0)
        self.assertGreater(r.funding_paid, 0)
        self.assertLess(r.final_equity, 10_000)
        self.assertAlmostEqual(r.negative_share, 100.0, places=6)

    def test_funding_is_charged_on_notional_not_on_equity(self):
        """레버리지 1배면 명목가는 자본의 절반이다.

        자본에 붙이면 수익이 두 배로 나온다 — 가장 흔한 착각이다.
        """
        r = simulate(flat_prices(120), funding_series(360, 0.0001), equity=10_000,
                     cfg=CarryConfig(leverage=1.0, spot_fee=0, perp_fee=0))
        # 명목가 5,000 × 0.01% × 360회 = 180
        self.assertAlmostEqual(r.funding_collected, 180.0, delta=1.0)


class CapitalSplitTests(unittest.TestCase):
    def test_yield_is_halved_at_one_times_leverage(self):
        self.assertAlmostEqual(yield_on_capital(0.0001, 1.0), 10.9575 / 2, places=3)

    def test_more_leverage_approaches_the_headline_rate(self):
        ys = [yield_on_capital(0.0001, L) for L in (1, 2, 3, 5, 10)]
        self.assertEqual(ys, sorted(ys))
        self.assertLess(ys[-1], 10.9575, "명목가 수익률을 넘을 수는 없습니다")

    def test_a_realistic_rate_is_single_digit_annually(self):
        """기대치를 고정한다. 펀딩 수취는 하루 3%가 나오는 구조가 아니다."""
        self.assertLess(yield_on_capital(0.0001, 3.0), 9.0)


class LiquidationTests(unittest.TestCase):
    """총자산이 안전해도 숏 다리는 따로 죽는다."""

    def test_a_big_rally_liquidates_a_leveraged_short_leg(self):
        prices = drifting_prices(120, per_period=0.006)   # 누적 2배 상승
        r = simulate(prices, funding_series(120, 0.0001), equity=10_000,
                     cfg=CarryConfig(leverage=5.0, allow_topup=False))
        self.assertTrue(r.liquidated, "5배 숏이 2배 상승에서 살아남았습니다")
        self.assertIsNotNone(r.liquidated_at)
        self.assertIn("청산", r.render())

    def test_even_one_times_leverage_dies_on_a_doubling(self):
        """**보충하지 않으면 1배도 죽는다.**

        1배면 명목가 = 증거금이다. 가격이 2배가 되면 숏 미실현이 증거금
        전액과 같아진다. "1배니까 안전하다"는 틀렸다.
        """
        prices = drifting_prices(120, per_period=0.006)   # 누적 +105%
        r = simulate(prices, funding_series(120, 0.0001), equity=10_000,
                     cfg=CarryConfig(leverage=1.0, allow_topup=False))
        self.assertTrue(r.liquidated)

    def test_one_times_leverage_survives_a_fifty_percent_rally(self):
        """과잉 경고도 버그다. 50% 상승은 1배로 버틴다."""
        prices = drifting_prices(120, per_period=0.0034)  # 누적 +50%
        r = simulate(prices, funding_series(120, 0.0001), equity=10_000,
                     cfg=CarryConfig(leverage=1.0, allow_topup=False))
        self.assertFalse(r.liquidated)

    def test_higher_leverage_dies_to_a_smaller_rally(self):
        """청산까지 견디는 상승폭이 레버리지에 반비례하는지 확인한다."""
        def survives(leverage, total_rise):
            n = 120
            step = (1 + total_rise) ** (1 / n) - 1
            r = simulate(drifting_prices(n, per_period=step),
                         funding_series(n, 0.0), equity=10_000,
                         cfg=CarryConfig(leverage=leverage, allow_topup=False,
                                         spot_fee=0, perp_fee=0))
            return not r.liquidated

        self.assertTrue(survives(1.0, 0.80))    # 1배는 +80%까지 버틴다
        self.assertFalse(survives(1.0, 1.20))
        self.assertTrue(survives(3.0, 0.20))    # 3배는 +20%에서 아직 산다
        self.assertFalse(survives(3.0, 0.50))   # +50%면 죽는다

    def test_topup_prevents_liquidation_but_is_counted(self):
        prices = drifting_prices(120, per_period=0.006)
        r = simulate(prices, funding_series(120, 0.0001), equity=10_000,
                     cfg=CarryConfig(leverage=5.0, allow_topup=True))
        self.assertFalse(r.liquidated)
        self.assertGreater(r.topups, 0, "보충 없이 살아남았다면 계산이 틀렸습니다")
        self.assertGreater(r.topup_amount, 0)

    def test_the_margin_low_water_mark_is_recorded(self):
        prices = drifting_prices(60, per_period=0.004)
        r = simulate(prices, funding_series(60, 0.0001), equity=10_000,
                     cfg=CarryConfig(leverage=3.0))
        self.assertLess(r.min_margin_ratio, 1.0)


class BreakevenTests(unittest.TestCase):
    def test_a_short_hold_cannot_cover_the_round_trip(self):
        cfg = CarryConfig()
        need = breakeven_rate(cfg, holding_days=1)
        self.assertGreater(need, 0.001, "하루만 들고 나면 0.1% 넘는 펀딩이 필요합니다")

    def test_a_long_hold_amortizes_the_fees(self):
        cfg = CarryConfig()
        self.assertLess(breakeven_rate(cfg, holding_days=365), 0.00001)

    def test_breakeven_falls_as_the_hold_lengthens(self):
        cfg = CarryConfig()
        needs = [breakeven_rate(cfg, d) for d in (1, 7, 30, 365)]
        self.assertEqual(needs, sorted(needs, reverse=True))


class ValidationTests(unittest.TestCase):
    def test_leverage_below_one_is_rejected(self):
        with self.assertRaises(ValueError):
            simulate(flat_prices(10), funding_series(30, 0.0001), equity=1000,
                     cfg=CarryConfig(leverage=0.5))

    def test_no_prices_is_rejected(self):
        with self.assertRaises(ValueError):
            simulate([], funding_series(30, 0.0001), equity=1000)

    def test_zero_equity_is_rejected(self):
        with self.assertRaises(ValueError):
            simulate(flat_prices(10), funding_series(30, 0.0001), equity=0)

    def test_funding_before_entry_is_ignored(self):
        prices = flat_prices(30)
        f = [(-5 * EIGHT_H, 0.5)] + funding_series(30, 0.0001)
        r = simulate(prices, f, equity=10_000)
        self.assertEqual(r.periods, 30)


class TopupModeTests(unittest.TestCase):
    """증거금이 모자랄 때 무엇을 하느냐가 생사를 가른다."""

    @staticmethod
    def _rally(n=200, total=3.0):
        step = (1 + total) ** (1 / n) - 1
        return drifting_prices(n, per_period=step)

    def test_selling_spot_to_defend_a_short_destroys_the_account(self):
        """현물만 팔면 순숏으로 기울고, 상승이 이어지면 무너진다.

        '청산' 깃발이 꼭 켜지는 건 아니다 — 형식적 청산 없이 그냥 녹기도
        한다. 그래서 깃발이 아니라 **결과**로 확인한다.
        """
        r = simulate(self._rally(), funding_series(200, 0.0001), equity=10_000,
                     cfg=CarryConfig(leverage=1.0, topup_mode="sell_spot"))
        self.assertLess(r.return_pct, -50, "가격 3배 상승에서 멀쩡했다면 계산이 틀렸습니다")
        self.assertGreater(r.hedge_drift, 50, "헤지가 어긋난 것이 기록되지 않았습니다")

    def test_deleveraging_beats_selling_spot_on_the_same_path(self):
        """**핵심 비교.** 같은 가격 경로에서 대응 방식만 바꾼다."""
        path, f = self._rally(), funding_series(200, 0.0001)
        safe = simulate(path, f, equity=10_000,
                        cfg=CarryConfig(leverage=1.0, topup_mode="deleverage"))
        naive = simulate(path, f, equity=10_000,
                         cfg=CarryConfig(leverage=1.0, topup_mode="sell_spot"))
        self.assertGreater(safe.return_pct, naive.return_pct + 50)
        self.assertEqual(safe.hedge_drift, 0.0)

    def test_deleveraging_never_liquidates(self):
        """양 다리를 같이 줄이면 원리상 청산되지 않는다 — 작아질 뿐이다."""
        r = simulate(self._rally(), funding_series(200, 0.0001), equity=10_000,
                     cfg=CarryConfig(leverage=1.0, topup_mode="deleverage"))
        self.assertFalse(r.liquidated)
        self.assertEqual(r.hedge_drift, 0.0, "중립이 깨졌습니다")

    def test_deleveraging_shrinks_the_position(self):
        r = simulate(self._rally(), funding_series(200, 0.0001), equity=10_000,
                     cfg=CarryConfig(leverage=3.0, topup_mode="deleverage"))
        self.assertGreater(r.topups, 0)
        self.assertGreater(r.closed_fraction, 0.0)

    def test_closed_fraction_never_exceeds_one(self):
        """누적합으로 세면 154%가 나온다. 원래 대비 비율이어야 한다."""
        r = simulate(self._rally(400, total=8.0), funding_series(400, 0.0001),
                     equity=10_000, cfg=CarryConfig(leverage=3.0))
        self.assertLessEqual(r.closed_fraction, 1.0 + 1e-9)
        self.assertGreaterEqual(r.closed_fraction, 0.0)

    def test_a_fully_closed_position_says_so(self):
        r = simulate(self._rally(400, total=20.0), funding_series(400, 0.0001),
                     equity=10_000, cfg=CarryConfig(leverage=3.0))
        if r.fully_closed:
            self.assertIn("현금", r.render())

    def test_an_unknown_topup_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            simulate(flat_prices(10), funding_series(30, 0.0001), equity=1000,
                     cfg=CarryConfig(topup_mode="pray"))
