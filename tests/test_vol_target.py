"""변동성 타게팅 테스트.

가장 중요한 것은 인과성이다. 배율을 정할 때 현재 봉의 움직임을 보면
"많이 움직인 날에 미리 줄여놨다"가 되어 결과가 통째로 허구가 된다.
"""

import math
import unittest

from dorothy.backtest.vol_target import _RollingVol, Curve, analyse, realized_vol
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


class RollingVolTests(unittest.TestCase):
    """빠른 구현이 느린 구현과 같은 값을 내는지. 어긋나면 결론이 조용히 바뀐다."""

    def test_matches_the_direct_computation(self):
        import math
        closes = [100 * (1 + 0.01 * math.sin(i * 0.7)) for i in range(500)]
        candles = series(closes)
        for lookback in (10, 30, 120):
            rolling = _RollingVol(candles, lookback)
            for index in range(0, 500, 17):
                direct = realized_vol(candles, index, lookback)
                fast = rolling.at(index)
                if direct is None:
                    self.assertIsNone(fast, f"lookback={lookback} index={index}")
                else:
                    self.assertAlmostEqual(direct, fast, places=10,
                                           msg=f"lookback={lookback} index={index}")

    def test_matches_on_a_pure_trend(self):
        """잔파동 없이 일정 비율로만 오르는 구간. 분산이 사실상 0이라
        누적합 방식은 여기서 자릿수가 날아간다. 실제로 이 테스트가 그 버그를 잡았다.
        배율은 변동성에 반비례하므로, 분산이 0으로 잘못 나오면 상한까지 튄다."""
        candles = series([100 * (1.003 ** i) for i in range(400)])
        rolling = _RollingVol(candles, 60)
        for index in (100, 200, 300, 399):
            direct = realized_vol(candles, index, 60)
            fast = rolling.at(index)
            if direct is None:
                self.assertIsNone(fast, f"index={index}")
            else:
                self.assertAlmostEqual(direct, fast, places=12, msg=f"index={index}")

    def test_matches_when_trend_dwarfs_the_wobble(self):
        """추세가 크고 잔파동이 작을수록 자릿수 소실이 심해진다."""
        import math
        for wobble in (1e-3, 1e-5, 1e-7):
            closes, price = [], 100.0
            for i in range(300):
                price *= 1.005 + (wobble if i % 2 else -wobble)
                closes.append(price)
            candles = series(closes)
            rolling = _RollingVol(candles, 60)
            for index in (150, 250):
                direct = realized_vol(candles, index, 60)
                fast = rolling.at(index)
                self.assertIsNotNone(fast, f"wobble={wobble}")
                self.assertAlmostEqual(
                    direct, fast, delta=direct * 1e-6,
                    msg=f"wobble={wobble} index={index}: {direct} vs {fast}")

    def test_flat_series_gives_none_both_ways(self):
        candles = series([100] * 200)
        rolling = _RollingVol(candles, 30)
        self.assertIsNone(rolling.at(100))
        self.assertIsNone(realized_vol(candles, 100, 30))

    def test_none_before_enough_history(self):
        candles = series([100 + i for i in range(200)])
        self.assertIsNone(_RollingVol(candles, 30).at(2))


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

    def test_start_index_makes_baselines_comparable(self):
        """lookback이 달라도 start_index를 맞추면 매수 후 보유가 같아야 한다.

        이게 안 맞으면 lookback이 긴 설정이 앞부분(폭락 구간)을 더 건너뛰어
        더 유리한 기간을 보게 되고, 승률 비교가 통째로 무의미해진다.
        실제로 그 결함 때문에 매수 후 보유 기준선이 +365%에서 +822%까지 움직였다.
        """
        candles = self.wobble(1500, 0.01, drift=0.0003)
        short = analyse(candles, self.cfg, lookback=30, start_index=400)
        long_ = analyse(candles, self.cfg, lookback=240, start_index=400)
        self.assertAlmostEqual(short.hold.return_pct, long_.hold.return_pct, places=8)
        self.assertAlmostEqual(short.hold.max_drawdown_pct,
                               long_.hold.max_drawdown_pct, places=8)
        self.assertEqual(short.start_index, long_.start_index)

    def test_without_start_index_baselines_diverge(self):
        """start_index를 안 주면 실제로 어긋난다는 것도 못 박아 둔다."""
        candles = self.wobble(1500, 0.01, drift=0.0003)
        short = analyse(candles, self.cfg, lookback=30)
        long_ = analyse(candles, self.cfg, lookback=240)
        self.assertNotAlmostEqual(short.hold.return_pct, long_.hold.return_pct, places=2)

    def test_start_index_below_lookback_is_raised_to_it(self):
        candles = self.wobble(600, 0.01)
        result = analyse(candles, self.cfg, lookback=100, start_index=10)
        self.assertEqual(result.start_index, 101)

    def test_start_index_past_the_data_is_rejected(self):
        candles = self.wobble(300, 0.01)
        with self.assertRaises(ValueError):
            analyse(candles, self.cfg, lookback=30, start_index=5000)

    def test_volatility_estimate_uses_bars_before_the_start(self):
        """워밍업 구간은 평가에서 빠지되 변동성 추정에는 쓰여야 한다.
        첫 봉부터 배율이 0이 아니어야 그게 확인된다."""
        candles = self.wobble(800, 0.01)
        result = analyse(candles, self.cfg, lookback=200, start_index=400)
        self.assertGreater(result.targeted.weights[0], 0.0)

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
