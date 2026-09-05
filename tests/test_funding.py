"""펀딩비 테스트.

무기한 선물은 8시간마다 펀딩비가 오간다. 포지션을 하루 이상 들고 가는
전략에서는 왕복 수수료에 맞먹는 비용이 되는데, 반영하지 않으면 백테스트가
낙관적으로 나온다. Trade.funding 필드는 처음부터 있었지만 아무도 채우지
않고 있었다 — 있는 줄 알고 지나치기 쉬운 종류의 구멍이다.
"""

import unittest

from dorothy.backtest import engine as bt
from dorothy.config import Config
from dorothy.data.loader import synthetic
from dorothy.exchange.paper import PaperExchange
from dorothy.models import Candle, Side
from dorothy.strategy.base import get_strategy

HOUR = 3_600_000
SYM = "BTC/USDT:USDT"


def exchange(rate=0.0001, equity=1000.0):
    px = PaperExchange(equity=equity, taker_fee=0.0, slippage=0.0, funding_rate=rate)
    px.feed_candle(Candle(0, 100, 100, 100, 100, 1.0))
    return px


class TestFundingTiming(unittest.TestCase):
    def test_no_charge_before_the_first_funding_time(self):
        px = exchange()
        px.open_position(SYM, Side.LONG, 10.0, stop_loss=50)
        px.feed_candle(Candle(4 * HOUR, 100, 100, 100, 100, 1.0))
        self.assertAlmostEqual(px._funding_accrued, 0.0)

    def test_charged_once_at_the_eight_hour_mark(self):
        px = exchange()
        px.open_position(SYM, Side.LONG, 10.0, stop_loss=50)
        px.feed_candle(Candle(8 * HOUR, 100, 100, 100, 100, 1.0))
        self.assertAlmostEqual(px._funding_accrued, 0.1)   # 명목가 1000 × 0.0001

    def test_charged_three_times_over_a_day(self):
        px = exchange()
        px.open_position(SYM, Side.LONG, 10.0, stop_loss=50)
        px.feed_candle(Candle(24 * HOUR, 100, 100, 100, 100, 1.0))
        self.assertAlmostEqual(px._funding_accrued, 0.3)

    def test_a_single_candle_can_span_several_funding_times(self):
        """일봉 등 긴 캔들은 여러 펀딩 시각을 한 번에 건너뛴다."""
        px = exchange()
        px.open_position(SYM, Side.LONG, 10.0, stop_loss=50)
        px.feed_candle(Candle(48 * HOUR, 100, 100, 100, 100, 1.0))
        self.assertAlmostEqual(px._funding_accrued, 0.6)   # 6회

    def test_no_position_means_no_funding(self):
        px = exchange()
        px.feed_candle(Candle(24 * HOUR, 100, 100, 100, 100, 1.0))
        self.assertAlmostEqual(px._funding_accrued, 0.0)
        self.assertAlmostEqual(px.equity, 1000.0)

    def test_zero_rate_charges_nothing(self):
        px = exchange(rate=0.0)
        px.open_position(SYM, Side.LONG, 10.0, stop_loss=50)
        px.feed_candle(Candle(24 * HOUR, 100, 100, 100, 100, 1.0))
        self.assertAlmostEqual(px._funding_accrued, 0.0)


class TestFundingDirection(unittest.TestCase):
    def test_long_pays_when_the_rate_is_positive(self):
        px = exchange()
        px.open_position(SYM, Side.LONG, 10.0, stop_loss=50)
        px.feed_candle(Candle(24 * HOUR, 100, 100, 100, 100, 1.0))
        self.assertGreater(px._funding_accrued, 0)
        self.assertLess(px.equity, 1000.0)

    def test_short_receives_when_the_rate_is_positive(self):
        px = exchange()
        px.open_position(SYM, Side.SHORT, 10.0, stop_loss=150)
        px.feed_candle(Candle(24 * HOUR, 100, 100, 100, 100, 1.0))
        self.assertLess(px._funding_accrued, 0)
        self.assertGreater(px.equity, 1000.0)

    def test_negative_rate_reverses_who_pays(self):
        px = exchange(rate=-0.0001)
        px.open_position(SYM, Side.LONG, 10.0, stop_loss=50)
        px.feed_candle(Candle(24 * HOUR, 100, 100, 100, 100, 1.0))
        self.assertLess(px._funding_accrued, 0)


class TestFundingInTrades(unittest.TestCase):
    def test_funding_is_recorded_on_the_trade(self):
        px = exchange()
        px.open_position(SYM, Side.LONG, 10.0, stop_loss=50)
        px.feed_candle(Candle(24 * HOUR, 100, 100, 100, 100, 1.0))
        px.close_position(SYM, reason="test")
        self.assertAlmostEqual(px.trades[0].funding, 0.3)

    def test_funding_reduces_net_pnl(self):
        px = exchange()
        px.open_position(SYM, Side.LONG, 10.0, stop_loss=50)
        px.feed_candle(Candle(24 * HOUR, 100, 100, 100, 100, 1.0))
        px.close_position(SYM, reason="test")
        trade = px.trades[0]
        self.assertAlmostEqual(trade.gross_pnl, 0.0)     # 가격 그대로
        self.assertAlmostEqual(trade.net_pnl, -0.3)      # 펀딩비만큼 손실

    def test_accrual_resets_between_trades(self):
        """앞 매매의 펀딩비가 다음 매매에 딸려오면 안 된다."""
        px = exchange()
        px.open_position(SYM, Side.LONG, 10.0, stop_loss=50)
        px.feed_candle(Candle(24 * HOUR, 100, 100, 100, 100, 1.0))
        px.close_position(SYM, reason="first")
        px.open_position(SYM, Side.LONG, 10.0, stop_loss=50)
        px.feed_candle(Candle(32 * HOUR, 100, 100, 100, 100, 1.0))
        px.close_position(SYM, reason="second")
        self.assertAlmostEqual(px.trades[1].funding, 0.1)   # 1회분만


class TestFundingImpactOnStrategies(unittest.TestCase):
    """방향 편향이 있는 전략일수록 펀딩비에 노출된다."""

    @classmethod
    def setUpClass(cls):
        cls.candles = synthetic(4000, seed=5, timeframe="1h", start=65000.0)

    def _run(self, rate, allow_short):
        cfg = Config()
        cfg.mode = "backtest"
        cfg.initial_equity = 100.0
        cfg.exchange.timeframe = "1h"
        cfg.exchange.funding_rate = rate
        strategy = get_strategy("donchian", channel=40, exit_channel=20, allow_short=allow_short)
        return bt.run(self.candles, strategy, cfg)

    def test_long_only_is_hurt_by_high_funding(self):
        cheap = self._run(0.0, allow_short=False)
        pricey = self._run(0.001, allow_short=False)
        self.assertLess(pricey.return_pct, cheap.return_pct - 1.0)

    def test_two_sided_strategy_largely_cancels_funding(self):
        """롱이 내고 숏이 받으므로 방향이 반반이면 상쇄된다."""
        cheap = self._run(0.0, allow_short=True)
        pricey = self._run(0.001, allow_short=True)
        self.assertLess(abs(pricey.return_pct - cheap.return_pct), 2.0)

    def test_long_only_is_more_exposed_than_two_sided(self):
        long_gap = abs(
            self._run(0.001, allow_short=False).return_pct
            - self._run(0.0, allow_short=False).return_pct
        )
        both_gap = abs(
            self._run(0.001, allow_short=True).return_pct
            - self._run(0.0, allow_short=True).return_pct
        )
        self.assertGreater(long_gap, both_gap)


if __name__ == "__main__":
    unittest.main()
