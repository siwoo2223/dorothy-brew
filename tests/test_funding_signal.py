"""펀딩률 시계열 · 펀딩 역방향 전략 테스트.

**핵심은 인과성이다.** 펀딩은 8시간마다 확정되므로, 어떤 시점에서 알 수 있는
값은 그 이전에 확정된 마지막 값뿐이다. 다음 펀딩률을 미리 쓰면
백테스트가 통째로 거짓이 된다.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dorothy.backtest import engine as bt
from dorothy.config import Config
from dorothy.data.funding import (
    FundingPoint,
    FundingSeries,
    load_csv,
    save_csv,
    synthetic as synthetic_funding,
)
from dorothy.data.loader import synthetic as synthetic_candles
from dorothy.exchange.paper import PaperExchange
from dorothy.models import Action, Candle, Side
from dorothy.strategy.base import get_strategy

HOUR = 3_600_000
SYM = "BTC/USDT:USDT"


def series(rates, start=0, step=8 * HOUR):
    return FundingSeries([FundingPoint(start + i * step, r) for i, r in enumerate(rates)])


class TestCausality(unittest.TestCase):
    """이 클래스가 깨지면 펀딩 관련 백테스트는 전부 무효다."""

    def setUp(self):
        self.s = series([0.0001, 0.0002, 0.0003, 0.0004, 0.0005])

    def test_returns_the_last_confirmed_rate(self):
        # 2번째 확정(16h) 후 3시간 시점 → 2번째 값이어야 한다
        self.assertAlmostEqual(self.s.rate_at(16 * HOUR + 3 * HOUR), 0.0003)

    def test_never_returns_a_future_rate(self):
        for probe_hours in range(0, 40):
            ts = probe_hours * HOUR
            rate = self.s.rate_at(ts)
            if rate is None:
                continue
            confirmed = [p.rate for p in self.s.points if p.ts <= ts]
            with self.subTest(hour=probe_hours):
                self.assertEqual(rate, confirmed[-1])

    def test_exact_confirmation_time_is_included(self):
        self.assertAlmostEqual(self.s.rate_at(8 * HOUR), 0.0002)

    def test_before_the_first_point_returns_none(self):
        self.assertIsNone(self.s.rate_at(-1))

    def test_empty_series(self):
        self.assertIsNone(FundingSeries([]).rate_at(0))
        self.assertFalse(FundingSeries([]))

    def test_history_only_includes_the_past(self):
        history = self.s.history_at(16 * HOUR, 10)
        self.assertEqual(history, [0.0001, 0.0002, 0.0003])

    def test_unsorted_input_is_sorted(self):
        s = FundingSeries([FundingPoint(100, 0.2), FundingPoint(0, 0.1)])
        self.assertEqual([p.ts for p in s.points], [0, 100])


class TestStatistics(unittest.TestCase):
    def test_zscore_is_positive_for_an_unusually_high_rate(self):
        s = series([0.0001] * 40 + [0.002])
        z = s.zscore_at(s.points[-1].ts)
        self.assertGreater(z, 3.0)

    def test_zscore_is_negative_for_an_unusually_low_rate(self):
        s = series([0.0001] * 40 + [-0.002])
        self.assertLess(s.zscore_at(s.points[-1].ts), -3.0)

    def test_zscore_needs_enough_history(self):
        self.assertIsNone(series([0.0001] * 5).zscore_at(100 * HOUR))

    def test_constant_series_has_zero_zscore(self):
        s = series([0.0001] * 40)
        self.assertAlmostEqual(s.zscore_at(s.points[-1].ts), 0.0)

    def test_percentile_bounds(self):
        s = synthetic_funding(0, 200)
        p = s.percentile_at(s.points[-1].ts)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 100.0)

    def test_average_smooths_a_single_spike(self):
        s = series([0.0001] * 10 + [0.005])
        self.assertLess(s.average_at(s.points[-1].ts, 3), 0.005)


class TestCsvRoundTrip(unittest.TestCase):
    def test_save_and_load(self):
        original = synthetic_funding(0, 50)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.csv"
            save_csv(original, path)
            loaded = load_csv(path)
        self.assertEqual(len(loaded), len(original))
        for a, b in zip(original.points, loaded.points):
            self.assertEqual(a.ts, b.ts)
            self.assertAlmostEqual(a.rate, b.rate)

    def test_alternate_headers_are_accepted(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.csv"
            path.write_text("timestamp,fundingRate\n0,0.0001\n28800000,0.0002\n", encoding="utf-8")
            loaded = load_csv(path)
        self.assertEqual(len(loaded), 2)

    def test_unreadable_file_raises(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.csv"
            path.write_text("a,b\n1,2\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_csv(path)


class TestPaperExchangeUsesRealRates(unittest.TestCase):
    def test_actual_rate_overrides_the_constant(self):
        s = series([0.001] * 5)          # 상수(0.0001)보다 10배 큰 실제값
        px = PaperExchange(
            equity=1000, taker_fee=0.0, slippage=0.0,
            funding_rate=0.0001, funding_series=s,
        )
        px.feed_candle(Candle(0, 100, 100, 100, 100, 1.0))
        px.open_position(SYM, Side.LONG, 10.0, stop_loss=50)
        px.feed_candle(Candle(8 * HOUR, 100, 100, 100, 100, 1.0))
        self.assertAlmostEqual(px._funding_accrued, 1.0)    # 1000 × 0.001

    def test_falls_back_to_the_constant_before_the_series_starts(self):
        s = series([0.001] * 5, start=100 * HOUR)
        px = PaperExchange(
            equity=1000, taker_fee=0.0, slippage=0.0,
            funding_rate=0.0001, funding_series=s,
        )
        px.feed_candle(Candle(0, 100, 100, 100, 100, 1.0))
        px.open_position(SYM, Side.LONG, 10.0, stop_loss=50)
        px.feed_candle(Candle(8 * HOUR, 100, 100, 100, 100, 1.0))
        self.assertAlmostEqual(px._funding_accrued, 0.1)    # 상수 사용


class TestFundingBiasStrategy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candles = synthetic_candles(3000, seed=5, timeframe="1h", start=65000.0)
        cls.funding = synthetic_funding(cls.candles[0].ts, len(cls.candles) // 8 + 10, seed=7)

    def _strategy(self, **kw):
        s = get_strategy("funding_bias", **kw)
        s.set_funding(self.funding)
        return s

    def test_no_signals_without_funding_data(self):
        s = get_strategy("funding_bias")
        sig = s.generate(self.candles, None)
        self.assertIs(sig.action, Action.HOLD)
        self.assertIn("펀딩률", sig.reason)

    def test_high_funding_produces_a_short(self):
        s = self._strategy()
        s.set_funding(series([0.0001] * 40 + [0.005], start=self.candles[0].ts))
        sig = s.generate(self.candles, None)
        self.assertIs(sig.action, Action.ENTER_SHORT)

    def test_negative_funding_produces_a_long(self):
        s = self._strategy()
        s.set_funding(series([0.0001] * 40 + [-0.005], start=self.candles[0].ts))
        sig = s.generate(self.candles, None)
        self.assertIs(sig.action, Action.ENTER_LONG)

    def test_normal_funding_holds(self):
        s = self._strategy()
        s.set_funding(series([0.0001] * 41, start=self.candles[0].ts))
        self.assertIs(s.generate(self.candles, None).action, Action.HOLD)

    def test_always_takes_the_side_that_receives_funding(self):
        """구조적 성질 — 이게 이 전략의 실제 우위원이다."""
        entries = []
        s = self._strategy()
        for i in range(s.warmup, len(self.candles), 3):
            sig = s.generate(self.candles[: i + 1], None)
            if sig.action is Action.HOLD:
                continue
            z = sig.meta["funding_z"]
            entries.append((sig.action, z))
        self.assertGreater(len(entries), 0)
        for action, z in entries:
            with self.subTest(z=z):
                if action is Action.ENTER_SHORT:
                    self.assertGreater(z, 0)    # 펀딩 높음 → 숏이 받는다
                else:
                    self.assertLess(z, 0)       # 펀딩 낮음 → 롱이 받는다

    def test_higher_threshold_means_fewer_entries(self):
        def count(z):
            s = self._strategy(entry_z=z)
            return sum(
                1 for i in range(s.warmup, len(self.candles), 3)
                if s.generate(self.candles[: i + 1], None).action is not Action.HOLD
            )
        self.assertGreater(count(1.5), count(3.0))

    def test_exit_threshold_must_be_below_entry(self):
        with self.assertRaises(ValueError):
            get_strategy("funding_bias", entry_z=2.0, exit_z=2.0)

    def test_invalid_entry_threshold(self):
        with self.assertRaises(ValueError):
            get_strategy("funding_bias", entry_z=0)

    def test_is_causal(self):
        """미래 캔들을 바꿔도 과거 판단이 그대로여야 한다."""
        s = self._strategy()
        cut = 2000
        tampered = self.candles[:cut] + [
            Candle(c.ts, c.open * 4, c.high * 4, c.low * 4, c.close * 4, c.volume)
            for c in self.candles[cut:]
        ]
        for i in (1800, 1900, cut - 1):
            with self.subTest(bar=i):
                self.assertIs(
                    s.generate(self.candles[: i + 1], None).action,
                    s.generate(tampered[: i + 1], None).action,
                )

    def test_backtest_runs_end_to_end(self):
        cfg = Config()
        cfg.mode = "backtest"
        cfg.initial_equity = 200.0
        cfg.exchange.timeframe = "1h"
        m = bt.run(self.candles, self._strategy(), cfg, funding_series=self.funding)
        self.assertGreater(m.trades, 0)


if __name__ == "__main__":
    unittest.main()
