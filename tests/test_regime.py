"""시장 국면 판별 · 국면 필터 테스트.

분산비(VR)가 실제로 추세성/회귀성을 구분하는지가 핵심이다.
검증을 위해 자기상관 계수를 아는 인공 시계열을 만들어 쓴다 —
합성 캔들로만 테스트하면 지표가 고장나도 알 수 없다.
"""

import math
import random
import unittest

from dorothy.analysis.regime import (
    Trendiness,
    Volatility,
    autocorrelation,
    classify,
    variance_ratio,
)
from dorothy.backtest import regime_report
from dorothy.config import Config
from dorothy.data.loader import synthetic
from dorothy.models import Action, Candle, Position, Side
from dorothy.strategy.base import get_strategy

HOUR = 3_600_000


def candles_from(prices):
    return [Candle(i * HOUR, p, p * 1.001, p * 0.999, p, 1.0) for i, p in enumerate(prices)]


def ar1(phi, n=400, sigma=0.01, seed=1):
    """자기상관 계수 phi를 아는 시계열. phi>0이면 추세, phi<0이면 회귀."""
    rng = random.Random(seed)
    prices, price, r = [], 100.0, 0.0
    for _ in range(n):
        r = phi * r + rng.gauss(0, sigma)
        price *= math.exp(r)
        prices.append(price)
    return prices


class TestVarianceRatio(unittest.TestCase):
    def test_positive_autocorrelation_gives_ratio_above_one(self):
        self.assertGreater(variance_ratio(ar1(0.6)), 1.3)

    def test_negative_autocorrelation_gives_ratio_below_one(self):
        self.assertLess(variance_ratio(ar1(-0.6)), 0.7)

    def test_random_walk_sits_near_one(self):
        self.assertAlmostEqual(variance_ratio(ar1(0.0)), 1.0, delta=0.25)

    def test_ratio_increases_with_autocorrelation(self):
        """계수가 커질수록 분산비도 커져야 한다 — 단조성이 지표의 신뢰도다."""
        ratios = [variance_ratio(ar1(phi)) for phi in (-0.6, -0.3, 0.0, 0.3, 0.6)]
        for a, b in zip(ratios, ratios[1:]):
            self.assertLess(a, b)

    def test_perfect_zigzag_is_strongly_reverting(self):
        prices = [100 + (3 if i % 2 == 0 else -3) for i in range(300)]
        self.assertLess(variance_ratio(prices), 0.3)

    def test_constant_returns_do_not_produce_garbage(self):
        """로그수익률이 일정하면 분산이 0에 붙는다.

        가드가 없으면 부동소수 노이즈가 증폭돼 단조 상승이 '회귀장'으로
        분류된다 — 실제로 검산에서 잡은 버그다.
        """
        prices = [100 * 1.002**i for i in range(300)]
        self.assertAlmostEqual(variance_ratio(prices), 1.0, places=6)

    def test_short_series_falls_back_to_one(self):
        self.assertEqual(variance_ratio([100.0, 101.0, 102.0]), 1.0)

    def test_invalid_k_is_rejected(self):
        with self.assertRaises(ValueError):
            variance_ratio(ar1(0.0), k=1)


class TestAutocorrelation(unittest.TestCase):
    def test_sign_matches_the_generating_process(self):
        self.assertGreater(autocorrelation(ar1(0.6)), 0.2)
        self.assertLess(autocorrelation(ar1(-0.6)), -0.2)

    def test_short_series_returns_zero(self):
        self.assertEqual(autocorrelation([100.0, 101.0]), 0.0)


