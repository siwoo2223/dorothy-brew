"""수퍼트렌드 / TMA 테스트.

**핵심은 인과성이다.**
MT4/MT5의 'TMA 밴드'가 널리 쓰이면서도 실전에서 안 되는 이유는
중심이동 평균이 미래를 보기 때문이다. 여기서는 그 사실을 테스트로 고정한다 —
누가 나중에 "이게 더 잘 나오는데요?"라며 기본값을 바꾸지 못하도록.
"""

import unittest

from dorothy.data.indicators import supertrend, tma, tma_centered
from dorothy.data.loader import synthetic
from dorothy.models import Action, Candle, Position, Side
from dorothy.strategy.base import get_strategy

CANDLES = synthetic(2000, seed=5, timeframe="15m", start=65000.0)
HIGHS = [c.high for c in CANDLES]
LOWS = [c.low for c in CANDLES]
CLOSES = [c.close for c in CANDLES]


def tampered_closes(cut=1200, factor=3.0):
    return CLOSES[:cut] + [v * factor for v in CLOSES[cut:]]


class TestSupertrend(unittest.TestCase):
    def test_direction_is_only_plus_or_minus_one(self):
        trend, _ = supertrend(HIGHS, LOWS, CLOSES)
        self.assertTrue(all(t in (None, 1, -1) for t in trend))

    def test_does_not_look_ahead(self):
        """미래 캔들을 바꿔도 과거 값이 그대로여야 한다."""
        cut = 1200
        full, _ = supertrend(HIGHS, LOWS, CLOSES)
        partial, _ = supertrend(HIGHS[:cut], LOWS[:cut], CLOSES[:cut])
        for i in range(100, cut):
            with self.subTest(bar=i):
                self.assertEqual(full[i], partial[i])

    def test_flips_are_infrequent(self):
        """저빈도가 이 전략의 존재 이유다. 자주 뒤집히면 수수료에 녹는다."""
        trend, _ = supertrend(HIGHS, LOWS, CLOSES)
        flips = sum(
            1 for i in range(1, len(trend))
            if trend[i] is not None and trend[i - 1] is not None and trend[i] != trend[i - 1]
        )
        bars_per_flip = len(CANDLES) / max(flips, 1)
        self.assertGreater(bars_per_flip, 20, f"{bars_per_flip:.1f}봉당 1회 — 너무 잦습니다")

    def test_line_sits_below_price_in_uptrend(self):
        trend, line = supertrend(HIGHS, LOWS, CLOSES)
        for i, (t, ln) in enumerate(zip(trend, line)):
            if t == 1 and ln is not None:
                with self.subTest(bar=i):
                    self.assertLessEqual(ln, HIGHS[i])

    def test_rejects_invalid_multiplier(self):
        with self.assertRaises(ValueError):
            get_strategy("supertrend", multiplier=0)


class TestTmaCausality(unittest.TestCase):
    def test_causal_tma_ignores_the_future(self):
        cut = 1200
        clean = tma(CLOSES, 20)
        dirty = tma(tampered_closes(cut), 20)
        for i in range(100, cut):
            with self.subTest(bar=i):
                self.assertEqual(clean[i], dirty[i])

    def test_centered_tma_peeks_at_the_future(self):
        """이 테스트가 통과한다는 건 중심이동이 미래를 본다는 증거다.

        실패하면 tma_centered가 더 이상 중심이동이 아니라는 뜻이므로
        전략 문서의 경고도 함께 손봐야 한다.
        """
        cut = 1200
        clean = tma_centered(CLOSES, 20)
        dirty = tma_centered(tampered_closes(cut), 20)
        differing = [
            i for i in range(cut - 20, cut)
            if clean[i] is not None and clean[i] != dirty[i]
        ]
        self.assertTrue(
            differing,
            "중심이동 TMA가 미래에 반응하지 않습니다 — 구현을 확인하세요",
        )

    def test_centered_only_differs_near_the_boundary(self):
        """미래참조는 절반 주기만큼만 앞을 본다. 그 밖은 같아야 한다."""
        cut = 1200
        clean = tma_centered(CLOSES, 20)
        dirty = tma_centered(tampered_closes(cut), 20)
        for i in range(100, cut - 30):
            with self.subTest(bar=i):
                self.assertEqual(clean[i], dirty[i])


class TestStrategyCausality(unittest.TestCase):
    def test_supertrend_strategy_is_causal(self):
        strategy = get_strategy("supertrend")
        cut = 1500
        tampered = CANDLES[:cut] + [
            Candle(c.ts, c.open * 4, c.high * 4, c.low * 4, c.close * 4, c.volume)
            for c in CANDLES[cut:]
        ]
        for i in (1300, 1400, cut - 1):
            with self.subTest(bar=i):
                a = strategy.generate(CANDLES[: i + 1], None)
                b = strategy.generate(tampered[: i + 1], None)
                self.assertIs(a.action, b.action)
                self.assertEqual(a.stop_loss, b.stop_loss)

    def test_tma_band_default_is_causal(self):
        """기본값은 반드시 인과적이어야 한다."""
        strategy = get_strategy("tma_band")
        self.assertEqual(strategy.mode, "causal")
        cut = 1500
        tampered = CANDLES[:cut] + [
            Candle(c.ts, c.open * 4, c.high * 4, c.low * 4, c.close * 4, c.volume)
            for c in CANDLES[cut:]
        ]
        for i in (1300, 1400, cut - 1):
            with self.subTest(bar=i):
                self.assertIs(
                    strategy.generate(CANDLES[: i + 1], None).action,
                    strategy.generate(tampered[: i + 1], None).action,
                )


