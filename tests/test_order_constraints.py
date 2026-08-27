"""거래소 주문 제약(최소 수량·수량 단위) 테스트.

**이 파일이 막는 사고:**
최소 주문 수량을 반영하지 않으면 백테스트가 실제로는 나가지도 못할 주문을
체결시킨다. 그러면 소액 계좌 결과가 통째로 거짓말이 되고,
"$10으로 얼마까지 되나"에 낙관적인 답을 주게 된다.
"""

import unittest

from dorothy.backtest import engine as bt
from dorothy.config import Config, RiskConfig
from dorothy.data.loader import synthetic
from dorothy.exchange.base import OrderError
from dorothy.exchange.paper import PaperExchange
from dorothy.execution.executor import Executor
from dorothy.execution.sizing import normalize, round_down_to_step
from dorothy.models import Action, Candle, Side, Signal
from dorothy.risk.manager import RiskManager
from dorothy.strategy.base import get_strategy

SYM = "BTC/USDT:USDT"


class TestRounding(unittest.TestCase):
    def test_rounds_down_never_up(self):
        """올림하면 감수하기로 한 리스크를 넘게 된다."""
        self.assertAlmostEqual(round_down_to_step(0.000462, 0.0001), 0.0004)
        self.assertAlmostEqual(round_down_to_step(0.00019, 0.0001), 0.0001)
        self.assertAlmostEqual(round_down_to_step(0.00099, 0.0001), 0.0009)

    def test_exact_multiples_are_preserved(self):
        """부동소수 오차로 한 단위 깎이면 안 된다."""
        for n in range(1, 30):
            with self.subTest(units=n):
                self.assertAlmostEqual(round_down_to_step(n * 0.0001, 0.0001), n * 0.0001)

    def test_zero_step_is_a_passthrough(self):
        self.assertAlmostEqual(round_down_to_step(0.12345, 0.0), 0.12345)

    def test_negative_and_zero_sizes(self):
        self.assertEqual(round_down_to_step(0.0, 0.0001), 0.0)
        self.assertEqual(round_down_to_step(-5.0, 0.0001), 0.0)

    def test_below_minimum_returns_zero(self):
        self.assertEqual(normalize(0.00009, min_size=0.0001, step=0.0001), 0.0)

    def test_rounding_below_minimum_returns_zero(self):
        """내림 결과가 최소에 못 미치면 주문 불가다. 최소까지 올리지 않는다."""
        self.assertEqual(normalize(0.00019, min_size=0.0002, step=0.0001), 0.0)

    def test_valid_size_survives(self):
        self.assertAlmostEqual(normalize(0.00046, min_size=0.0001, step=0.0001), 0.0004)


class TestAtrWindowBounding(unittest.TestCase):
    """ATR을 최근 구간으로 잘라 계산해도 값이 같아야 한다.

    전체 히스토리로 계산하면 매 봉마다 O(n)이라 백테스트가 O(n²)가 된다.
    실제 2년치 시간봉(17,521개)에서 100봉/초까지 떨어졌다.
    Wilder 평활이라 최근 구간만으로 수렴하므로 잘라도 값이 바뀌지 않는다 —
    그 사실을 여기서 고정한다. 깨지면 성능 최적화가 정확도를 해친 것이다.
    """

    def setUp(self):
        from dorothy.data.indicators import atr as atr_indicator
        from dorothy.strategy.common import atr_at

        self.atr_indicator = atr_indicator
        self.atr_at = atr_at
        self.candles = synthetic(6000, seed=5, timeframe="1h", start=65000.0)

    def test_bounded_atr_matches_full_history(self):
        for cut in (1000, 3000, 6000):
            window = self.candles[:cut]
            full = self.atr_indicator(
                [c.high for c in window], [c.low for c in window],
                [c.close for c in window], 14,
            )[-1]
            with self.subTest(bars=cut):
                self.assertAlmostEqual(self.atr_at(window, 14), full, places=6)

    def test_matches_for_a_long_period_too(self):
        window = self.candles[:4000]
        full = self.atr_indicator(
            [c.high for c in window], [c.low for c in window],
            [c.close for c in window], 50,
        )[-1]
        self.assertAlmostEqual(self.atr_at(window, 50), full, places=6)

    def test_short_history_still_works(self):
        self.assertIsNotNone(self.atr_at(self.candles[:60], 14))


