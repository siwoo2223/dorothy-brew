"""실전 모드 안전장치 회귀 테스트.

**이 파일이 막는 사고:**
거래소 스탑으로 청산되면 봇은 주문을 낸 적이 없어 그 사실을 모른다.
그러면 일일 손실 한도와 연속 손실 차단이 영원히 0을 보고 작동하지 않고,
포지션 카운터가 반납되지 않아 봇이 첫 매매 후 조용히 멈춘다.
실제로 그 상태였고, 페이퍼 모드에서만 멀쩡해서 테스트가 전부 통과했다.

그래서 여기서는 **페이퍼 거래소를 쓰지 않는다.**
실거래소처럼 '봇이 모르는 사이에 포지션이 사라지는' 가짜 거래소로 검증한다.
"""

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from dorothy.config import Config, RiskConfig
from dorothy.engine import TradingEngine
from dorothy.exchange.base import Exchange
from dorothy.journal.store import Journal
from dorothy.models import Account, Candle, Position, Side, Trade
from dorothy.notify.telegram import Notifier
from dorothy.risk.manager import RiskManager
from dorothy.strategy.base import get_strategy

SYM = "BTC/USDT:USDT"


class BrokerLikeExchange(Exchange):
    """실거래소 흉내. 포지션이 봇 모르게 사라진다.

    PaperExchange와 달리 `trades` 리스트를 들고 있지 않다 —
    실전 버그가 정확히 그 차이에서 나왔기 때문이다.
    """

    def __init__(self, candles, equity=1000.0):
        self.candles = candles
        self.cursor = 60
        self.equity = equity
        self.position: Position | None = None
        self._watched: Position | None = None
        self._equity_before = 0.0
        self.orders: list[tuple] = []

    @property
    def name(self):
        return "broker-like"

    def fetch_candles(self, symbol, timeframe, limit=200):
        self.cursor = min(self.cursor + 1, len(self.candles))
        return self.candles[max(0, self.cursor - limit) : self.cursor]

    def fetch_price(self, symbol):
        return self.candles[min(self.cursor, len(self.candles)) - 1].close

    def fetch_account(self):
        return Account(equity=self.equity, available=self.equity,
                       positions=[self.position] if self.position else [])

    def fetch_position(self, symbol):
        return self.position

    def set_leverage(self, symbol, leverage, margin_mode):
        pass

    def open_position(self, symbol, side, size, *, stop_loss=None,
                      take_profit=None, client_id=""):
        price = self.fetch_price(symbol)
        self.position = Position(symbol, side, size, price, stop_loss=stop_loss,
                                 take_profit=take_profit, opened_at=1, client_id=client_id)
        self.orders.append(("open", side.value, size))
        return self.position

    def close_position(self, symbol, *, reason=""):
        self.position = None
        self.orders.append(("close", reason))
        return self.fetch_price(symbol)

    def cancel_all(self, symbol):
        pass

    # --- 실거래소와 같은 방식으로 청산을 '감지'한다 ---
    def poll_closed_trades(self, symbol):
        if self.position is not None and self._watched is None:
            self._equity_before = self.equity
            self._watched = self.position
            return []
        if self.position is None and self._watched is not None:
            pos = self._watched
            self._watched = None
            # 실제 BitgetExchange와 같은 방식: 손익을 자본 변화에서 뽑는다
            return [Trade(symbol, pos.side, pos.size, pos.entry_price,
                          self.fetch_price(symbol), 1, 2,
                          realized_pnl=self.equity - self._equity_before,
                          reason="거래소 청산")]
        return []

    # --- 테스트가 쓰는 조작 ---
    def stop_out(self, loss: float):
        """거래소 스탑이 발동한 상황. 봇은 주문을 낸 적이 없다."""
        self.equity -= loss
        self.position = None


