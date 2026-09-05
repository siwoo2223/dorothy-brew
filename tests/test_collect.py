"""수집기 테스트.

네트워크 없이 돌린다. 그래도 이 파일이 수집기 정확성의 대부분을 덮는다 —
통신은 얇게 두고 판단은 전부 파싱·저장 쪽에 몰아뒀기 때문이다.

가장 위험한 실수 둘:
  1. 공격자 방향을 뒤집는 것. 슬리피지 방향이 통째로 반대가 된다.
  2. 빠짐을 기록하지 않는 것. '체결이 없었다'와 '못 받았다'를 섞게 된다.
"""

import json
import tempfile
import unittest
from pathlib import Path

from dorothy.collect.messages import (
    BookDelta,
    BookLevel,
    ParseError,
    Trade,
    parse_binance_depth,
    parse_binance_trade,
    unwrap,
)
from dorothy.collect.runner import CollectSpec, handle
from dorothy.collect.store import Store

# 바이낸스 문서의 실제 형태
AGG_TRADE = {
    "e": "aggTrade", "E": 1700000000123, "s": "BTCUSDT", "a": 555,
    "p": "43210.50", "q": "0.125", "f": 100, "l": 105,
    "T": 1700000000100, "m": True,
}
DEPTH = {
    "e": "depthUpdate", "E": 1700000000200, "s": "BTCUSDT",
    "U": 1000, "u": 1005,
    "b": [["43200.0", "1.5"], ["43199.0", "0"]],
    "a": [["43211.0", "2.0"]],
}


class TradeParseTests(unittest.TestCase):
    def test_reads_every_field(self):
        t = parse_binance_trade(AGG_TRADE)
        self.assertEqual(t.ts, 1700000000100)      # T(체결시각)이지 E(수신시각)가 아니다
        self.assertAlmostEqual(t.price, 43210.50)
        self.assertAlmostEqual(t.qty, 0.125)
        self.assertEqual(t.trade_id, 555)

    def test_aggressor_side_is_inverted_from_m(self):
        """m은 '매수자가 메이커인가'다. 공격자 방향은 그 반대다.

        여기서 부호를 잘못 잡으면 슬리피지 방향이 통째로 반대가 된다.
        """
        self.assertFalse(parse_binance_trade({**AGG_TRADE, "m": True}).is_buy)
        self.assertTrue(parse_binance_trade({**AGG_TRADE, "m": False}).is_buy)

    def test_notional(self):
        self.assertAlmostEqual(parse_binance_trade(AGG_TRADE).notional,
                               43210.50 * 0.125)

    def test_missing_field_is_loud(self):
        """조용히 넘기면 데이터에 구멍이 생긴다."""
        for field in ("T", "p", "q", "m", "a"):
            payload = {k: v for k, v in AGG_TRADE.items() if k != field}
            with self.assertRaises(ParseError, msg=field):
                parse_binance_trade(payload)

    def test_unparseable_number_is_loud(self):
        with self.assertRaises(ParseError):
            parse_binance_trade({**AGG_TRADE, "p": "약 43000"})


class DepthParseTests(unittest.TestCase):
    def test_reads_both_sides(self):
        d = parse_binance_depth(DEPTH)
        self.assertEqual(d.first_id, 1000)
        self.assertEqual(d.final_id, 1005)
        self.assertEqual(len(d.bids), 2)
        self.assertEqual(len(d.asks), 1)

    def test_zero_quantity_is_kept(self):
        """수량 0은 '그 호가가 사라졌다'는 정보다. 버리면 호가창을 복원할 수 없다."""
        d = parse_binance_depth(DEPTH)
        self.assertIn(BookLevel(43199.0, 0.0), d.bids)

    def test_malformed_level_is_loud(self):
        with self.assertRaises(ParseError):
            parse_binance_depth({**DEPTH, "b": [["43200.0"]]})
        with self.assertRaises(ParseError):
            parse_binance_depth({**DEPTH, "b": "43200.0"})


class UnwrapTests(unittest.TestCase):
    def test_combined_stream_envelope(self):
        name, payload = unwrap({"stream": "btcusdt@aggTrade", "data": AGG_TRADE})
        self.assertEqual(name, "btcusdt@aggTrade")
        self.assertEqual(payload["a"], 555)

    def test_bare_message(self):
        name, payload = unwrap(AGG_TRADE)
        self.assertEqual(name, "aggTrade")

    def test_unknown_shape_is_loud(self):
        with self.assertRaises(ParseError):
            unwrap({"result": None, "id": 1})