class TestTmaModes(unittest.TestCase):
    """delayed 모드가 '실행 가능한 중심이동'인지 확인한다."""

    def _signals(self, mode):
        strategy = get_strategy("tma_band", mode=mode)
        return [
            (i, strategy.generate(CANDLES[: i + 1], None))
            for i in range(strategy.warmup, len(CANDLES), 3)
        ]

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            get_strategy("tma_band", mode="magic")

    def test_centered_mode_produces_no_signals(self):
        """중심이동의 현재 봉 값은 실시간에 존재하지 않는다.

        코드가 막아서가 아니라 계산이 불가능해서 0건이다.
        이 사실을 테스트로 고정해 두면 나중에 누가 '미래참조로 바꿔달라'고 해도
        왜 안 되는지 코드가 답한다.
        """
        entries = [s for _, s in self._signals("centered") if s.action is not Action.HOLD]
        self.assertEqual(len(entries), 0)

    def test_delayed_mode_does_produce_signals(self):
        entries = [s for _, s in self._signals("delayed") if s.action is not Action.HOLD]
        self.assertGreater(len(entries), 0)

    def test_delayed_mode_is_causal(self):
        """지연 모드는 확정된 값만 쓰므로 미래를 보지 않아야 한다."""
        strategy = get_strategy("tma_band", mode="delayed")
        cut = 1500
        tampered = CANDLES[:cut] + [
            Candle(c.ts, c.open * 4, c.high * 4, c.low * 4, c.close * 4, c.volume)
            for c in CANDLES[cut:]
        ]
        for i in (1300, 1400, cut - 1):
            with self.subTest(bar=i):
                a = strategy.generate(CANDLES[: i + 1], None)
                b = strategy.generate(tampered[: i + 1], None)
                self.assertIs(a.action, b.action)
                self.assertEqual(a.stop_loss, b.stop_loss)

    def test_delayed_entries_have_valid_stops(self):
        for i, sig in self._signals("delayed"):
            if sig.action is Action.HOLD:
                continue
            price = CANDLES[i].close
            with self.subTest(bar=i):
                self.assertIsNotNone(sig.stop_loss)
                if sig.action is Action.ENTER_LONG:
                    self.assertLess(sig.stop_loss, price)
                else:
                    self.assertGreater(sig.stop_loss, price)


class TestEntryInvariants(unittest.TestCase):
    def _entries(self, name, **params):
        strategy = get_strategy(name, **params)
        out = []
        for i in range(strategy.warmup, len(CANDLES), 3):
            sig = strategy.generate(CANDLES[: i + 1], None)
            if sig.action is not Action.HOLD:
                out.append((i, sig))
        return out

    def test_supertrend_entries_have_valid_stops(self):
        entries = self._entries("supertrend")
        self.assertGreater(len(entries), 0)
        for i, sig in entries:
            price = CANDLES[i].close
            with self.subTest(bar=i):
                self.assertIsNotNone(sig.stop_loss)
                if sig.action is Action.ENTER_LONG:
                    self.assertLess(sig.stop_loss, price)
                else:
                    self.assertGreater(sig.stop_loss, price)

    def test_tma_entries_have_valid_stops(self):
        for i, sig in self._entries("tma_band"):
            price = CANDLES[i].close
            with self.subTest(bar=i):
                self.assertIsNotNone(sig.stop_loss)
                if sig.action is Action.ENTER_LONG:
                    self.assertLess(sig.stop_loss, price)
                else:
                    self.assertGreater(sig.stop_loss, price)

    def test_short_can_be_disabled(self):
        for name in ("supertrend", "tma_band"):
            with self.subTest(strategy=name):
                for _, sig in self._entries(name, allow_short=False):
                    self.assertIsNot(sig.action, Action.ENTER_SHORT)

    def test_holding_never_produces_an_entry(self):
        for name in ("supertrend", "tma_band"):
            strategy = get_strategy(name)
            pos = Position("BTC/USDT:USDT", Side.LONG, 1.0, 65000.0)
            actions = {
                strategy.generate(CANDLES[: i + 1], pos).action
                for i in range(strategy.warmup, 1500, 11)
            }
            with self.subTest(strategy=name):
                self.assertTrue(actions <= {Action.HOLD, Action.EXIT})


if __name__ == "__main__":
    unittest.main()