def make_engine(exchange, tmpdir, **risk_overrides):
    cfg = Config()
    cfg.mode = "paper"
    cfg.exchange.symbol = SYM
    cfg.poll_interval_sec = 0
    cfg.db_path = str(Path(tmpdir) / "t.db")
    cfg.kill_switch_file = str(Path(tmpdir) / "NOKILL")
    cfg.risk = RiskConfig(**risk_overrides) if risk_overrides else cfg.risk
    engine = TradingEngine(
        cfg, exchange, get_strategy("donchian", channel=10),
        journal=Journal(cfg.db_path), notifier=Notifier(enabled=False),
    )
    engine.risk.roll_day(exchange.equity)
    return engine


def flat_candles(n=200, price=100.0):
    return [Candle(i * 1000, price, price, price, price, 1.0) for i in range(n)]


class TestClosedTradeDetection(unittest.TestCase):
    def test_exchange_stop_out_is_recorded(self):
        """거래소 스탑 청산이 저널과 리스크에 반영되어야 한다."""
        with TemporaryDirectory() as tmp:
            ex = BrokerLikeExchange(flat_candles())
            engine = make_engine(ex, tmp)

            ex.open_position(SYM, Side.LONG, 1.0, stop_loss=95.0)
            engine._sync_closed_trades()          # 관측 시작
            self.assertEqual(engine._reported_trades, 0)

            ex.stop_out(loss=30.0)                # 봇 모르게 청산
            engine._sync_closed_trades()

            self.assertEqual(engine._reported_trades, 1)
            self.assertEqual(len(engine.journal.recent_trades()), 1)

    def test_daily_loss_limit_triggers_from_exchange_stops(self):
        """봇이 청산 주문을 낸 적 없어도 일일 한도가 작동해야 한다."""
        with TemporaryDirectory() as tmp:
            ex = BrokerLikeExchange(flat_candles(), equity=1000.0)
            engine = make_engine(ex, tmp, max_daily_loss_pct=0.03, max_consecutive_losses=99)

            ex.open_position(SYM, Side.LONG, 1.0, stop_loss=95.0)
            engine._sync_closed_trades()
            ex.stop_out(loss=40.0)                # -4%
            engine._sync_closed_trades()

            self.assertIn("일일 손실 한도", engine.risk.halt_reason(ex.equity))

    def test_consecutive_losses_count_up_in_live_mode(self):
        with TemporaryDirectory() as tmp:
            ex = BrokerLikeExchange(flat_candles(), equity=10_000.0)
            engine = make_engine(ex, tmp, max_consecutive_losses=3, max_daily_loss_pct=0.99)

            for _ in range(3):
                ex.open_position(SYM, Side.LONG, 1.0, stop_loss=95.0)
                engine._sync_closed_trades()
                ex.stop_out(loss=10.0)
                engine._sync_closed_trades()

            self.assertEqual(engine.risk.state.consecutive_losses, 3)
            self.assertIn("연속", engine.risk.halt_reason(ex.equity))

    def test_a_win_resets_the_streak(self):
        with TemporaryDirectory() as tmp:
            ex = BrokerLikeExchange(flat_candles(), equity=10_000.0)
            engine = make_engine(ex, tmp)

            ex.open_position(SYM, Side.LONG, 1.0, stop_loss=95.0)
            engine._sync_closed_trades()
            ex.stop_out(loss=10.0)
            engine._sync_closed_trades()
            self.assertEqual(engine.risk.state.consecutive_losses, 1)

            ex.open_position(SYM, Side.LONG, 1.0, stop_loss=95.0)
            engine._sync_closed_trades()
            ex.stop_out(loss=-20.0)               # 이익
            engine._sync_closed_trades()
            self.assertEqual(engine.risk.state.consecutive_losses, 0)


