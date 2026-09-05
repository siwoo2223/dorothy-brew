"""백테스트와 페이퍼(리플레이)가 같은 답을 내는지.

둘은 **다른 코드 경로**다. 백테스트는 backtest/engine.py의 단순 루프이고,
페이퍼는 실전과 똑같은 TradingEngine을 탄다. 그래서 조용히 갈라질 수 있고,
실제로 갈라져 있었다.

발견 당시 페이퍼가 백테스트보다 좋게 나왔다. 원인 둘:
  1. cmd_paper가 펀딩비를 안 넘겨서 비용이 덜 나갔다 (자본 4.8% 부풀림)
  2. 리플레이가 벽시계를 써서 8.6년이 하루로 취급됐다. 일일 손실 한도와
     연속 손실이 한 번 걸리면 안 풀려 체결이 142건 → 76건으로 줄었다

**실전 직전에 딱 속기 좋은 자리다.** 페이퍼가 좋게 나오면 그대로 실전에 간다.
"""

import tempfile
import unittest
from pathlib import Path

from dorothy.backtest import engine as bt
from dorothy.config import Config
from dorothy.data.loader import synthetic
from dorothy.engine import TradingEngine
from dorothy.exchange.paper import ReplayExchange
from dorothy.strategy.base import get_strategy


def make_config(db_path: str) -> Config:
    cfg = Config()
    cfg.initial_equity = 1000.0
    cfg.db_path = db_path
    cfg.poll_interval_sec = 0
    cfg.exchange.taker_fee = 0.0006
    cfg.exchange.slippage = 0.0005
    cfg.exchange.funding_rate = 0.0001
    cfg.exchange.funding_interval_hours = 8
    cfg.strategy.name = "donchian"
    cfg.strategy.params = {"channel": 20, "allow_short": False}
    return cfg


