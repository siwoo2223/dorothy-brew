"""멀티 타임프레임 · 몬테카를로 테스트."""

import unittest

from dorothy.backtest import montecarlo as mc
from dorothy.data.loader import synthetic
from dorothy.data.resample import infer_interval, resample, timeframe_ms
from dorothy.models import Action, Candle, Position, Side, Trade
from dorothy.strategy.base import get_strategy

HOUR = 3_600_000


def bar(ts, o, h, l, c, v=1.0):
    return Candle(ts, o, h, l, c, v)


def aligned(n, start_price=100.0):
    """4시간 경계(ts=0)에 정렬된 1시간봉. 버킷 경계 동작을 정확히 검증하기 위함."""
    out = []
    for i in range(n):
        base = start_price + i
        out.append(bar(i * HOUR, base, base + 2, base - 2, base + 1, float(i + 1)))
    return out


class TestResample(unittest.TestCase):
    def setUp(self):
        self.candles = synthetic(200, seed=5, timeframe="1h", start=65000.0)

    def test_infers_the_source_interval(self):
        self.assertEqual(infer_interval(self.candles), HOUR)

    def test_ohlc_is_aggregated_correctly(self):
        candles = aligned(8)
        higher = resample(candles, timeframe_ms("4h"))
        first_four = candles[:4]
        self.assertAlmostEqual(higher[0].open, first_four[0].open)
        self.assertAlmostEqual(higher[0].close, first_four[-1].close)
        self.assertAlmostEqual(higher[0].high, max(c.high for c in first_four))
        self.assertAlmostEqual(higher[0].low, min(c.low for c in first_four))

    def test_volume_is_summed(self):
        candles = aligned(8)
        higher = resample(candles, timeframe_ms("4h"))
        self.assertAlmostEqual(higher[0].volume, sum(c.volume for c in candles[:4]))

    def test_incomplete_bar_is_dropped(self):
        """진행 중인 상위 봉의 종가는 실시간에 알 수 없다. 쓰면 미래참조다."""
        partial = aligned(6)              # 4시간봉 1개 + 2시간치
        self.assertEqual(len(resample(partial, timeframe_ms("4h"))), 1)

    def test_incomplete_bar_can_be_kept_explicitly(self):
        self.assertEqual(
            len(resample(aligned(6), timeframe_ms("4h"), drop_incomplete=False)), 2
        )

    def test_partial_leading_bucket_is_dropped(self):
        """첫 캔들이 버킷 중간에서 시작하면 그 버킷의 시가는 실제와 다르다."""
        candles = aligned(12)[2:]         # 4시간 버킷의 3번째 봉부터 시작
        higher = resample(candles, timeframe_ms("4h"))
        self.assertTrue(higher)
        for h in higher:
            with self.subTest(ts=h.ts):
                self.assertEqual(h.ts % timeframe_ms("4h"), 0)
        # 잘린 첫 버킷(ts=0)은 빠져야 한다
        self.assertNotIn(0, [h.ts for h in higher])

    def test_growing_history_never_rewrites_past_bars(self):
        """캔들이 늘어나도 이미 만들어진 상위 봉은 그대로여야 한다."""
        a = resample(self.candles[:100], timeframe_ms("4h"))
        b = resample(self.candles[:160], timeframe_ms("4h"))
        for i, candle in enumerate(a):
            with self.subTest(bar=i):
                self.assertEqual(candle, b[i])

    def test_rejects_smaller_target_timeframe(self):
        with self.assertRaises(ValueError):
            resample(self.candles, timeframe_ms("15m"))

    def test_unknown_timeframe_is_rejected(self):
        with self.assertRaises(ValueError):
            timeframe_ms("7h")

    def test_empty_input(self):
        self.assertEqual(resample([], timeframe_ms("4h")), [])


class TestMtfFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candles = synthetic(3000, seed=5, timeframe="1h", start=65000.0)
        cls.base = {"channel": 40, "exit_channel": 20}

    def _strategy(self, **kw):
        return get_strategy(
            "mtf_filter", base="donchian", base_params=self.base, **kw
        )

    def test_filter_reduces_entry_count(self):
        plain = get_strategy("donchian", **self.base)
        filtered = self._strategy(higher_timeframe="4h")
        def count(s):
            return sum(
                1 for i in range(s.warmup, len(self.candles), 3)
                if s.generate(self.candles[: i + 1], None).action is not Action.HOLD
            )
        self.assertLess(count(filtered), count(plain))

    def test_entries_agree_with_the_higher_timeframe(self):
        strategy = self._strategy(higher_timeframe="4h")
        for i in range(strategy.warmup, len(self.candles), 5):
            sig = strategy.generate(self.candles[: i + 1], None)
            if sig.action is Action.HOLD:
                continue
            with self.subTest(bar=i):
                wanted = 1 if sig.action is Action.ENTER_LONG else -1
                self.assertEqual(sig.meta.get("htf_bias"), wanted)

    def test_exit_signals_pass_through_unfiltered(self):
        """필터는 들어가는 문만 좁힌다. 나가는 문을 막으면 손실이 방치된다."""
        strategy = self._strategy(higher_timeframe="4h")
        position = Position("BTC/USDT:USDT", Side.LONG, 1.0, 65000.0)
        actions = {
            strategy.generate(self.candles[: i + 1], position).action
            for i in range(strategy.warmup, 2000, 7)
        }
        self.assertIn(Action.EXIT, actions)

    def test_is_causal(self):
        strategy = self._strategy(higher_timeframe="4h")
        cut = 2000
        tampered = self.candles[:cut] + [
            bar(c.ts, c.open * 4, c.high * 4, c.low * 4, c.close * 4)
            for c in self.candles[cut:]
        ]
        for i in (1800, 1900, cut - 1):
            with self.subTest(bar=i):
                self.assertIs(
                    strategy.generate(self.candles[: i + 1], None).action,
                    strategy.generate(tampered[: i + 1], None).action,
                )

    def test_cannot_wrap_itself(self):
        with self.assertRaises(ValueError):
            get_strategy("mtf_filter", base="mtf_filter")

    def test_works_with_any_base_strategy(self):
        for base in ("donchian", "supertrend", "ema_cross"):
            with self.subTest(base=base):
                s = get_strategy("mtf_filter", base=base)
                self.assertIsNotNone(s.generate(self.candles[:1500], None))


class TestMonteCarlo(unittest.TestCase):
    def _trades(self, pattern, equity=100.0):
        trades, eq = [], equity
        for r in pattern:
            pnl = eq * r
            trades.append(Trade("B", Side.LONG, 1, 100, 101, 0, 1, realized_pnl=pnl))
            eq += pnl
        return trades

    def test_break_even_sequence_stays_near_start(self):
        trades = self._trades([0.01, -0.01] * 25)
        result = mc.run(trades, 100.0, runs=2000)
        self.assertLess(abs(result.median_final - 100.0), 5.0)

    def test_winning_sequence_has_low_loss_probability(self):
        trades = self._trades([0.02, 0.02, -0.01] * 20)
        result = mc.run(trades, 100.0, runs=2000)
        self.assertLess(result.loss_probability, 20.0)

    def test_losing_sequence_has_high_loss_probability(self):
        trades = self._trades([-0.02, 0.01] * 25)
        result = mc.run(trades, 100.0, runs=2000)
        self.assertGreater(result.loss_probability, 80.0)

    def test_percentiles_are_ordered(self):
        trades = self._trades([0.03, -0.02] * 30)
        r = mc.run(trades, 100.0, runs=2000)
        self.assertLessEqual(r.percentile(r.finals, 5), r.percentile(r.finals, 50))
        self.assertLessEqual(r.percentile(r.finals, 50), r.percentile(r.finals, 95))

    def test_ruin_is_detected_for_reckless_sizing(self):
        """한 번에 자본의 절반을 거는 매매는 파산 경로가 생긴다."""
        trades = self._trades([-0.5, 0.6] * 15)
        result = mc.run(trades, 100.0, runs=2000)
        self.assertGreater(result.ruin_probability, 0.0)

    def test_is_deterministic_for_a_seed(self):
        trades = self._trades([0.01, -0.01] * 25)
        a = mc.run(trades, 100.0, runs=1000, seed=7)
        b = mc.run(trades, 100.0, runs=1000, seed=7)
        self.assertEqual(a.finals, b.finals)

    def test_too_few_trades_is_rejected(self):
        with self.assertRaises(ValueError):
            mc.run(self._trades([0.01, -0.01]), 100.0)

    def test_report_warns_about_in_sample_optimism(self):
        trades = self._trades([0.01, -0.01] * 25)
        text = mc.run(trades, 100.0, runs=500).report()
        self.assertIn("워크포워드", text)
        self.assertIn("예측이 아닙니다", text)

    def test_returns_are_relative_to_equity_at_the_time(self):
        """복리 계좌에서 금액이 아니라 비율이 매매의 성질이다."""
        trades = self._trades([0.1] * 5, equity=100.0)
        returns = mc.trade_returns(trades, 100.0)
        for r in returns:
            self.assertAlmostEqual(r, 0.1, places=6)


if __name__ == "__main__":
    unittest.main()