class TestPositionCounterNeverSticks(unittest.TestCase):
    """봇이 첫 매매 후 조용히 멈추던 버그."""

    def test_counter_follows_reality(self):
        with TemporaryDirectory() as tmp:
            ex = BrokerLikeExchange(flat_candles())
            engine = make_engine(ex, tmp)

            engine.risk.state.open_positions = 1   # 어긋난 상태
            engine.risk.sync_open_positions(0)     # 실제로는 포지션 없음
            self.assertEqual(engine.risk.state.open_positions, 0)
            self.assertEqual(engine.risk.halt_reason(ex.equity), "")

    def test_bot_can_enter_again_after_an_exchange_stop(self):
        """스탑 청산 후에도 다음 진입이 승인되어야 한다."""
        with TemporaryDirectory() as tmp:
            ex = BrokerLikeExchange(flat_candles(), equity=10_000.0)
            engine = make_engine(ex, tmp, max_open_positions=1, max_daily_loss_pct=0.99)

            first = engine.risk.evaluate_entry(
                equity=10_000, price=100, side=Side.LONG, stop_loss=95, leverage=2
            )
            self.assertTrue(first.approved)

            ex.open_position(SYM, Side.LONG, 1.0, stop_loss=95.0)
            engine._sync_closed_trades()
            ex.stop_out(loss=10.0)
            engine._sync_closed_trades()
            engine.risk.sync_open_positions(0)     # 매 틱 하는 보정

            second = engine.risk.evaluate_entry(
                equity=9_990, price=100, side=Side.LONG, stop_loss=95, leverage=2
            )
            self.assertTrue(second.approved, f"재진입이 막혔습니다: {second.reason}")


class TestEquityFallback(unittest.TestCase):
    """청산 감지가 완전히 실패해도 자본 낙폭이 막아야 한다."""

    def test_daily_limit_works_with_no_trade_records_at_all(self):
        rm = RiskManager(RiskConfig(max_daily_loss_pct=0.03), kill_switch_file="/nonexistent")
        rm.roll_day(1000.0)
        self.assertEqual(rm.halt_reason(1000.0), "")
        reason = rm.halt_reason(960.0)             # 기록 0건, 자본만 -4%
        self.assertIn("일일 손실 한도", reason)
        self.assertIn("자본 낙폭", reason)

    def test_worse_of_the_two_measures_wins(self):
        rm = RiskManager(RiskConfig(max_daily_loss_pct=0.03), kill_switch_file="/nonexistent")
        rm.roll_day(1000.0)
        rm.record_trade(Trade("B", Side.LONG, 1, 100, 60, 0, 1))   # 기록상 -40
        # 자본은 아직 -10만 반영된 상태여도 기록 쪽이 더 나쁘면 그쪽으로 막는다
        self.assertIn("실현손익", rm.halt_reason(990.0))

    def test_profit_does_not_trigger_the_limit(self):
        rm = RiskManager(RiskConfig(max_daily_loss_pct=0.03), kill_switch_file="/nonexistent")
        rm.roll_day(1000.0)
        self.assertEqual(rm.halt_reason(1200.0), "")


if __name__ == "__main__":
    unittest.main()