def run_both(candles, tweak=None):
    """같은 캔들로 두 경로를 돌리고 (백테스트 지표, 리플레이 거래소)를 준다."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_config(str(Path(tmp) / "a.db"))
        if tweak:
            tweak(cfg)
        cfg.mode = "backtest"
        metrics = bt.run(candles, get_strategy(cfg.strategy.name, **cfg.strategy.params), cfg)

        cfg2 = make_config(str(Path(tmp) / "b.db"))
        if tweak:
            tweak(cfg2)
        cfg2.mode = "paper"
        exchange = ReplayExchange(
            candles,
            equity=cfg2.initial_equity,
            taker_fee=cfg2.exchange.taker_fee,
            slippage=cfg2.exchange.slippage,
            funding_rate=cfg2.exchange.funding_rate,
            funding_interval_hours=cfg2.exchange.funding_interval_hours,
            min_size=cfg2.exchange.min_order_size,
            size_step=cfg2.exchange.size_step,
        )
        engine = TradingEngine(
            cfg2, exchange, get_strategy(cfg2.strategy.name, **cfg2.strategy.params)
        )
        engine.start_offline_replay()
        return metrics, exchange


class PathAgreementTests(unittest.TestCase):
    def setUp(self):
        self.candles = synthetic(1500, seed=5)

    def test_same_number_of_trades(self):
        """체결 수가 다르면 어느 한쪽이 신호를 놓치거나 지어내고 있다."""
        metrics, exchange = run_both(self.candles)
        self.assertGreater(metrics.trades, 5, "표본이 너무 적어 비교가 무의미합니다")
        self.assertEqual(metrics.trades, len(exchange.trades))

    def test_final_equity_agrees(self):
        """**소수점까지 같아야 한다.**

        예전엔 1% 여유를 두고 "미청산 처리 차이"로 설명해뒀는데, 실제로
        재보니 그게 아니었다. 리플레이는 tick()이 가져오는 창(limit)만 보고
        지표를 계산하는데 ATR은 Wilder 평활(재귀)이라 **과거를 얼마나 깊이
        봤는지에 따라 값이 달라진다.** 창이 얕아서 첫 체결부터 체결가가
        어긋났고 8.6년 누적 0.07%까지 벌어졌다.

        1%짜리 여유가 그걸 덮고 있었다. 창을 수렴할 만큼 넓혀 고쳤으므로
        이제 여유를 좁힌다 — 다시 벌어지면 지표 창 문제다.
        """
        metrics, exchange = run_both(self.candles)
        gap = abs(exchange.total_equity() - metrics.final_equity)
        self.assertLess(gap, max(metrics.final_equity * 1e-9, 1e-6),
                        f"백테스트 {metrics.final_equity:.6f} vs "
                        f"페이퍼 {exchange.total_equity():.6f}")

    def test_every_trade_agrees_not_just_the_total(self):
        """합계만 맞고 개별 체결이 어긋나면 상쇄된 것일 뿐이다.

        백테스트도 거래소를 남겨 돌려서 체결끼리 직접 맞춘다.
        """
        from dorothy.exchange.paper import PaperExchange
        from dorothy.execution.executor import Executor
        from dorothy.risk.manager import RiskManager

        _, replay = run_both(self.candles)

        cfg = make_config(":memory:")
        cfg.mode = "backtest"
        strategy = get_strategy(cfg.strategy.name, **cfg.strategy.params)
        ex = PaperExchange(
            equity=cfg.initial_equity, taker_fee=cfg.exchange.taker_fee,
            slippage=cfg.exchange.slippage, min_size=cfg.exchange.min_order_size,
            size_step=cfg.exchange.size_step, funding_rate=cfg.exchange.funding_rate,
            funding_interval_hours=cfg.exchange.funding_interval_hours,
        )
        holder = {"ts": self.candles[0].ts}
        risk = RiskManager(cfg.risk, kill_switch_file="/nonexistent-kill",
                           clock=lambda: holder["ts"])
        risk.state.day_start_equity = cfg.initial_equity
        executor = Executor(ex, risk, symbol=cfg.exchange.symbol,
                            leverage=cfg.exchange.leverage,
                            min_size=cfg.exchange.min_order_size,
                            size_step=cfg.exchange.size_step)
        settled = 0
        for i, candle in enumerate(self.candles):
            holder["ts"] = candle.ts
            ex.feed_candle(candle)
            while settled < len(ex.trades):
                risk.record_trade(ex.trades[settled]); settled += 1
            risk.roll_day(ex.total_equity())
            if i < strategy.warmup:
                continue
            pos = ex.fetch_position(cfg.exchange.symbol)
            executor.handle(strategy.generate(self.candles[: i + 1], pos), position=pos,
                            equity=ex.total_equity(), candle_ts=candle.ts)
            while settled < len(ex.trades):
                risk.record_trade(ex.trades[settled]); settled += 1

        # bt.run과 같이 마지막 미청산 포지션을 정리한다.
        # 빼먹으면 리플레이(force_close를 함)보다 체결이 하나 적어진다.
        if ex.fetch_position(cfg.exchange.symbol) is not None:
            ex.close_position(cfg.exchange.symbol, reason="백테스트 종료")

        self.assertGreater(len(replay.trades), 5)
        self.assertEqual(len(ex.trades), len(replay.trades))
        end_labels = {"백테스트 종료", "리플레이 종료"}
        for k, (b, p) in enumerate(zip(ex.trades, replay.trades)):
            # 마지막 강제 청산만 이름이 다르다(경로 이름이 들어감). 숫자는 같아야 한다.
            if not (b.reason in end_labels and p.reason in end_labels):
                self.assertEqual(b.reason, p.reason, f"#{k} 청산 사유")
            self.assertAlmostEqual(b.size, p.size, places=12, msg=f"#{k} 수량")
            self.assertAlmostEqual(b.entry_price, p.entry_price, places=8, msg=f"#{k} 진입가")
            self.assertAlmostEqual(b.exit_price, p.exit_price, places=8, msg=f"#{k} 청산가")
            self.assertAlmostEqual(b.net_pnl, p.net_pnl, places=9, msg=f"#{k} 손익")

    def test_the_fetch_window_is_deep_enough_for_wilder_smoothing(self):
        """엔진이 **실제로 요청하는** 창이 ATR을 수렴시킬 만큼 깊은가.

        상수를 테스트에 베껴 쓰면 엔진을 얕게 바꿔도 통과한다.
        그래서 fetch_candles를 가로채 엔진이 넘긴 limit을 직접 본다.
        """
        seen = []

        class Spy(ReplayExchange):
            def fetch_candles(self, symbol, timeframe, limit=200):
                seen.append(limit)
                return super().fetch_candles(symbol, timeframe, limit)

        cfg = make_config(":memory:")
        cfg.mode = "paper"
        strategy = get_strategy(cfg.strategy.name, **cfg.strategy.params)
        exchange = Spy(self.candles[:400], equity=cfg.initial_equity)
        TradingEngine(cfg, exchange, strategy).start_offline_replay()

        self.assertTrue(seen, "엔진이 캔들을 가져오지 않았습니다")
        past_seed = min(seen) - strategy.warmup
        self.assertLess(
            (13 / 14) ** past_seed, 1e-6,
            f"엔진이 요청한 창 {min(seen)}봉 → 시드 이후 {past_seed}봉으로는 "
            "ATR이 수렴하지 않아 실전이 백테스트와 갈라집니다",
        )


class FundingTests(unittest.TestCase):
    """펀딩비가 실제로 나가는지. 이걸 빠뜨려서 페이퍼가 4.8% 부풀었다."""

    def setUp(self):
        self.candles = synthetic(1500, seed=5)

    def test_funding_reduces_paper_equity(self):
        def free(cfg):
            cfg.exchange.funding_rate = 0.0

        _, paid = run_both(self.candles)
        _, unpaid = run_both(self.candles, tweak=free)
        self.assertLess(paid.total_equity(), unpaid.total_equity(),
                        "펀딩비가 자본에 반영되지 않았습니다")


class ReplayClockTests(unittest.TestCase):
    """리플레이는 캔들 시각을 '지금'으로 써야 한다.

    벽시계를 쓰면 긴 데이터를 몇 초에 재생하는 동안 전체가 하루가 되어,
    일일 손실 한도와 연속 손실이 한 번 걸리면 영영 안 풀린다.
    """

    def test_replay_clock_follows_candles(self):
        candles = synthetic(400, seed=9)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(str(Path(tmp) / "c.db"))
            cfg.mode = "paper"
            exchange = ReplayExchange(candles, equity=cfg.initial_equity)
            engine = TradingEngine(
                cfg, exchange, get_strategy(cfg.strategy.name, **cfg.strategy.params)
            )
            engine.start_offline_replay()
            self.assertTrue(engine._replay_clock)
            self.assertEqual(engine._now_ms(), engine._last_candle_ts)
            self.assertGreater(engine._last_candle_ts, 0)

    def test_live_clock_is_wall_clock(self):
        import time

        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(str(Path(tmp) / "d.db"))
            exchange = ReplayExchange(synthetic(100, seed=1), equity=cfg.initial_equity)
            engine = TradingEngine(
                cfg, exchange, get_strategy(cfg.strategy.name, **cfg.strategy.params)
            )
            self.assertFalse(engine._replay_clock)
            self.assertAlmostEqual(engine._now_ms() / 1000, time.time(), delta=5)

    def test_replay_does_not_inherit_losses_from_a_previous_run(self):
        """저장된 일지에서 연속 손실을 읽으면 이전 실행이 다음 실행을 오염시킨다."""
        candles = synthetic(600, seed=11)
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "shared.db")
            first = None
            for _ in range(2):
                cfg = make_config(db)
                cfg.mode = "paper"
                exchange = ReplayExchange(
                    candles, equity=cfg.initial_equity,
                    taker_fee=cfg.exchange.taker_fee, slippage=cfg.exchange.slippage,
                )
                engine = TradingEngine(
                    cfg, exchange,
                    get_strategy(cfg.strategy.name, **cfg.strategy.params),
                )
                engine.start_offline_replay()
                if first is None:
                    first = len(exchange.trades)
                else:
                    self.assertEqual(first, len(exchange.trades),
                                     "두 번째 실행이 첫 실행에 오염됐습니다")


if __name__ == "__main__":
    unittest.main()
