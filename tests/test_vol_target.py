"""변동성 타게팅 테스트.

가장 중요한 것은 인과성이다. 배율을 정할 때 현재 봉의 움직임을 보면
"많이 움직인 날에 미리 줄여놨다"가 되어 결과가 통째로 허구가 된다.
"""

import math
import unittest

from dorothy.backtest.vol_target import Curve, analyse, realized_vol
from dorothy.config import Config
from dorothy.models import Candle


def bar(ts, o, h, l, c, v=1.0):
    return Candle(ts, o, h, l, c, v)


def series(closes, step=3600_000):
    return [bar(i * step, c, c, c, c) for i, c in enumerate(closes)]


class RealizedVolTests(unittest.TestCase):
    def test_none_before_enough_history(self):
        candles = series([100] * 10)
        self.assertIsNone(realized_vol(candles, 2, 30))

    def test_flat_series_has_no_volatility(self):
        candles = series([100] * 50)
        self.assertIsNone(realized_vol(candles, 40, 30))

    def test_bigger_swings_give_bigger_vol(self):
        calm = series([100 + (0.1 if i % 2 else -0.1) for i in range(60)])
        wild = series([100 + (5 if i % 2 else -5) for i in range(60)])
        self.assertGreater(realized_vol(wild, 50, 30), realized_vol(calm, 50, 30))

    def test_only_uses_bars_up_to_index(self):
        """이후 봉을 아무리 흔들어도 값이 바뀌면 안 된다."""
        closes = [100 + math.sin(i) for i in range(80)]
        candles = series(closes)
        before = realized_vol(candles, 50, 30)
        shaken = series(closes[:51] + [c * 3 for c in closes[51:]])
        self.assertEqual(before, realized_vol(shaken, 50, 30))


class CurveTests(unittest.TestCase):
    def test_return_pct(self):
        self.assertAlmostEqual(Curve("x", [1.0, 1.5]).return_pct, 50.0)

    def test_max_drawdown(self):
        curve = Curve("x", [1.0, 2.0, 1.0, 1.5])
        self.assertAlmostEqual(curve.max_drawdown_pct, 50.0)

    def test_no_drawdown_on_monotone_rise(self):
        self.assertAlmostEqual(Curve("x", [1.0, 1.1, 1.2]).max_drawdown_pct, 0.0)

    def test_ratio_is_zero_without_drawdown(self):
        self.assertEqual(Curve("x", [1.0, 1.2]).ratio, 0.0)

    def test_empty_curve_is_safe(self):
        curve = Curve("x", [])
        self.assertEqual(curve.return_pct, 0.0)
        self.assertEqual(curve.max_drawdown_pct, 0.0)
        self.assertEqual(curve.realized_vol_pct(365), 0.0)


class AnalyseTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()

    def wobble(self, n, amp, drift=0.0):
        closes, price = [], 100.0
        for i in range(n):
            price *= 1 + drift + (amp if i % 2 else -amp)
            closes.append(price)
        return series(closes)

    def test_weight_shrinks_when_volatility_rises(self):
        """조용한 구간 뒤 시끄러운 구간이 오면 배율이 내려가야 한다."""
        candles = self.wobble(200, 0.002) + self.wobble(200, 0.02)
        for i, c in enumerate(candles):
            candles[i] = bar(i * 3600_000, c.open, c.high, c.low, c.close)
        result = analyse(candles, self.cfg, lookback=30)
        weights = result.targeted.weights
        calm = sum(weights[50:150]) / 100
        wild = sum(weights[-100:]) / 100
        self.assertGreater(calm, wild)

    def test_never_exceeds_max_leverage(self):
        result = analyse(self.wobble(400, 0.0005), self.cfg, max_leverage=2.0)
        self.assertLessEqual(max(result.targeted.weights), 2.0 + 1e-9)

    def test_weights_are_never_negative(self):
        """이건 롱 전용 노출이다. 음수 배율은 숏이 되어 버린다."""
        result = analyse(self.wobble(400, 0.01), self.cfg)
        self.assertGreaterEqual(min(result.targeted.weights), 0.0)

    def test_hold_curve_matches_price_change(self):
        """비교 기준이 정확해야 한다. 여기가 한 봉만 어긋나도
        '타게팅이 이겼다/졌다'가 뒤집힐 수 있다."""
        lookback = 30
        candles = series([100 * (1.01 ** i) for i in range(200)])
        result = analyse(candles, self.cfg, lookback=lookback)
        # 첫 수익률은 candles[lookback] → candles[lookback+1]이므로 기준은 candles[lookback]
        first, last = candles[lookback].close, candles[-1].close
        self.assertAlmostEqual(result.hold.return_pct, (last / first - 1) * 100, places=4)

    def test_both_curves_cover_the_same_bars(self):
        """두 곡선의 길이가 다르면 비교 자체가 성립하지 않는다."""
        result = analyse(self.wobble(300, 0.01), self.cfg, lookback=30)
        self.assertEqual(len(result.hold.equity), len(result.targeted.equity))
        self.assertEqual(len(result.hold.weights), len(result.targeted.weights))

    def test_no_look_ahead_in_weights(self):
        """미래 봉을 바꿔도 그 이전 배율은 그대로여야 한다."""
        candles = self.wobble(400, 0.01)
        base = analyse(candles, self.cfg, lookback=30).targeted.weights
        tail = candles[:250] + [
            bar(c.ts, c.open * 5, c.high * 5, c.low * 5, c.close * 5) for c in candles[250:]
        ]
        shaken = analyse(tail, self.cfg, lookback=30).targeted.weights
        self.assertEqual(base[:200], shaken[:200])

    def test_rebalance_band_reduces_turnover(self):
        candles = self.wobble(600, 0.01)
        tight = analyse(candles, self.cfg, rebalance_band=0.0)
        loose = analyse(candles, self.cfg, rebalance_band=0.50)
        self.assertLess(loose.targeted.rebalances, tight.targeted.rebalances)
        self.assertLess(loose.targeted.fees_paid, tight.targeted.fees_paid)

    def test_fees_are_actually_charged(self):
        result = analyse(self.wobble(400, 0.01), self.cfg, rebalance_band=0.0)
        self.assertGreater(result.targeted.fees_paid, 0)

    def test_perp_charges_funding_on_the_whole_position(self):
        result = analyse(self.wobble(400, 0.01), self.cfg, venue="perp",
                         max_leverage=1.0, rebalance_band=0.0)
        self.assertGreater(result.targeted.funding_paid, 0)

    def test_spot_charges_no_funding_below_one_times(self):
        """현물을 그냥 들고 있는 사람은 펀딩비를 내지 않는다.
        여기를 틀리면 벤치마크와 조건이 어긋나 전략이 부당하게 진다."""
        result = analyse(self.wobble(400, 0.01), self.cfg, venue="spot",
                         max_leverage=1.0, rebalance_band=0.0)
        self.assertEqual(result.targeted.funding_paid, 0.0)

    def test_spot_charges_funding_only_above_one_times(self):
        calm = self.wobble(400, 0.0002)      # 조용해서 배율이 상한까지 올라간다
        result = analyse(calm, self.cfg, venue="spot", max_leverage=3.0,
                         rebalance_band=0.0)
        perp = analyse(calm, self.cfg, venue="perp", max_leverage=3.0,
                       rebalance_band=0.0)
        self.assertGreater(result.targeted.funding_paid, 0)
        self.assertLess(result.targeted.funding_paid, perp.targeted.funding_paid)

    def test_rejects_unknown_venue(self):
        with self.assertRaises(ValueError):
            analyse(self.wobble(200, 0.01), self.cfg, venue="선물아님")

    def test_zero_fee_zero_funding_is_cheaper(self):
        cfg = Config()
        cfg.exchange.taker_fee = 0.0
        cfg.exchange.slippage = 0.0
        cfg.exchange.funding_rate = 0.0
        candles = self.wobble(400, 0.01, drift=0.0005)
        free = analyse(candles, cfg, venue="perp", rebalance_band=0.0)
        paid = analyse(candles, self.cfg, venue="perp", rebalance_band=0.0)
        self.assertGreater(free.targeted.return_pct, paid.targeted.return_pct)

    def test_report_renders(self):
        result = analyse(self.wobble(400, 0.01), self.cfg)
        report = result.report()
        self.assertIn("변동성 타게팅", report)
        self.assertIn("펀딩", report)

    def test_verdict_reports_failure_honestly(self):
        """낙폭이 안 줄면 줄었다고 말하면 안 된다."""
        result = analyse(self.wobble(400, 0.01), self.cfg)
        result.targeted.equity = [1.0, 0.4, 1.0]      # 낙폭 60%
        result.hold.equity = [1.0, 0.9, 1.0]          # 낙폭 10%
        self.assertIn("낙폭이 줄지 않았습니다", result.report())


if __name__ == "__main__":
    unittest.main()
