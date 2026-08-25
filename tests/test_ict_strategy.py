"""ICT 합류 전략 테스트.

개별 진입이 '좋은 진입'인지는 테스트할 수 없다(그건 백테스트의 몫이다).
대신 **모든 진입이 반드시 만족해야 하는 불변식**을 검증한다.
불변식이 깨지면 그건 전략의 의견 차이가 아니라 버그다.
"""

import unittest

from dorothy.backtest.diagnostics import ABLATIONS, funnel
from dorothy.data.loader import synthetic
from dorothy.models import Action, Position, Side
from dorothy.strategy.base import get_strategy, known_params
from dorothy.strategy.ict_confluence import IctConfluenceStrategy

CANDLES = synthetic(4000, seed=5, timeframe="15m")


def entries(strategy, candles, step=1):
    """(인덱스, 신호) 목록. 진입 신호만 골라낸다."""
    out = []
    for i in range(strategy.warmup, len(candles), step):
        sig = strategy.generate(candles[: i + 1], None)
        if sig.action is not Action.HOLD:
            out.append((i, sig))
    return out


class TestEntryInvariants(unittest.TestCase):
    """모든 진입 신호가 지켜야 하는 것들."""

    @classmethod
    def setUpClass(cls):
        cls.strategy = get_strategy("ict_confluence", min_displacement_angle=5.0)
        cls.entries = entries(cls.strategy, CANDLES, step=2)

    def test_produces_at_least_one_entry(self):
        self.assertGreater(len(self.entries), 0, "합성 데이터에서 진입이 0건 — 조건 확인 필요")

    def test_every_entry_has_a_stop_loss(self):
        """손절 없는 진입은 리스크 매니저가 거부한다. 애초에 내보내면 안 된다."""
        for i, sig in self.entries:
            with self.subTest(bar=i):
                self.assertIsNotNone(sig.stop_loss)

    def test_stop_is_on_the_losing_side_of_entry(self):
        for i, sig in self.entries:
            entry = CANDLES[i].close
            with self.subTest(bar=i):
                if sig.action is Action.ENTER_LONG:
                    self.assertLess(sig.stop_loss, entry)
                    self.assertGreater(sig.take_profit, entry)
                else:
                    self.assertGreater(sig.stop_loss, entry)
                    self.assertLess(sig.take_profit, entry)

    def test_stop_sits_beyond_the_swept_extreme(self):
        """스윕 극단 '안쪽'에 손절을 두면 같은 자리에서 또 털린다."""
        for i, sig in self.entries:
            sweep_index = sig.meta.get("sweep_index")
            if sweep_index is None:
                continue
            with self.subTest(bar=i):
                window = CANDLES[sweep_index : i + 1]
                if sig.action is Action.ENTER_LONG:
                    self.assertLessEqual(sig.stop_loss, min(c.low for c in window))
                else:
                    self.assertGreaterEqual(sig.stop_loss, max(c.high for c in window))

    def test_risk_reward_meets_the_minimum(self):
        for i, sig in self.entries:
            with self.subTest(bar=i):
                self.assertGreaterEqual(sig.meta["rr"], self.strategy.min_rr)

    def test_confluence_score_meets_the_minimum(self):
        for i, sig in self.entries:
            with self.subTest(bar=i):
                self.assertGreaterEqual(sig.meta["score"], self.strategy.min_score)

    def test_entry_carries_diagnostic_metadata(self):
        for _, sig in self.entries:
            self.assertIn("components", sig.meta)
            self.assertIn("pool", sig.meta)
            self.assertTrue(sig.reason)


