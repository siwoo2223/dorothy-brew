"""매매일지 분석 테스트.

핵심은 두 가지다:
- 순열검정이 **실제 신호가 있을 때만** 반응하는가 (없는 신호를 만들어내면 안 된다)
- 위험 신호가 실제 위험한 상황에서만 뜨는가
"""

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from dorothy.journal.analyze import Analysis, permutation_p_value, report
from dorothy.journal.records import JournalTrade, from_rows, load_csv, load_json

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "journal_sample.csv"

CHECKLIST_HEADER = "자동화 전에 채워야 할 것:"


def checklist_of(text: str) -> str:
    """리포트에서 체크리스트 구간만 잘라낸다.

    리포트는 같은 단어가 여러 섹션에 나온다("손절액 기록" vs "손절액 기록됨").
    전체 텍스트를 상대로 assertNotIn을 걸면 엉뚱한 곳에 걸려 테스트가 거짓 실패한다.
    """
    if CHECKLIST_HEADER not in text:
        return ""
    return text.split(CHECKLIST_HEADER, 1)[1]


def trade(pnl, *, margin=100.0, side="롱", tags=None, risk=0.0, day=1, lev=10.0,
          index=0, rationale=""):
    return JournalTrade(
        index=index,
        traded_on=date(2026, 1, day),
        symbol="BTCUSDT",
        side=side,
        leverage=lev,
        entry_price=42000.0,
        exit_price=42000.0 + pnl,
        margin=margin,
        pnl=pnl,
        planned_risk=risk,
        tags=list(tags or []),
        rationale=rationale,
    )


