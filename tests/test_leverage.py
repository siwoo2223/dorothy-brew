"""레버리지 트레이드오프 테스트.

이 표를 보고 실제로 배율을 정하게 된다. 잘못된 판정은 계좌를 날린다.
특히 청산 처리가 틀리면 파산한 계좌가 되살아나 고배율이 실제보다 좋아 보인다.
"""

import unittest

from dorothy.backtest.leverage import LeverageTable, Rung, analyse_leverage
from dorothy.backtest.vol_target import Curve
from dorothy.config import Config
from dorothy.models import Candle


def bar(ts, o, h, l, c, v=1.0):
    return Candle(ts, o, h, l, c, v)


def series(closes, step=14_400_000):
    return [bar(i * step, c, c, c, c) for i, c in enumerate(closes)]


def curve(returns_pct, mdd_pct, ruined=False, funding=0.0):
    """수익률과 MDD를 직접 지정한 곡선. 판정 로직만 시험할 때 쓴다."""
    equity = [1.0, 1.0 * (1 - mdd_pct / 100), 1.0 * (1 + returns_pct / 100)]
    c = Curve("x", equity)
    c.ruined = ruined
    c.funding_paid = funding
    return c


class RungTests(unittest.TestCase):
    def test_ruined_follows_the_curve(self):
        self.assertTrue(Rung("보유", 3.0, curve(-99, 99, ruined=True)).ruined)
        self.assertFalse(Rung("보유", 1.0, curve(100, 50)).ruined)


class TableVerdictTests(unittest.TestCase):
    def build(self, entries):
        table = LeverageTable()
        for label, lev, ret, mdd, ruined in entries:
            table.rungs.append(Rung(label, lev, curve(ret, mdd, ruined)))
        return table

    def test_best_ignores_ruined_rungs(self):
        """청산된 배율이 '최선'으로 뽑히면 표가 사람을 죽인다."""
        table = self.build([
            ("보유", 1.0, 774.2, 77.1, False),
            ("보유", 3.0, 5000.0, 99.9, True),
        ])
        self.assertEqual(table.best.leverage, 1.0)

    def test_says_so_when_leverage_never_helps(self):
        table = self.build([
            ("보유", 1.0, 774.2, 77.1, False),
            ("보유", 2.0, 24.6, 97.9, False),
        ])
        report = table.report()
        self.assertIn("배율을 올리는 것이 어떤 경우에도 도움이 되지 않았습니다", report)

    def test_no_such_claim_when_leverage_does_help(self):
        table = self.build([
            ("보유", 1.0, 100.0, 50.0, False),
            ("보유", 2.0, 400.0, 55.0, False),
        ])
        self.assertNotIn("어떤 경우에도 도움이 되지 않았습니다", table.report())

    def test_reports_where_returns_peak(self):
        table = self.build([
            ("보유", 1.0, 774.2, 77.1, False),
            ("보유", 1.5, 394.2, 92.0, False),
            ("보유", 3.0, -99.3, 99.9, False),
        ])
        report = table.report()
        self.assertIn("1배에서 정점", report)
        self.assertIn("-99.3%", report)

    def test_all_ruined_is_reported_not_crashed(self):
        table = self.build([("보유", 3.0, -99.0, 99.9, True)])
        self.assertIn("모든 배율에서 청산", table.report())

    def test_report_marks_ruined_rows(self):
        table = self.build([("보유", 3.0, -99.0, 99.9, True)])
        self.assertIn("청산", table.report())


class AnalyseLeverageTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()

    def rising(self, n=800, rate=0.001):
        return series([100 * ((1 + rate) ** i) for i in range(n)])

    def choppy(self, n=800, amp=0.03):
        closes, price = [], 100.0
        for i in range(n):
            price *= 1 + (amp if i % 2 else -amp)
            closes.append(price)
        return series(closes)

    def test_covers_every_level_for_both_strategies(self):
        table = analyse_leverage(self.rising(), self.cfg, lookback=60,
                                 levels=(1.0, 2.0))
        self.assertEqual(len(table.rungs), 4)
        self.assertEqual(len(table.by_label("매수 후 보유")), 2)
        self.assertEqual(len(table.by_label("변동성 타게팅")), 2)

    def test_leverage_multiplies_return_in_a_smooth_rise(self):
        """드래그가 없는 구간에서는 배율이 수익을 키워야 한다.
        여기서도 안 늘면 배율 적용 자체가 틀린 것이다."""
        table = analyse_leverage(self.rising(), self.cfg, lookback=60,
                                 levels=(1.0, 2.0))
        one, two = table.by_label("매수 후 보유")
        self.assertGreater(two.curve.return_pct, one.curve.return_pct)

    def test_volatility_drag_punishes_leverage_in_chop(self):
        """오르내리기만 하는 구간에서는 배율이 높을수록 나빠져야 한다."""
        table = analyse_leverage(self.choppy(), self.cfg, lookback=60,
                                 levels=(1.0, 2.0))
        one, two = table.by_label("매수 후 보유")
        self.assertLess(two.curve.return_pct, one.curve.return_pct)

    def test_higher_leverage_never_lowers_drawdown(self):
        table = analyse_leverage(self.choppy(), self.cfg, lookback=60,
                                 levels=(1.0, 1.5, 2.0))
        for label in ("매수 후 보유", "변동성 타게팅"):
            rungs = sorted(table.by_label(label), key=lambda r: r.leverage)
            mdds = [r.curve.max_drawdown_pct for r in rungs]
            self.assertEqual(mdds, sorted(mdds), f"{label}: {mdds}")

    def test_funding_grows_with_leverage(self):
        table = analyse_leverage(self.rising(), self.cfg, lookback=60,
                                 levels=(1.0, 2.0))
        one, two = table.by_label("매수 후 보유")
        self.assertEqual(one.curve.funding_paid, 0.0)   # 1배는 현물
        self.assertGreater(two.curve.funding_paid, 0.0)

    def test_a_crash_ruins_high_leverage(self):
        crash = series([100] * 200 + [20] + [100] * 200)
        table = analyse_leverage(crash, self.cfg, lookback=60, levels=(1.0, 3.0))
        one, three = table.by_label("매수 후 보유")
        self.assertFalse(one.ruined)
        self.assertTrue(three.ruined)
        self.assertEqual(three.curve.equity[-1], 0.0)

    def test_all_rungs_share_one_evaluation_window(self):
        """배율끼리 비교하려면 같은 기간을 봐야 한다."""
        table = analyse_leverage(self.rising(), self.cfg, lookback=60,
                                 levels=(1.0, 2.0), start_index=300)
        lengths = {len(r.curve.equity) for r in table.rungs}
        self.assertEqual(len(lengths), 1, f"곡선 길이가 다릅니다: {lengths}")

    def test_report_renders(self):
        table = analyse_leverage(self.rising(), self.cfg, lookback=60)
        self.assertIn("레버리지 트레이드오프", table.report())


if __name__ == "__main__":
    unittest.main()
