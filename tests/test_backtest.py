import unittest

from dorothy.backtest import engine as bt
from dorothy.config import Config
from dorothy.data.loader import synthetic
from dorothy.models import Trade, Side
from dorothy.backtest.metrics import compute
from dorothy.strategy.base import get_strategy


def _config() -> Config:
    cfg = Config()
    cfg.mode = "backtest"
    cfg.initial_equity = 1000.0
    cfg.strategy.name = "ema_cross"
    cfg.strategy.params = {"fast": 5, "slow": 15, "atr_period": 10}
    return cfg


class TestBacktestEngine(unittest.TestCase):
    def test_runs_end_to_end(self):
        cfg = _config()
        candles = synthetic(n=1500, seed=7)
        result = bt.run(candles, get_strategy(cfg.strategy.name, **cfg.strategy.params), cfg)
        self.assertEqual(result.initial_equity, 1000.0)
        self.assertGreaterEqual(result.trades, 0)
        self.assertGreaterEqual(result.max_drawdown_pct, 0.0)
        self.assertIn("백테스트 결과", result.report())

    def test_rejects_data_shorter_than_warmup(self):
        cfg = _config()
        strat = get_strategy(cfg.strategy.name, **cfg.strategy.params)
        with self.assertRaises(ValueError):
            bt.run(synthetic(n=5), strat, cfg)

    def test_is_deterministic(self):
        cfg = _config()
        candles = synthetic(n=1200, seed=3)
        a = bt.run(candles, get_strategy(cfg.strategy.name, **cfg.strategy.params), cfg)
        b = bt.run(candles, get_strategy(cfg.strategy.name, **cfg.strategy.params), cfg)
        self.assertEqual(a.trades, b.trades)
        self.assertAlmostEqual(a.final_equity, b.final_equity)

    def test_no_look_ahead(self):
        """미래 캔들을 바꿔도 과거 판단은 그대로여야 한다.

        이 테스트가 깨지면 백테스트 결과 전체가 신기루다.
        """
        cfg = _config()
        base = synthetic(n=1200, seed=11)
        cut = 900

        # 뒤쪽 300개를 전혀 다른 흐름으로 갈아끼운다
        tampered = base[:cut] + [
            type(c)(c.ts, c.open * 3, c.high * 3, c.low * 3, c.close * 3, c.volume)
            for c in base[cut:]
        ]
        # 전체 실행의 equity curve 중 cut 이전 구간이 prefix 실행과 일치해야 한다
        cutoff_ts = base[cut - 1].ts
        full_prefix = [(t, e) for t, e in _rerun_curve(tampered, cfg) if t < cutoff_ts]
        prefix_curve = [(t, e) for t, e in _rerun_curve(base[:cut], cfg) if t < cutoff_ts]
        self.assertEqual(len(full_prefix), len(prefix_curve))
        for (_, a), (_, b) in zip(full_prefix, prefix_curve):
            self.assertAlmostEqual(a, b, places=6)


def _rerun_curve(candles, cfg):
    """지표 객체에는 자본 곡선이 없으므로 같은 조건으로 재실행해 곡선을 얻는다."""
    from dorothy.execution.executor import Executor
    from dorothy.exchange.paper import PaperExchange
    from dorothy.risk.manager import RiskManager
    from dorothy.strategy.base import get_strategy as gs

    strategy = gs(cfg.strategy.name, **cfg.strategy.params)
    ex = PaperExchange(
        equity=cfg.initial_equity,
        taker_fee=cfg.exchange.taker_fee,
        slippage=cfg.exchange.slippage,
    )
    holder = {"ts": candles[0].ts}
    risk = RiskManager(cfg.risk, kill_switch_file="/nonexistent", clock=lambda: holder["ts"])
    risk.state.day_start_equity = cfg.initial_equity
    ex_ = Executor(ex, risk, symbol=cfg.exchange.symbol, leverage=cfg.exchange.leverage)
    settled = 0
    for i, candle in enumerate(candles):
        holder["ts"] = candle.ts
        ex.feed_candle(candle)
        while settled < len(ex.trades):
            risk.record_trade(ex.trades[settled]); settled += 1
        risk.roll_day(ex.total_equity())
        if i < strategy.warmup:
            continue
        pos = ex.fetch_position(cfg.exchange.symbol)
        sig = strategy.generate(candles[: i + 1], pos)
        ex_.handle(sig, position=pos, equity=ex.total_equity(), candle_ts=candle.ts)
        while settled < len(ex.trades):
            risk.record_trade(ex.trades[settled]); settled += 1
    return ex.equity_curve


class TestMetrics(unittest.TestCase):
    def test_profit_factor_and_win_rate(self):
        trades = [
            Trade("B", Side.LONG, 1, 100, 110, 0, 1),    # +10
            Trade("B", Side.LONG, 1, 100, 95, 0, 2),     # -5
            Trade("B", Side.LONG, 1, 100, 105, 0, 3),    # +5
        ]
        m = compute(trades, [(0, 1000), (3, 1010)], 1000)
        self.assertEqual(m.trades, 3)
        self.assertAlmostEqual(m.win_rate, 200 / 3, places=4)
        self.assertAlmostEqual(m.profit_factor, 15 / 5)

    def test_max_drawdown(self):
        curve = [(0, 1000), (1, 1200), (2, 900), (3, 1100)]
        m = compute([], curve, 1000)
        self.assertAlmostEqual(m.max_drawdown_pct, 25.0)   # 1200 → 900

    def test_empty_backtest_warns(self):
        m = compute([], [(0, 1000)], 1000)
        self.assertIn("거래가 한 건도 없습니다", m.report())


if __name__ == "__main__":
    unittest.main()