class TestLoading(unittest.TestCase):
    def test_sample_file_loads(self):
        trades = load_csv(SAMPLE)
        self.assertEqual(len(trades), 15)
        self.assertTrue(all(t.symbol for t in trades))

    def test_trades_are_sorted_by_date(self):
        trades = load_csv(SAMPLE)
        dates = [t.traded_on for t in trades]
        self.assertEqual(dates, sorted(dates))

    def test_korean_and_english_headers_both_work(self):
        korean = from_rows([{"방향": "롱", "손익": "12.5", "시작금액": "100"}])
        english = from_rows([{"side": "롱", "pnl": "12.5", "margin": "100"}])
        self.assertEqual(korean[0].pnl, english[0].pnl)
        self.assertEqual(korean[0].side, english[0].side)

    def test_currency_symbols_and_commas_are_stripped(self):
        t = from_rows([{"손익": "$1,234.50", "시작금액": "$2,000"}])[0]
        self.assertAlmostEqual(t.pnl, 1234.50)

    def test_notion_json_array_tags(self):
        t = from_rows([{"손익": "5", "시작금액": "100", "실수 태그": '["뇌동매매", "FOMO"]'}])[0]
        self.assertEqual(t.tags, ["뇌동매매", "FOMO"])

    def test_comma_separated_tags(self):
        t = from_rows([{"손익": "5", "시작금액": "100", "실수 태그": "뇌동매매, FOMO"}])[0]
        self.assertEqual(t.tags, ["뇌동매매", "FOMO"])

    def test_unknown_columns_are_ignored(self):
        t = from_rows([{"손익": "5", "시작금액": "100", "알수없는칸": "무시"}])
        self.assertEqual(len(t), 1)

    def test_empty_rows_are_dropped(self):
        self.assertEqual(from_rows([{"손익": "", "시작금액": ""}]), [])

    def test_notion_expanded_date_key(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "j.json"
            p.write_text(
                '{"results": [{"date:날짜:start": "2026-03-05", "손익": 10, "시작금액": 100}]}',
                encoding="utf-8",
            )
            trades = load_json(p)
        self.assertEqual(trades[0].traded_on, date(2026, 3, 5))


class TestTradeMetrics(unittest.TestCase):
    def test_return_pct(self):
        self.assertAlmostEqual(trade(20.0, margin=100.0).return_pct, 20.0)

    def test_r_multiple_needs_a_planned_stop(self):
        self.assertIsNone(trade(20.0).r_multiple)
        self.assertAlmostEqual(trade(20.0, risk=10.0).r_multiple, 2.0)

    def test_losing_trade_at_planned_stop_is_minus_one_r(self):
        self.assertAlmostEqual(trade(-10.0, risk=10.0).r_multiple, -1.0)

    def test_price_move_is_direction_aware(self):
        long = JournalTrade(side="롱", entry_price=100, exit_price=110, margin=1, pnl=1)
        short = JournalTrade(side="숏", entry_price=100, exit_price=90, margin=1, pnl=1)
        self.assertAlmostEqual(long.price_move_pct, 10.0)
        self.assertAlmostEqual(short.price_move_pct, 10.0)   # 숏은 하락이 이득

    def test_zero_margin_does_not_crash(self):
        self.assertEqual(JournalTrade(margin=0, pnl=5).return_pct, 0.0)


class TestSetupExtraction(unittest.TestCase):
    """매매일지 스킬이 남기는 `[셋업이름]` 접두사를 읽어낸다.

    이 형식이 있어야 '어떤 진입이 통하는가'를 그룹으로 묶을 수 있다.
    자유 서술만으로는 분류가 불가능하다.
    """

    def test_extracts_bracketed_setup(self):
        t = trade(1.0, rationale="[유동성스윕] 전일 저가 쓸고 반등")
        self.assertEqual(t.setup, "유동성스윕")
        self.assertTrue(t.has_setup)

    def test_setup_without_description(self):
        self.assertEqual(trade(1.0, rationale="[되돌림]").setup, "되돌림")

    def test_free_text_is_unclassified(self):
        t = trade(1.0, rationale="그냥 느낌으로 진입")
        self.assertEqual(t.setup, "(미분류)")
        self.assertFalse(t.has_setup)

    def test_empty_rationale(self):
        t = trade(1.0, rationale="")
        self.assertEqual(t.setup, "(없음)")
        self.assertFalse(t.has_setup)

    def test_leading_whitespace_is_tolerated(self):
        self.assertEqual(trade(1.0, rationale="  [돌파] 설명").setup, "돌파")

    def test_absurdly_long_bracket_is_not_a_setup(self):
        """대괄호 안이 너무 길면 셋업 이름이 아니라 그냥 문장이다."""
        t = trade(1.0, rationale="[" + "가" * 40 + "] 설명")
        self.assertFalse(t.has_setup)

    def test_grouping_by_setup(self):
        trades = [
            trade(10.0, rationale="[유동성스윕] a"),
            trade(5.0, rationale="[유동성스윕] b"),
            trade(-3.0, rationale="[감] c"),
        ]
        groups = {g.label: g for g in Analysis(trades).by_setup()}
        self.assertEqual(groups["유동성스윕"].n, 2)
        self.assertAlmostEqual(groups["유동성스윕"].total_pnl, 15.0)

    def test_setup_coverage(self):
        trades = [trade(1.0, rationale="[돌파] x"), trade(1.0, rationale="자유 서술")]
        self.assertAlmostEqual(Analysis(trades).setup_coverage, 50.0)

    def test_coverage_is_zero_without_rationale(self):
        self.assertEqual(Analysis([trade(1.0)]).setup_coverage, 0.0)


class TestPermutationTest(unittest.TestCase):
    """검정이 신호를 만들어내지도, 놓치지도 않는가."""

    def test_identical_groups_give_high_p(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertGreater(permutation_p_value(a, b, iterations=3000), 0.5)

    def test_clearly_separated_groups_give_low_p(self):
        a = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
        b = [-10.0, -11.0, -12.0, -13.0, -14.0, -15.0]
        self.assertLess(permutation_p_value(a, b, iterations=3000), 0.01)

    def test_small_noisy_difference_is_not_significant(self):
        a = [1.0, -2.0, 3.0, -1.0, 2.0]
        b = [0.5, -1.0, 2.0, -2.0, 1.0]
        self.assertGreater(permutation_p_value(a, b, iterations=3000), 0.2)

    def test_empty_group_returns_one(self):
        self.assertEqual(permutation_p_value([], [1.0, 2.0]), 1.0)

    def test_is_deterministic_for_a_given_seed(self):
        a, b = [1.0, 5.0, 3.0], [2.0, 8.0, 4.0]
        self.assertEqual(
            permutation_p_value(a, b, iterations=2000, seed=3),
            permutation_p_value(a, b, iterations=2000, seed=3),
        )


class TestGroupStats(unittest.TestCase):
    def test_expectancy_uses_percent_not_amount(self):
        """증거금이 커지는 계좌에서 금액 기대값은 최근 매매에 끌려간다."""
        trades = [trade(10.0, margin=100.0), trade(10.0, margin=1000.0)]
        stat = Analysis(trades).overall
        self.assertAlmostEqual(stat.expectancy, 10.0)
        self.assertAlmostEqual(stat.expectancy_pct, 5.5)   # (10% + 1%) / 2

    def test_profit_factor(self):
        trades = [trade(20.0), trade(-10.0)]
        self.assertAlmostEqual(Analysis(trades).overall.profit_factor, 2.0)

    def test_grouping_by_side(self):
        trades = [trade(10.0, side="롱"), trade(-5.0, side="숏"), trade(3.0, side="롱")]
        groups = {g.label: g for g in Analysis(trades).by_side()}
        self.assertEqual(groups["롱"].n, 2)
        self.assertAlmostEqual(groups["롱"].total_pnl, 13.0)

    def test_multi_tag_trade_counts_in_every_tag(self):
        trades = [trade(10.0, tags=["FOMO", "뇌동매매"])]
        labels = {g.label for g in Analysis(trades).by_tag()}
        self.assertEqual(labels, {"FOMO", "뇌동매매"})

    def test_untagged_trades_are_grouped_separately(self):
        trades = [trade(10.0), trade(5.0, tags=["계획대로"])]
        labels = {g.label for g in Analysis(trades).by_tag()}
        self.assertIn("(없음)", labels)


class TestRiskSignals(unittest.TestCase):
    def test_rewarded_bad_habit_is_detected(self):
        """나쁜 습관이 돈을 벌고 있으면 반드시 경고해야 한다."""
        trades = [trade(50.0, tags=["과도한 레버리지"]), trade(30.0, tags=["과도한 레버리지"])]
        flagged = [g.label for g in Analysis(trades).rewarded_bad_habits()]
        self.assertIn("과도한 레버리지", flagged)

    def test_losing_bad_habit_is_not_flagged_as_rewarded(self):
        trades = [trade(-50.0, tags=["뇌동매매"])]
        self.assertEqual(Analysis(trades).rewarded_bad_habits(), [])

    def test_good_habit_is_never_flagged(self):
        trades = [trade(50.0, tags=["계획대로"])]
        self.assertEqual(Analysis(trades).rewarded_bad_habits(), [])

    def test_concentration_detects_one_big_winner(self):
        trades = [trade(100.0), trade(2.0), trade(1.0)]
        self.assertGreater(Analysis(trades).concentration, 90)

    def test_even_profits_have_low_concentration(self):
        trades = [trade(10.0) for _ in range(10)]
        self.assertAlmostEqual(Analysis(trades).concentration, 10.0)

    def test_pnl_without_best_removes_the_top_trade(self):
        trades = [trade(100.0), trade(-20.0), trade(5.0)]
        self.assertAlmostEqual(Analysis(trades).pnl_without_best, -15.0)

    def test_stop_overruns_detect_broken_stops(self):
        trades = [trade(-50.0, risk=20.0), trade(-19.0, risk=20.0)]
        overruns = Analysis(trades).stop_overruns
        self.assertEqual(len(overruns), 1)
        self.assertAlmostEqual(overruns[0].pnl, -50.0)

    def test_consistent_sizing_has_low_variation(self):
        trades = [trade(1.0, margin=100.0) for _ in range(5)]
        self.assertLess(Analysis(trades).margin_variation, 0.01)

    def test_erratic_sizing_has_high_variation(self):
        trades = [trade(1.0, margin=m) for m in (10.0, 500.0, 20.0, 900.0)]
        self.assertGreater(Analysis(trades).margin_variation, 0.5)

    def test_missing_stop_ratio(self):
        trades = [trade(1.0, risk=0.0), trade(1.0, risk=10.0)]
        self.assertAlmostEqual(Analysis(trades).missing_stop_ratio, 50.0)

    def test_max_consecutive_losses(self):
        trades = [trade(1.0), trade(-1.0), trade(-1.0), trade(-1.0), trade(1.0)]
        self.assertEqual(Analysis(trades).max_consecutive_losses, 3)


class TestReport(unittest.TestCase):
    def test_report_renders_for_the_sample(self):
        text = report(Analysis(load_csv(SAMPLE)))
        self.assertIn("매매일지 분석", text)
        self.assertIn("위험 신호", text)
        self.assertIn("자동화 후보", text)

    def test_report_includes_r_analysis_when_stops_recorded(self):
        text = report(Analysis(load_csv(SAMPLE)))
        self.assertIn("1회 평균 R", text)
        self.assertIn("이겼을 때 평균", text)

    def test_report_omits_r_analysis_without_stops(self):
        """손절액이 없으면 R 섹션은 나오지 않아야 한다.

        (안내 문구의 "R 분석이 열립니다"와 헷갈리지 않도록
        섹션 고유 문구로 확인한다.)
        """
        trades = [trade(10.0, day=d) for d in range(1, 6)]
        self.assertNotIn("1회 평균 R", report(Analysis(trades)))

    def test_report_warns_about_missing_stops(self):
        trades = [trade(10.0, day=d) for d in range(1, 6)]
        self.assertIn("손절액이", report(Analysis(trades)))

    def test_small_sample_is_always_flagged(self):
        trades = [trade(10.0, day=d) for d in range(1, 6)]
        self.assertIn("잠정적", report(Analysis(trades)))

    def test_empty_journal_does_not_crash(self):
        self.assertIn("없습니다", report(Analysis([])))

    def test_setup_section_appears_when_setups_recorded(self):
        text = report(Analysis(load_csv(SAMPLE)))
        self.assertIn("표기된 비율", text)      # 섹션 머리말 고유 문구
        self.assertIn("유동성스윕", text)

    def test_setup_section_is_skipped_without_setups(self):
        """셋업 표기가 없으면 표를 그리지 않는다.

        (경고 문구에도 '셋업별'이 들어가므로 섹션 고유 문구로 확인한다.)
        """
        trades = [trade(10.0, day=d) for d in range(1, 6)]
        self.assertNotIn("표기된 비율", report(Analysis(trades)))

    def test_checklist_only_lists_actual_gaps(self):
        """이미 갖춘 항목까지 '채우라'고 하면 리포트를 믿지 않게 된다."""
        items = checklist_of(report(Analysis(load_csv(SAMPLE))))
        self.assertNotIn("손절액 기록", items)         # 샘플은 손절액 100% 기록됨
        self.assertNotIn("[셋업] 표기", items)         # 샘플은 셋업 100% 표기됨
        self.assertIn("건 더 쌓기", items)             # 표본만 부족하다

    def test_checklist_lists_missing_stops(self):
        trades = [trade(10.0, day=d, rationale="[돌파] x") for d in range(1, 6)]
        self.assertIn("손절액 기록", checklist_of(report(Analysis(trades))))

    def test_missing_setup_is_warned(self):
        trades = [trade(10.0, day=d, risk=5.0) for d in range(1, 6)]
        self.assertIn("[셋업] 표기", checklist_of(report(Analysis(trades))))

    def test_no_stop_tag_counts_as_a_bad_habit(self):
        """스킬이 붙이는 '손절 미설정' 태그도 나쁜 습관으로 추적되어야 한다."""
        trades = [trade(50.0, tags=["손절 미설정"]), trade(30.0, tags=["손절 미설정"])]
        flagged = [g.label for g in Analysis(trades).rewarded_bad_habits()]
        self.assertIn("손절 미설정", flagged)


if __name__ == "__main__":
    unittest.main()