class PeakEquityRestoreTests(unittest.TestCase):
    """재시작이 낙폭 한도를 초기화하면 안 된다.

    고점에서 15% 내려온 상태로 봇을 다시 켰을 때 그 자리가 새 고점이 되면,
    한도가 영영 안 걸린다. 연속 손실 카운터를 복구하는 이유와 같다.
    """

    def setUp(self):
        import tempfile

        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "j.db"

    def tearDown(self):
        self.dir.cleanup()

    def journal(self):
        from dorothy.journal.store import Journal

        return Journal(self.path)

    def test_empty_journal_reports_zero(self):
        j = self.journal()
        self.assertEqual(j.peak_equity(), 0.0)
        j.close()

    def test_reports_the_highest_value_ever_seen(self):
        j = self.journal()
        for ts, equity in ((1, 1000.0), (2, 1500.0), (3, 1200.0)):
            j.record_equity(ts, equity)
        self.assertEqual(j.peak_equity(), 1500.0)
        j.close()

    def test_peak_survives_reopening(self):
        j = self.journal()
        j.record_equity(1, 2000.0)
        j.close()
        j2 = self.journal()
        self.assertEqual(j2.peak_equity(), 2000.0)
        j2.close()

    def test_engine_restores_the_peak_on_start(self):
        """이게 핵심이다. 재시작 후에도 예전 고점 대비로 판정해야 한다."""
        from dorothy.config import Config
        from dorothy.data.loader import synthetic
        from dorothy.engine import TradingEngine
        from dorothy.exchange.paper import ReplayExchange
        from dorothy.strategy.base import get_strategy

        j = self.journal()
        j.record_equity(1, 5000.0)          # 예전 고점
        j.close()

        cfg = Config()
        cfg.db_path = str(self.path)
        cfg.initial_equity = 4000.0          # 고점에서 20% 내려온 상태로 재시작
        cfg.poll_interval_sec = 0
        cfg.risk.max_drawdown_pct = 0.15
        cfg.strategy.name = "donchian"
        cfg.strategy.params = {"channel": 20}

        exchange = ReplayExchange(synthetic(300, seed=3), equity=cfg.initial_equity)
        engine = TradingEngine(
            cfg, exchange, get_strategy(cfg.strategy.name, **cfg.strategy.params)
        )
        # start()가 부르는 바로 그 복구를 부른다. 여기서 복구 코드를 베껴
        # 쓰면 엔진이 복구를 안 해도 테스트가 통과한다.
        engine._restore_state()

        self.assertEqual(engine.risk.state.peak_equity, 5000.0,
                         "재시작이 고점을 잊었습니다")
        reason = engine.risk.halt_reason(4000.0)
        self.assertIn("고점 대비 낙폭", reason,
                      "고점 5000 대비 20% 낙폭인데 안 막혔습니다")


