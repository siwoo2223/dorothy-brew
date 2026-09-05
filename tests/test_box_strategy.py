"""박스 매매 전략.

**이 파일이 막는 사고:**
박스 매매는 승률이 높고 손익비가 나쁜 구조라, 승률만 보면 잘 되는 것처럼
보인다. 그래서 여기서는 '이겼는가'가 아니라 **규칙을 지키는가**를 본다 —
박스 밖에서 들어가지 않는가, 손절이 경계 밖에 붙는가, 전제가 깨지면 나오는가.
"""

import unittest

from dorothy.models import Action, Candle, Position, Side
from dorothy.strategy.base import get_strategy


def bar(i, o, h, l, c):
    return Candle(i * 3_600_000, o, h, l, c, 1.0)


def box_candles(n=40, lo=98.0, hi=102.0, last_close=None):
    """상·하단을 번갈아 찍는 박스. 마지막 종가만 따로 지정할 수 있다."""
    out = []
    for i in range(n):
        if i % 2:
            out.append(bar(i, lo + 1, hi, lo + 0.9, hi - 0.2))
        else:
            out.append(bar(i, hi - 1, hi - 0.9, lo, lo + 0.2))
    if last_close is not None:
        p = last_close
        out[-1] = bar(n - 1, p, max(p, hi - 0.9), min(p, lo + 0.9), p)
    return out


def strat(**kw):
    return get_strategy("box", lookback=30, min_height_pct=0.001, **kw)


class EntryRuleTests(unittest.TestCase):
    def test_buys_near_the_bottom(self):
        sig = strat().generate(box_candles(last_close=98.3), None)
        self.assertIs(sig.action, Action.ENTER_LONG)

    def test_holds_in_the_middle(self):
        sig = strat().generate(box_candles(last_close=100.0), None)
        self.assertIs(sig.action, Action.HOLD)
        self.assertIn("중간", sig.reason)

    def test_does_not_buy_the_top(self):
        sig = strat().generate(box_candles(last_close=101.8), None)
        self.assertIsNot(sig.action, Action.ENTER_LONG)

    def test_shorts_the_top_only_when_enabled(self):
        top = box_candles(last_close=101.8)
        self.assertIs(strat(allow_short=False).generate(top, None).action, Action.HOLD)
        self.assertIs(strat(allow_short=True).generate(top, None).action,
                      Action.ENTER_SHORT)

    def test_never_enters_outside_the_box(self):
        """이탈했는데 되돌림을 노리는 것은 박스 매매가 아니다."""
        broken = box_candles()
        broken[-1] = bar(39, 102.0, 105.0, 101.9, 104.5)   # 상단 돌파
        sig = strat(allow_short=True).generate(broken, None)
        self.assertIs(sig.action, Action.HOLD)

    def test_no_box_means_no_entry(self):
        trend = [bar(i, 100 + i, 100 + i + 0.3, 100 + i - 0.1, 100 + i + 0.2)
                 for i in range(40)]
        sig = strat().generate(trend, None)
        self.assertIs(sig.action, Action.HOLD)
        self.assertIn("박스 없음", sig.reason)


class StopRuleTests(unittest.TestCase):
    def test_the_stop_sits_below_the_box_floor(self):
        """손절 근거가 전략의 전제와 같아야 한다 — 박스가 깨지면 나간다."""
        sig = strat().generate(box_candles(last_close=98.3), None)
        self.assertLess(sig.stop_loss, 98.0, "손절이 박스 하단 위에 있습니다")

    def test_the_stop_is_not_exactly_on_the_edge(self):
        """경계와 같으면 노이즈에 즉시 털린다."""
        sig = strat().generate(box_candles(last_close=98.3), None)
        self.assertLess(sig.stop_loss, 98.0 - 1e-9)

    def test_the_target_is_the_far_edge(self):
        sig = strat().generate(box_candles(last_close=98.3), None)
        self.assertAlmostEqual(sig.take_profit, 102.0, places=6)

    def test_a_short_stop_sits_above_the_ceiling(self):
        sig = strat(allow_short=True).generate(box_candles(last_close=101.8), None)
        self.assertGreater(sig.stop_loss, 102.0)


class ExitRuleTests(unittest.TestCase):
    def setUp(self):
        self.long = Position("BTC/USDT:USDT", Side.LONG, 1.0, 98.3)

    def test_exits_at_the_far_edge(self):
        sig = strat().generate(box_candles(last_close=101.8), self.long)
        self.assertIs(sig.action, Action.EXIT)

    def test_holds_while_inside(self):
        sig = strat().generate(box_candles(last_close=100.0), self.long)
        self.assertIs(sig.action, Action.HOLD)

    def test_exits_when_the_box_disappears(self):
        """**전제가 깨지면 목표를 기다리지 않는다.**"""
        trend = [bar(i, 100 + i, 100 + i + 0.3, 100 + i - 0.1, 100 + i + 0.2)
                 for i in range(40)]
        sig = strat().generate(trend, self.long)
        self.assertIs(sig.action, Action.EXIT)
        self.assertIn("소멸", sig.reason)


class ValidationTests(unittest.TestCase):
    def test_zones_must_be_ordered(self):
        with self.assertRaises(ValueError):
            get_strategy("box", entry_zone=0.8, exit_zone=0.2)

    def test_zero_stop_buffer_is_rejected(self):
        with self.assertRaises(ValueError):
            get_strategy("box", stop_buffer=0.0)

    def test_tiny_lookback_is_rejected(self):
        with self.assertRaises(ValueError):
            get_strategy("box", lookback=3)


class CausalityTests(unittest.TestCase):
    def test_future_candles_do_not_change_the_signal(self):
        """미래 봉을 붙여도 같은 시점의 판단은 같아야 한다."""
        base = box_candles(last_close=98.3)
        later = base + [bar(40 + i, 120, 125, 119, 124) for i in range(10)]
        a = strat().generate(base, None)
        b = strat().generate(later[: len(base)], None)
        self.assertEqual((a.action, a.stop_loss, a.take_profit),
                         (b.action, b.stop_loss, b.take_profit))

    def test_the_analysis_window_is_bounded(self):
        """전체 히스토리를 훑으면 O(n²)가 된다."""
        import time
        long_run = box_candles(4000, lo=98.0, hi=102.0)
        s = strat()

        def per_bar(n):
            t0 = time.perf_counter()
            for i in range(n - 60, n):
                s.generate(long_run[: i + 1], None)
            return (time.perf_counter() - t0) / 60

        self.assertLess(per_bar(4000), per_bar(1000) * 2.5)