class SpecTests(unittest.TestCase):
    def test_builds_the_stream_url(self):
        url = CollectSpec(symbol="BTCUSDT").url()
        self.assertIn("fstream.binance.com", url)
        self.assertIn("btcusdt@aggTrade", url)
        self.assertIn("btcusdt@depth@100ms", url)

    def test_spot_and_futures_differ(self):
        """선물 스트림을 현물 주소로 받으면 조용히 다른 시장을 모으게 된다."""
        self.assertNotEqual(CollectSpec(venue="binance-spot").url(),
                            CollectSpec(venue="binance-futures").url())

    def test_rejects_unknown_venue(self):
        with self.assertRaises(ValueError):
            CollectSpec(venue="업비트").url()

    def test_rejects_collecting_nothing(self):
        with self.assertRaises(ValueError):
            CollectSpec(trades=False, book=False).streams()


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "c.db", commit_every=1)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def trade(self, tid, ts=1700000000000, price=43000.0):
        return Trade(ts=ts, price=price, qty=0.1, is_buy=True, trade_id=tid)

    def test_stores_and_counts(self):
        for i in range(5):
            self.store.add_trade(self.trade(i))
        self.assertEqual(self.store.trade_count(), 5)

    def test_duplicate_trade_id_is_ignored(self):
        """재접속하면 같은 체결이 다시 올 수 있다. 두 번 세면 안 된다."""
        self.store.add_trade(self.trade(7))
        self.store.add_trade(self.trade(7))
        self.assertEqual(self.store.trade_count(), 1)

    def test_missing_trade_ids_record_a_gap(self):
        self.store.add_trade(self.trade(1))
        self.store.add_trade(self.trade(5))
        self.assertEqual(self.store.gap_count(), 1)

    def test_consecutive_ids_record_no_gap(self):
        for i in range(1, 6):
            self.store.add_trade(self.trade(i))
        self.assertEqual(self.store.gap_count(), 0)

    def test_book_sequence_break_records_a_gap(self):
        self.store.add_book(BookDelta(1, 100, 105, [BookLevel(1.0, 1.0)], []))
        self.store.add_book(BookDelta(2, 200, 205, [BookLevel(1.0, 1.0)], []))
        self.assertEqual(self.store.gap_count(), 1)

    def test_continuous_book_records_no_gap(self):
        self.store.add_book(BookDelta(1, 100, 105, [BookLevel(1.0, 1.0)], []))
        self.store.add_book(BookDelta(2, 106, 110, [BookLevel(1.0, 1.0)], []))
        self.assertEqual(self.store.gap_count(), 0)

    def test_span_reports_the_time_range(self):
        self.store.add_trade(self.trade(1, ts=1000))
        self.store.add_trade(self.trade(2, ts=5000))
        self.assertEqual(self.store.span(), (1000, 5000))

    def test_meta_round_trip(self):
        self.store.set_meta("symbol", "BTCUSDT")
        self.assertEqual(self.store.get_meta("symbol"), "BTCUSDT")
        self.assertIsNone(self.store.get_meta("없음"))

    def test_summary_warns_about_gaps(self):
        self.store.add_trade(self.trade(1))
        self.store.add_trade(self.trade(9))
        self.assertIn("빠진 구간이 있습니다", self.store.summary())

    def test_data_survives_reopening(self):
        """수집 도중 죽어도 앞부분은 남아 있어야 한다."""
        path = Path(self.dir.name) / "reopen.db"
        with Store(path, commit_every=1) as s:
            s.add_trade(self.trade(1))
            s.add_trade(self.trade(2))
        with Store(path) as s:
            self.assertEqual(s.trade_count(), 2)