class StopVerificationTests(unittest.TestCase):
    """손절이 거래소에 실제로 걸렸는지 확인하는가.

    `pos.stop_loss = stop_loss`는 **우리가 요청한 값**을 넣을 뿐이다.
    거래소가 stopLossPrice를 무시해도 봇은 손절이 있다고 믿는다.
    보호받는다고 믿으면서 무방비인 것이 제일 나쁜 실패다.
    """

    @staticmethod
    def _bare(call):
        """네트워크 없이 _verify_stop만 시험한다.

        client는 None이면 안 된다 — _call에 넘기기 전에 client.fetch_open_orders를
        읽으므로 거기서 먼저 터진다. 실제로 이 픽스처를 그렇게 짰다가 걸렸다.
        """
        from types import SimpleNamespace

        from dorothy.exchange.bitget import BitgetExchange

        ex = BitgetExchange.__new__(BitgetExchange)      # __init__ 우회 (ccxt 불필요)
        ex.client = SimpleNamespace(fetch_open_orders=lambda *a, **k: [])
        ex._call = call
        return ex

    def exchange(self, open_orders):
        return self._bare(lambda fn, *a, **k: open_orders)

    def test_accepts_a_matching_stop_price(self):
        ex = self.exchange([{"stopPrice": 50000.0}])
        with self.assertNoLogs("dorothy.exchange.bitget", level="WARNING"):
            ex._verify_stop("BTC/USDT:USDT", 50000.0)

    def test_accepts_a_trigger_price_field(self):
        ex = self.exchange([{"triggerPrice": 50000.0}])
        with self.assertNoLogs("dorothy.exchange.bitget", level="WARNING"):
            ex._verify_stop("BTC/USDT:USDT", 50000.0)

    def test_accepts_a_nested_preset_field(self):
        ex = self.exchange([{"info": {"presetStopLossPrice": "50000"}}])
        with self.assertNoLogs("dorothy.exchange.bitget", level="WARNING"):
            ex._verify_stop("BTC/USDT:USDT", 50000.0)

    def test_tolerates_small_rounding(self):
        ex = self.exchange([{"stopPrice": 50010.0}])         # 0.02% 차이
        with self.assertNoLogs("dorothy.exchange.bitget", level="WARNING"):
            ex._verify_stop("BTC/USDT:USDT", 50000.0)

    def test_warns_when_no_stop_is_present(self):
        """이게 핵심이다. 손절이 없으면 반드시 시끄러워야 한다."""
        ex = self.exchange([])
        with self.assertLogs("dorothy.exchange.bitget", level="WARNING") as logs:
            ex._verify_stop("BTC/USDT:USDT", 50000.0)
        self.assertIn("손절", "\n".join(logs.output))

    def test_warns_when_the_price_is_wrong(self):
        ex = self.exchange([{"stopPrice": 40000.0}])         # 20% 어긋남
        with self.assertLogs("dorothy.exchange.bitget", level="WARNING"):
            ex._verify_stop("BTC/USDT:USDT", 50000.0)

    def test_warns_when_the_query_itself_fails(self):
        """조회 실패를 조용히 넘기면 무방비 상태를 모른 채 매매하게 된다."""
        def boom(*a, **k):
            raise RuntimeError("네트워크")

        ex = self._bare(boom)
        with self.assertLogs("dorothy.exchange.bitget", level="WARNING") as logs:
            ex._verify_stop("BTC/USDT:USDT", 50000.0)
        self.assertIn("확인하지 못했습니다", "\n".join(logs.output))

    def test_a_failed_query_does_not_raise(self):
        """확인 실패가 매매 자체를 막으면 안 된다. 경고로 충분하다."""
        ex = self._bare(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        ex._verify_stop("BTC/USDT:USDT", 50000.0)      # 예외가 새어나오면 실패

    def test_garbage_values_do_not_crash(self):
        ex = self.exchange([{"stopPrice": "없음"}, {"triggerPrice": None}])
        with self.assertLogs("dorothy.exchange.bitget", level="WARNING"):
            ex._verify_stop("BTC/USDT:USDT", 50000.0)


class FrozenBookExchange(BrokerLikeExchange):
    """새 봉이 아직 안 나온 상태의 거래소. 몇 번을 물어도 같은 캔들을 준다."""

    def fetch_candles(self, symbol, timeframe, limit=200):
        return self.candles[-limit:]

    def fetch_price(self, symbol):
        # 상위 클래스는 cursor를 쓰는데 여기선 cursor가 안 움직인다.
        # 안 덮으면 신호는 새 봉으로 내고 체결은 옛 봉 가격으로 하게 된다.
        return self.candles[-1].close

    def append(self, candle):
        self.candles = [*self.candles, candle]


def _breakout_candles(n=60, tf_ms=43_200_000):
    """마지막 봉이 10봉 고가를 뚫고 마감한 12시간봉."""
    c = [Candle(i * tf_ms, 100, 100, 100, 100, 1.0) for i in range(n - 1)]
    c.append(Candle((n - 1) * tf_ms, 100, 130, 100, 130, 1.0))
    return c


class RestartDoesNotRetradeTheSameBar(unittest.TestCase):
    """재시작하면 마지막 마감봉을 다시 판단하는가.

    **이 파일이 막는 사고:**
      09:00  봉 마감, 돌파 → 진입
      11:00  손절 체결
      12:00  봇 재시작 (배포·크래시·노트북 절전)
      → 마지막 마감봉은 여전히 09:00 봉이고 돌파는 아직 참이다.

    _last_candle_ts를 복구하지 않으면 방금 손절당한 그 자리에 다시 들어간다.
    재시작이 반복되면 연속 손실 차단에 걸릴 때까지 같은 매매를 되풀이한다.
    """

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "t.db")

    def engine(self, exchange):
        from dorothy.config import Config
        cfg = Config()
        cfg.mode = "paper"
        cfg.exchange.symbol = SYM
        cfg.poll_interval_sec = 0
        cfg.db_path = self.db
        cfg.kill_switch_file = str(Path(self.tmp.name) / "NOKILL")
        cfg.risk = RiskConfig(risk_per_trade=0.01, max_position_pct=10.0)
        e = TradingEngine(
            cfg, exchange, get_strategy("donchian", channel=10),
            journal=Journal(self.db), notifier=Notifier(enabled=False),
        )
        e._restore_state()
        e.risk.roll_day(exchange.equity)
        return e

    @staticmethod
    def _opens(exchange):
        return [o for o in exchange.orders if o[0] == "open"]

    def test_a_restart_does_not_re_enter_the_bar_it_just_stopped_out_on(self):
        ex = FrozenBookExchange(_breakout_candles(), equity=1000.0)
        first = self.engine(ex)
        first.tick()
        self.assertEqual(len(self._opens(ex)), 1, "첫 진입이 안 났습니다")

        ex.stop_out(20.0)          # 거래소 스탑 발동
        first.tick()               # 봇이 청산을 인지

        self.engine(ex).tick()     # 재시작 — 새 봉은 아직 없다
        self.assertEqual(
            len(self._opens(ex)), 1,
            "재시작이 같은 봉에 다시 진입했습니다 (_last_candle_ts 미복구)",
        )

    def test_a_restart_loop_does_not_bleed(self):
        """크래시 루프. 한 봉 안에서 몇 번을 재시작해도 진입은 한 번뿐이어야 한다."""
        ex = FrozenBookExchange(_breakout_candles(), equity=1000.0)
        self.engine(ex).tick()
        for _ in range(5):
            if ex.position is not None:
                ex.stop_out(20.0)
            self.engine(ex).tick()
        self.assertEqual(len(self._opens(ex)), 1,
                         f"재시작 5회에 진입이 {len(self._opens(ex))}번 났습니다")

    def test_a_new_bar_is_still_traded_after_a_restart(self):
        """과잉 차단도 버그다. 새 봉이 오면 정상적으로 판단해야 한다."""
        ex = FrozenBookExchange(_breakout_candles(), equity=1000.0)
        self.engine(ex).tick()
        ex.stop_out(20.0)
        self.engine(ex).tick()
        self.assertEqual(len(self._opens(ex)), 1)

        # 새 12시간봉이 마감되고, 다시 돌파했다
        ex.append(Candle(60 * 43_200_000, 130, 160, 130, 160, 1.0))
        self.engine(ex).tick()
        self.assertEqual(len(self._opens(ex)), 2,
                         "새 봉인데도 판단을 건너뛰었습니다")

    def test_the_position_is_recorded_before_the_order_goes_out(self):
        """주문 도중에 죽으면 '신호 한 번 놓침'이어야 한다. '두 번 진입'이 아니라."""
        ex = FrozenBookExchange(_breakout_candles(), equity=1000.0)
        engine = self.engine(ex)

        boom = RuntimeError("주문 도중 프로세스 사망")

        def explode(*a, **kw):
            raise boom

        engine.executor.handle = explode
        with self.assertRaises(RuntimeError):
            engine.tick()

        # 죽기 전에 이미 기록되어 있어야 한다
        self.assertEqual(
            Journal(self.db).last_candle_ts(), ex.candles[-1].ts,
            "주문 전에 기록하지 않아, 재시작하면 같은 봉을 다시 판단합니다",
        )

    def test_replay_does_not_persist_its_position(self):
        """리플레이는 깨끗한 상태에서 시작해야 한다 — 저널을 더럽히면 안 된다."""
        ex = FrozenBookExchange(_breakout_candles(), equity=1000.0)
        engine = self.engine(ex)
        engine._replay_clock = True
        engine.tick()
        self.assertEqual(Journal(self.db).last_candle_ts(), 0)