class TestCausality(unittest.TestCase):
    def test_future_candles_do_not_change_the_signal(self):
        """전략 전체가 미래를 보지 않는지 확인한다.

        분석 모듈 각각을 검증했더라도, 조합하는 과정에서 미래참조가 새로 생길 수 있다.
        """
        strategy = get_strategy("ict_confluence", min_displacement_angle=5.0)
        cut = 1500
        tampered = CANDLES[:cut] + [
            type(c)(c.ts, c.open * 4, c.high * 4, c.low * 4, c.close * 4, c.volume)
            for c in CANDLES[cut:]
        ]
        for i in (1200, 1350, cut - 1):
            with self.subTest(bar=i):
                a = strategy.generate(CANDLES[: i + 1], None)
                b = strategy.generate(tampered[: i + 1], None)
                self.assertIs(a.action, b.action)
                self.assertEqual(a.stop_loss, b.stop_loss)

    def test_analysis_window_makes_signals_independent_of_ancient_history(self):
        """윈도우 밖 과거를 바꿔도 신호가 같아야 한다 (실전 조건과 동일)."""
        strategy = get_strategy("ict_confluence", analysis_window=300)
        i = 1000
        prefix_changed = [
            type(c)(c.ts, c.open * 2, c.high * 2, c.low * 2, c.close * 2, c.volume)
            for c in CANDLES[:600]
        ] + CANDLES[600 : i + 1]
        a = strategy.generate(CANDLES[: i + 1], None)
        b = strategy.generate(prefix_changed, None)
        self.assertIs(a.action, b.action)


class TestExitBehaviour(unittest.TestCase):
    def test_long_exits_only_on_bearish_choch(self):
        strategy = get_strategy("ict_confluence")
        pos = Position("BTC/USDT:USDT", Side.LONG, 1.0, 100.0)
        actions = {
            strategy.generate(CANDLES[: i + 1], pos).action
            for i in range(strategy.warmup, 2000, 7)
        }
        self.assertTrue(actions <= {Action.HOLD, Action.EXIT}, "보유 중 진입 신호가 나왔습니다")

    def test_exit_can_be_disabled(self):
        strategy = get_strategy("ict_confluence", exit_on_opposite_choch=False)
        pos = Position("BTC/USDT:USDT", Side.LONG, 1.0, 100.0)
        actions = {
            strategy.generate(CANDLES[: i + 1], pos).action
            for i in range(strategy.warmup, 1500, 11)
        }
        self.assertEqual(actions, {Action.HOLD})


class TestConfiguration(unittest.TestCase):
    def test_short_can_be_disabled(self):
        strategy = get_strategy("ict_confluence", allow_short=False, min_displacement_angle=5.0)
        for _, sig in entries(strategy, CANDLES, step=3):
            self.assertIsNot(sig.action, Action.ENTER_SHORT)

    def test_unknown_parameter_is_rejected(self):
        with self.assertRaises(ValueError):
            get_strategy("ict_confluence", min_dispalcement_angle=30.0)   # 오타

    def test_every_ablation_targets_real_parameters(self):
        """제거 실험이 존재하지 않는 파라미터를 건드리면 조용히 무효가 된다."""
        supported = known_params(IctConfluenceStrategy)
        for label, overrides in ABLATIONS.items():
            with self.subTest(ablation=label):
                unknown = set(overrides) - supported
                self.assertFalse(unknown, f"{label}: 존재하지 않는 파라미터 {unknown}")

    def test_min_rr_zero_does_not_crash(self):
        """0으로 나누기 회귀 테스트 — 제거 실험에서 실제로 터졌던 경로."""
        strategy = get_strategy("ict_confluence", min_rr=0.0, min_displacement_angle=5.0)
        for i in range(strategy.warmup, 2000, 5):
            strategy.generate(CANDLES[: i + 1], None)

    def test_warmup_is_respected(self):
        strategy = get_strategy("ict_confluence")
        sig = strategy.generate(CANDLES[: strategy.warmup - 1], None)
        self.assertIs(sig.action, Action.HOLD)


class TestDiagnostics(unittest.TestCase):
    def test_funnel_accounts_for_every_bar(self):
        strategy = get_strategy("ict_confluence")
        result = funnel(CANDLES[:1500], strategy, step=5)
        self.assertEqual(result.entries + sum(result.rejections.values()), result.total_bars)
        self.assertIn("깔때기", result.report())

    def test_funnel_warns_when_nothing_triggers(self):
        # 통과 불가능한 각도를 요구하면 진입이 0건이어야 하고, 경고가 떠야 한다
        strategy = get_strategy("ict_confluence", min_displacement_angle=89.0)
        result = funnel(CANDLES[:1200], strategy, step=5)
        self.assertEqual(result.entries, 0)
        self.assertIn("진입이 0건", result.report())


if __name__ == "__main__":
    unittest.main()
