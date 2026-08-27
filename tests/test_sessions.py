"""시간대·세션 분석 테스트.

**핵심은 다중검정 보정이다.**
24개 시간대를 재보면 그중 최고는 성과가 완전히 무작위여도 좋아 보인다.
순열검정이 그걸 걸러내지 못하면 이 분석 전체가 과최적화 기계가 된다.
"""

import random
import unittest
from datetime import datetime, timezone

from dorothy.analysis.sessions import (
    Killzone,
    Session,
    is_weekend,
    killzone_of,
    parse_killzones,
    parse_sessions,
    session_of,
    utc_hour,
    weekday,
)
from dorothy.backtest import session_report
from dorothy.backtest.session_report import max_bucket_p_value
from dorothy.config import Config
from dorothy.data.loader import synthetic
from dorothy.models import Action, Candle, Position, Side
from dorothy.strategy.base import get_strategy


def ts_at(hour, day=27, month=8, year=2026):
    return int(datetime(year, month, day, hour, 0, tzinfo=timezone.utc).timestamp() * 1000)


class TestSessionBoundaries(unittest.TestCase):
    def test_hour_extraction_is_utc(self):
        self.assertEqual(utc_hour(ts_at(13)), 13)

    def test_sessions_cover_every_hour(self):
        covered = {session_of(ts_at(h)) for h in range(24)}
        self.assertTrue(covered <= set(Session))
        for h in range(24):
            self.assertIsInstance(session_of(ts_at(h)), Session)

    def test_session_boundaries(self):
        self.assertIs(session_of(ts_at(0)), Session.ASIA)
        self.assertIs(session_of(ts_at(7)), Session.ASIA)
        self.assertIs(session_of(ts_at(8)), Session.LONDON)
        self.assertIs(session_of(ts_at(12)), Session.OVERLAP)
        self.assertIs(session_of(ts_at(16)), Session.NEW_YORK)
        self.assertIs(session_of(ts_at(21)), Session.LATE)

    def test_killzones(self):
        self.assertIs(killzone_of(ts_at(3)), Killzone.ASIAN_RANGE)
        self.assertIs(killzone_of(ts_at(8)), Killzone.LONDON_OPEN)
        self.assertIs(killzone_of(ts_at(13)), Killzone.NEW_YORK_OPEN)
        self.assertIs(killzone_of(ts_at(16)), Killzone.LONDON_CLOSE)
        self.assertIs(killzone_of(ts_at(20)), Killzone.NONE)

    def test_weekend_detection(self):
        self.assertTrue(is_weekend(ts_at(12, day=29)))    # 토
        self.assertTrue(is_weekend(ts_at(12, day=30)))    # 일
        self.assertFalse(is_weekend(ts_at(12, day=27)))   # 목

    def test_weekday_names(self):
        self.assertEqual(weekday(ts_at(12, day=29)), "토")

    def test_unknown_names_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_sessions(["tokyo"])
        with self.assertRaises(ValueError):
            parse_killzones(["power_hour"])


class TestMultipleTestingCorrection(unittest.TestCase):
    """이 클래스가 이 기능 전체의 신뢰도를 지탱한다."""

    def test_random_performance_across_many_buckets_is_not_significant(self):
        """성과가 무작위여도 24구간 중 최고는 좋아 보인다. 속으면 안 된다."""
        rng = random.Random(3)
        labels = [f"{i % 24:02d}시" for i in range(2400)]
        values = [rng.gauss(0, 1) for _ in range(2400)]
        self.assertGreater(max_bucket_p_value(labels, values), 0.2)

    def test_genuine_bias_is_detected(self):
        rng = random.Random(3)
        labels = [f"{i % 24:02d}시" for i in range(2400)]
        values = [rng.gauss(1.5 if labels[i] == "13시" else 0, 1) for i in range(2400)]
        self.assertLess(max_bucket_p_value(labels, values), 0.05)

    def test_more_buckets_make_significance_harder(self):
        """뽑기 횟수가 늘면 같은 편향도 더 의심해야 한다."""
        rng = random.Random(5)
        many = [f"{i % 24:02d}" for i in range(2400)]
        few = ["A" if i % 2 == 0 else "B" for i in range(2400)]
        noise = [rng.gauss(0, 1) for _ in range(2400)]
        p_many = max_bucket_p_value(many, noise)
        p_few = max_bucket_p_value(few, noise)
        self.assertGreater(p_many, p_few)

    def test_too_few_samples_returns_one(self):
        self.assertEqual(max_bucket_p_value(["A", "B"], [1.0, 2.0]), 1.0)

    def test_is_deterministic(self):
        labels = [f"{i % 6}" for i in range(600)]
        values = [float(i % 7) for i in range(600)]
        a = max_bucket_p_value(labels, values, seed=9, runs=1000)
        b = max_bucket_p_value(labels, values, seed=9, runs=1000)
        self.assertEqual(a, b)


class TestSessionFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candles = synthetic(3000, seed=5, timeframe="1h", start=65000.0)
        cls.base = {"channel": 40, "exit_channel": 20}

    def _entries(self, strategy):
        out = []
        for i in range(strategy.warmup, len(self.candles), 3):
            sig = strategy.generate(self.candles[: i + 1], None)
            if sig.action is not Action.HOLD:
                out.append((self.candles[i].ts, sig))
        return out

    def test_only_allowed_sessions_produce_entries(self):
        strategy = get_strategy(
            "session_filter", base="donchian", base_params=self.base,
            sessions=["overlap"],
        )
        entries = self._entries(strategy)
        self.assertGreater(len(entries), 0)
        for ts, sig in entries:
            with self.subTest(ts=ts):
                self.assertIs(session_of(ts), Session.OVERLAP)
                self.assertEqual(sig.meta["session"], "overlap")

    def test_killzone_restriction(self):
        strategy = get_strategy(
            "session_filter", base="donchian", base_params=self.base,
            killzones=["new_york_open"],
        )
        for ts, _ in self._entries(strategy):
            with self.subTest(ts=ts):
                self.assertIs(killzone_of(ts), Killzone.NEW_YORK_OPEN)

    def test_weekend_can_be_skipped(self):
        strategy = get_strategy(
            "session_filter", base="donchian", base_params=self.base, skip_weekend=True
        )
        for ts, _ in self._entries(strategy):
            with self.subTest(ts=ts):
                self.assertFalse(is_weekend(ts))

    def test_filter_reduces_entry_count(self):
        plain = get_strategy("donchian", **self.base)
        filtered = get_strategy(
            "session_filter", base="donchian", base_params=self.base, sessions=["overlap"]
        )
        self.assertLess(len(self._entries(filtered)), len(self._entries(plain)))

    def test_exits_are_never_blocked(self):
        """장이 한산하다고 손실 포지션을 방치할 이유가 없다."""
        strategy = get_strategy(
            "session_filter", base="donchian", base_params=self.base, sessions=["overlap"]
        )
        position = Position("BTC/USDT:USDT", Side.LONG, 1.0, 65000.0)
        actions = {
            strategy.generate(self.candles[: i + 1], position).action
            for i in range(strategy.warmup, 2000, 7)
        }
        self.assertIn(Action.EXIT, actions)

    def test_cannot_wrap_itself(self):
        with self.assertRaises(ValueError):
            get_strategy("session_filter", base="session_filter")


class TestSessionReport(unittest.TestCase):
    def test_report_declines_to_claim_bias_on_structureless_data(self):
        """합성 데이터에는 시각 개념이 없다 — 진짜 시간대 편향은 0이다.

        그런데도 매매를 시간대로 나누면 승률 80%대 구간이 나온다.
        리포트가 그걸 유의하다고 말하면 이 분석은 과최적화 기계가 된다.
        """
        cfg = Config()
        cfg.mode = "backtest"
        cfg.initial_equity = 200.0
        cfg.exchange.timeframe = "1h"
        candles = synthetic(8000, seed=5, timeframe="1h", start=65000.0)
        strategy = get_strategy("donchian", channel=40, exit_channel=20)
        result = session_report.analyse(candles, strategy, cfg)

        hourly = next(b for b in result.breakdowns if "시간대별" in b.title)
        self.assertGreater(hourly.p_value, 0.05, "구조 없는 데이터에서 시간대 편향을 주장했습니다")

    def test_report_renders_all_breakdowns(self):
        cfg = Config()
        cfg.mode = "backtest"
        cfg.initial_equity = 200.0
        cfg.exchange.timeframe = "1h"
        candles = synthetic(3000, seed=5, timeframe="1h", start=65000.0)
        text = session_report.analyse(
            candles, get_strategy("donchian", channel=40, exit_channel=20), cfg
        ).report()
        for heading in ("세션별", "ICT 킬존별", "요일별", "시간대별"):
            self.assertIn(heading, text)


if __name__ == "__main__":
    unittest.main()