class TestClassify(unittest.TestCase):
    def test_labels_match_the_generating_process(self):
        self.assertIs(classify(candles_from(ar1(0.6))).trendiness, Trendiness.TRENDING)
        self.assertIs(classify(candles_from(ar1(-0.6))).trendiness, Trendiness.REVERTING)
        self.assertIs(classify(candles_from(ar1(0.0))).trendiness, Trendiness.RANDOM)

    def test_ambiguous_series_is_left_as_random(self):
        """1 근처를 억지로 판정하면 국면 판별이 또 하나의 노이즈가 된다."""
        self.assertIs(classify(candles_from(ar1(0.05))).trendiness, Trendiness.RANDOM)

    def test_volatility_state_is_reported(self):
        regime = classify(synthetic(600, seed=5, timeframe="1h", start=65000.0))
        self.assertIn(regime.volatility, set(Volatility))
        self.assertGreaterEqual(regime.atr_percentile, 0.0)
        self.assertLessEqual(regime.atr_percentile, 100.0)

    def test_label_is_human_readable(self):
        self.assertIn("/", classify(candles_from(ar1(0.6))).label)

    def test_suits_flags_agree_with_trendiness(self):
        trending = classify(candles_from(ar1(0.6)))
        self.assertTrue(trending.suits_trend_following)
        self.assertFalse(trending.suits_mean_reversion)

    def test_is_causal(self):
        """미래 캔들을 바꿔도 과거 시점 판정은 그대로여야 한다."""
        candles = synthetic(1000, seed=5, timeframe="1h", start=65000.0)
        cut = 600
        tampered = candles[:cut] + [
            Candle(c.ts, c.open * 3, c.high * 3, c.low * 3, c.close * 3, c.volume)
            for c in candles[cut:]
        ]
        a = classify(candles[:cut])
        b = classify(tampered[:cut])
        self.assertEqual(a.trendiness, b.trendiness)
        self.assertAlmostEqual(a.variance_ratio, b.variance_ratio)


class TestRegimeFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candles = synthetic(3000, seed=5, timeframe="1h", start=65000.0)
        cls.base = {"channel": 40, "exit_channel": 20}

    def _count_entries(self, strategy):
        return sum(
            1 for i in range(strategy.warmup, len(self.candles), 3)
            if strategy.generate(self.candles[: i + 1], None).action is not Action.HOLD
        )

    def test_filter_reduces_entries(self):
        plain = get_strategy("donchian", **self.base)
        filtered = get_strategy(
            "regime_filter", base="donchian", base_params=self.base,
            allow_trendiness=["trending"],
        )
        self.assertLess(self._count_entries(filtered), self._count_entries(plain))

    def test_entries_carry_the_regime_that_allowed_them(self):
        strategy = get_strategy(
            "regime_filter", base="donchian", base_params=self.base,
            allow_trendiness=["trending"],
        )
        for i in range(strategy.warmup, len(self.candles), 5):
            sig = strategy.generate(self.candles[: i + 1], None)
            if sig.action is Action.HOLD:
                continue
            with self.subTest(bar=i):
                self.assertIn("추세", sig.meta["regime"])

    def test_exits_are_never_blocked(self):
        """국면이 나빠도 나가는 문은 열려 있어야 한다."""
        strategy = get_strategy(
            "regime_filter", base="donchian", base_params=self.base,
            allow_trendiness=["trending"],
        )
        position = Position("BTC/USDT:USDT", Side.LONG, 1.0, 65000.0)
        actions = {
            strategy.generate(self.candles[: i + 1], position).action
            for i in range(strategy.warmup, 2000, 7)
        }
        self.assertIn(Action.EXIT, actions)

    def test_unknown_regime_name_is_rejected(self):
        with self.assertRaises(ValueError):
            get_strategy("regime_filter", allow_trendiness=["sideways"])
        with self.assertRaises(ValueError):
            get_strategy("regime_filter", block_volatility=["insane"])

    def test_cannot_wrap_itself(self):
        with self.assertRaises(ValueError):
            get_strategy("regime_filter", base="regime_filter")


class TestRegimeReport(unittest.TestCase):
    def test_report_splits_trades_across_regimes(self):
        cfg = Config()
        cfg.mode = "backtest"
        cfg.initial_equity = 200.0
        cfg.exchange.timeframe = "1h"
        candles = synthetic(3000, seed=5, timeframe="1h", start=65000.0)
        strategy = get_strategy("donchian", channel=40, exit_channel=20)
        result = regime_report.analyse(candles, strategy, cfg)

        self.assertGreater(result.total_trades, 0)
        classified = sum(b.n for b in result.by_trendiness.values())
        self.assertGreater(classified, 0)
        self.assertLessEqual(classified, result.total_trades)
        self.assertIn("국면별 성과", result.report())

    def test_report_handles_a_strategy_with_no_trades(self):
        cfg = Config()
        cfg.mode = "backtest"
        cfg.initial_equity = 200.0
        cfg.exchange.timeframe = "1h"
        candles = synthetic(1500, seed=5, timeframe="1h", start=65000.0)
        strategy = get_strategy("donchian", channel=400, exit_channel=200)
        result = regime_report.analyse(candles, strategy, cfg)
        self.assertIn("국면", result.report())


if __name__ == "__main__":
    unittest.main()