class TestPaperExchangeEnforcement(unittest.TestCase):
    def _exchange(self, equity=10.0):
        px = PaperExchange(equity=equity, taker_fee=0.0, slippage=0.0,
                           min_size=0.0001, size_step=0.0001)
        px.feed_candle(Candle(0, 65000, 65000, 65000, 65000, 1.0))
        return px

    def test_order_below_minimum_is_rejected(self):
        with self.assertRaises(OrderError) as ctx:
            self._exchange().open_position(SYM, Side.LONG, 0.00005, stop_loss=64000)
        self.assertIn("최소 주문 수량", str(ctx.exception))

    def test_order_is_rounded_down_to_the_step(self):
        pos = self._exchange().open_position(SYM, Side.LONG, 0.00046, stop_loss=64000)
        self.assertAlmostEqual(pos.size, 0.0004)

    def test_unconstrained_exchange_still_works(self):
        """제약을 0으로 두면 예전처럼 동작해야 한다 (기존 테스트 보호)."""
        px = PaperExchange(equity=1000, taker_fee=0.0, slippage=0.0)
        px.feed_candle(Candle(0, 100, 100, 100, 100, 1.0))
        pos = px.open_position(SYM, Side.LONG, 0.123456, stop_loss=95)
        self.assertAlmostEqual(pos.size, 0.123456)


class TestExecutorEnforcement(unittest.TestCase):
    def _executor(self, equity):
        px = PaperExchange(equity=equity, taker_fee=0.0006, slippage=0.0,
                           min_size=0.0001, size_step=0.0001)
        px.feed_candle(Candle(0, 65000, 65000, 65000, 65000, 1.0))
        risk = RiskManager(RiskConfig(), kill_switch_file="/nonexistent")
        risk.roll_day(equity)
        ex = Executor(px, risk, symbol=SYM, leverage=2,
                      min_size=0.0001, size_step=0.0001)
        return px, risk, ex

    def test_tiny_account_cannot_enter(self):
        """$10에 1% 리스크·1% 손절폭이면 수량이 최소에 못 미친다."""
        _, _, ex = self._executor(10.0)
        sig = Signal(Action.ENTER_LONG, "test", stop_loss=65000 * 0.99)
        self.assertIsNone(ex.handle(sig, position=None, equity=10.0, candle_ts=1))

    def test_rejected_entry_releases_the_risk_slot(self):
        """수량 미달로 거부됐는데 슬롯이 물려 있으면 봇이 영영 멈춘다."""
        _, risk, ex = self._executor(10.0)
        before = risk.state.open_positions
        sig = Signal(Action.ENTER_LONG, "test", stop_loss=65000 * 0.99)
        ex.handle(sig, position=None, equity=10.0, candle_ts=1)
        self.assertEqual(risk.state.open_positions, before)

    def test_larger_account_can_enter(self):
        px, _, ex = self._executor(500.0)
        sig = Signal(Action.ENTER_LONG, "test", stop_loss=65000 * 0.99)
        pos = ex.handle(sig, position=None, equity=500.0, candle_ts=1)
        self.assertIsNotNone(pos)
        self.assertGreaterEqual(pos.size, 0.0001)

    def test_ordered_size_is_a_multiple_of_the_step(self):
        px, _, ex = self._executor(500.0)
        sig = Signal(Action.ENTER_LONG, "test", stop_loss=65000 * 0.99)
        pos = ex.handle(sig, position=None, equity=500.0, candle_ts=1)
        units = pos.size / 0.0001
        self.assertAlmostEqual(units, round(units), places=6)

    def test_ordered_size_never_exceeds_the_risk_size(self):
        """내림이므로 리스크 계산 수량보다 커질 수 없다."""
        px, risk, ex = self._executor(500.0)
        ideal = risk.evaluate_entry(
            equity=500.0, price=65000, side=Side.LONG,
            stop_loss=65000 * 0.99, leverage=2,
        )
        risk.release()
        sig = Signal(Action.ENTER_LONG, "test", stop_loss=65000 * 0.99)
        pos = ex.handle(sig, position=None, equity=500.0, candle_ts=1)
        self.assertLessEqual(pos.size, ideal.size + 1e-12)


class TestBacktestRealism(unittest.TestCase):
    """제약 반영 전에는 소액 백테스트가 대액과 똑같이 나왔다."""

    @classmethod
    def setUpClass(cls):
        cls.candles = synthetic(4000, seed=5, timeframe="15m", start=65000.0)

    def _run(self, equity):
        cfg = Config()
        cfg.mode = "backtest"
        cfg.initial_equity = equity
        return bt.run(self.candles, get_strategy("donchian"), cfg)

    def test_small_account_makes_far_fewer_trades(self):
        small = self._run(10.0)
        large = self._run(1000.0)
        self.assertLess(small.trades, large.trades / 5,
                        "소액 계좌가 대액과 비슷하게 매매하고 있습니다 — 제약이 안 걸렸습니다")

    def test_returns_no_longer_scale_linearly(self):
        """예전 버그: $10과 $1,000의 수익률이 소수점까지 동일했다."""
        small = self._run(10.0)
        large = self._run(1000.0)
        self.assertGreater(abs(small.return_pct - large.return_pct), 1.0)

    def test_large_account_is_unaffected_by_the_constraint(self):
        """대액 계좌에서는 최소 수량이 사실상 무의미해야 한다."""
        self.assertGreater(self._run(1000.0).trades, 100)


if __name__ == "__main__":
    unittest.main()