class HandleTests(unittest.TestCase):
    """수집기 정확성은 사실상 이 함수가 전부다."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "h.db", commit_every=1)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_routes_a_trade(self):
        kind = handle(json.dumps({"stream": "btcusdt@aggTrade", "data": AGG_TRADE}),
                      self.store)
        self.assertEqual(kind, "trade")
        self.assertEqual(self.store.trade_count(), 1)

    def test_routes_a_depth_update(self):
        kind = handle(json.dumps({"stream": "btcusdt@depth", "data": DEPTH}), self.store)
        self.assertEqual(kind, "book")

    def test_ignores_subscription_replies(self):
        """구독 확인 응답까지 오류로 취급하면 재접속할 때마다 수집기가 죽는다.
        붙자마자 반드시 오는 메시지라 실전에서 즉시 터진다."""
        for control in ({"result": None, "id": 1}, {"id": 7}, {"result": ["a"], "id": 2}):
            self.assertIsNone(handle(json.dumps(control), self.store), control)

    def test_control_message_does_not_stop_real_data(self):
        """제어 메시지 뒤에 오는 진짜 데이터는 정상 처리되어야 한다."""
        handle(json.dumps({"result": None, "id": 1}), self.store)
        handle(json.dumps({"stream": "btcusdt@aggTrade", "data": AGG_TRADE}), self.store)
        self.assertEqual(self.store.trade_count(), 1)

    def test_bad_json_is_loud(self):
        with self.assertRaises(ParseError):
            handle("이건 JSON이 아님", self.store)

    def test_non_dict_is_loud(self):
        with self.assertRaises(ParseError):
            handle(json.dumps([1, 2, 3]), self.store)

    def test_a_realistic_burst(self):
        """같은 밀리초에 가격을 밟고 올라가는 연속 체결 — 이게 슬리피지다."""
        for i, price in enumerate((43210.0, 43211.5, 43213.0, 43216.5)):
            payload = {**AGG_TRADE, "a": 900 + i, "p": str(price), "m": False}
            handle(json.dumps(payload), self.store)
        self.assertEqual(self.store.trade_count(), 4)
        self.assertEqual(self.store.gap_count(), 0)


if __name__ == "__main__":
    unittest.main()


class DiskFootprintTests(unittest.TestCase):
    """몇 달 모으려면 용량이 진짜 제약이다.

    실측 81바이트/행 · depth@100ms는 초당 10메시지. 메시지당 20레벨이면
    하루 1.4GB, 3개월 126GB다. 줄이는 장치가 실제로 줄이는지 확인한다.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "c.db")

    @staticmethod
    def _delta(ts, fid, mid=70_000.0, span=200.0, levels=10):
        step = span / levels
        bids = [BookLevel(mid - (i + 1) * step, 1.0) for i in range(levels)]
        asks = [BookLevel(mid + (i + 1) * step, 1.0) for i in range(levels)]
        return BookDelta(ts=ts, first_id=fid, final_id=fid, bids=bids, asks=asks)

    def test_near_pct_drops_far_levels(self):
        store = Store(self.path, near_pct=0.001)      # ±0.1% = ±70원
        store.add_trade(Trade(ts=1, trade_id=1, price=70_000.0, qty=1.0, is_buy=True))
        store.add_book(self._delta(2, 10, span=2000.0, levels=10))
        kept = store.conn.execute("SELECT COUNT(*) FROM book").fetchone()[0]
        self.assertGreater(store.counts.book_dropped, 0, "먼 호가를 하나도 안 버렸습니다")
        self.assertLess(kept, 20, "전부 저장됐습니다")
        store.close()

    def test_without_a_reference_price_nothing_is_dropped(self):
        """기준가가 없는데 버리면 무엇을 버렸는지 알 수 없다."""
        store = Store(self.path, near_pct=0.001)
        store.add_book(self._delta(1, 10))            # 체결이 아직 없다
        self.assertEqual(store.counts.book_dropped, 0)
        self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM book").fetchone()[0], 20)
        store.close()

    def test_near_pct_off_keeps_everything(self):
        store = Store(self.path)
        store.add_trade(Trade(ts=1, trade_id=1, price=70_000.0, qty=1.0, is_buy=True))
        store.add_book(self._delta(2, 10, span=5000.0))
        self.assertEqual(store.counts.book_dropped, 0)
        store.close()

    def test_a_negative_band_is_rejected(self):
        with self.assertRaises(ValueError):
            Store(self.path, near_pct=0.0)

    def test_disk_bytes_counts_the_wal(self):
        store = Store(self.path, commit_every=10_000)
        for i in range(500):
            store.add_book(self._delta(i, i + 1))
        self.assertGreater(store.disk_bytes(), 0)
        store.close()

    def test_growth_estimate_reports_per_day(self):
        store = Store(self.path)
        day = 86_400_000
        store.add_trade(Trade(ts=day, trade_id=1, price=70_000.0, qty=1.0, is_buy=True))
        store.add_trade(Trade(ts=day * 3, trade_id=2, price=70_000.0, qty=1.0, is_buy=True))
        store.flush()
        text = store.growth_estimate()
        self.assertIn("하루", text)
        self.assertIn("90일", text)
        store.close()

    def test_growth_estimate_says_so_when_there_is_not_enough_data(self):
        store = Store(self.path)
        self.assertIn("아직", store.growth_estimate())
        store.close()

    def test_the_dropped_count_survives_a_restart(self):
        """몇 달 뒤에 '원래 이만큼이었다'와 '우리가 버렸다'를 구분해야 한다."""
        store = Store(self.path, near_pct=0.001)
        store.add_trade(Trade(ts=1, trade_id=1, price=70_000.0, qty=1.0, is_buy=True))
        store.add_book(self._delta(2, 10, span=2000.0))
        first = store.total_dropped()
        self.assertGreater(first, 0)
        store.close()

        again = Store(self.path, near_pct=0.001)
        self.assertEqual(again.total_dropped(), first, "재시작하면서 잊었습니다")
        again.add_trade(Trade(ts=3, trade_id=2, price=70_000.0, qty=1.0, is_buy=True))
        again.add_book(self._delta(4, 11, span=2000.0))
        self.assertGreater(again.total_dropped(), first, "누적되지 않습니다")
        again.close()

    def test_status_reports_dropping_even_without_the_flag(self):
        """collect-status는 near_pct 없이 파일을 연다. 그래도 알려줘야 한다."""
        store = Store(self.path, near_pct=0.001)
        store.add_trade(Trade(ts=1, trade_id=1, price=70_000.0, qty=1.0, is_buy=True))
        store.add_book(self._delta(2, 10, span=2000.0))
        store.close()

        reader = Store(self.path)            # 플래그 없이 열기
        text = reader.summary()
        self.assertIn("먼 호가 버림", text)
        self.assertIn("0.10%", text)
        reader.close()
