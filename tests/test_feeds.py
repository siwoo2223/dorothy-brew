"""Bitget client and live feed, driven by a fake transport (no network)."""

import json
import unittest
import urllib.error
import urllib.parse

from dorothy_brew import synth
from dorothy_brew.core import Series
from dorothy_brew.feeds import (BitgetClient, BitgetError, LiveFeed, JsonHttp,
                                normalize_timeframe, timeframe_seconds)
from dorothy_brew.feeds.http import HttpError


class FakeBitget:
    """Serves candle rows in Bitget's v2 envelope and records the requests."""

    def __init__(self, series=None, bars=None):
        source = series or synth.trending_market()
        self.rows = [[str(c.ts * 1000), f"{c.open}", f"{c.high}", f"{c.low}",
                      f"{c.close}", f"{c.volume}", "0"] for c in source]
        self.visible = len(self.rows) if bars is None else bars
        self.calls = []
        self.fail_times = 0
        self.status = 200
        self.envelope_code = "00000"

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise urllib.error.URLError("boom")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.last_query = {k: v[0] for k, v in query.items()}
        if self.envelope_code != "00000":
            body = {"code": self.envelope_code, "msg": "bad symbol", "data": []}
            return self.status, json.dumps(body).encode()
        if "/public/symbols" in url or "/market/contracts" in url:
            return self.status, json.dumps(
                {"code": "00000", "data": [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]}).encode()
        if "/market/ticker" in url:
            return self.status, json.dumps(
                {"code": "00000", "data": [{"symbol": "BTCUSDT", "lastPr": "63500.5"}]}).encode()
        limit = int(query.get("limit", ["100"])[0])
        end = query.get("endTime", [None])[0]
        rows = self.rows[:self.visible]
        if end is not None:
            rows = [r for r in rows if int(r[0]) < int(end)]
        return self.status, json.dumps({"code": "00000", "msg": "success",
                                        "data": rows[-limit:]}).encode()

    def advance(self, bars: int = 1) -> None:
        self.visible = min(len(self.rows), self.visible + bars)

    @property
    def last_ts(self) -> int:
        return int(self.rows[self.visible - 1][0]) // 1000


def client_for(fake, product="usdt-futures", **kw):
    return BitgetClient(product, http=JsonHttp(transport=fake, sleeper=lambda s: None, **kw))


class TestTimeframes(unittest.TestCase):
    def test_normalisation(self):
        for given, want in (("1H", "1h"), ("1hour", "1h"), ("60m", "1h"), ("15min", "15m"),
                            ("1D", "1d"), ("1day", "1d"), ("1week", "1w"), ("1M", "1M")):
            self.assertEqual(normalize_timeframe(given), want)

    def test_unsupported_timeframe_is_rejected(self):
        for bad in ("2h", "", "7m", "banana"):
            with self.assertRaises(ValueError):
                normalize_timeframe(bad)

    def test_seconds(self):
        self.assertEqual(timeframe_seconds("1H"), 3600)
        self.assertEqual(timeframe_seconds("15m"), 900)
        self.assertEqual(timeframe_seconds("1d"), 86400)

    def test_granularity_differs_per_product(self):
        fake = FakeBitget()
        self.assertEqual(client_for(fake, "spot").granularity("1h"), "1h")
        self.assertEqual(client_for(fake, "spot").granularity("1d"), "1day")
        self.assertEqual(client_for(fake, "usdt-futures").granularity("1h"), "1H")
        self.assertEqual(client_for(fake, "usdt-futures").granularity("1d"), "1D")


class TestClient(unittest.TestCase):
    def setUp(self):
        self.fake = FakeBitget()
        self.client = client_for(self.fake)

    def test_candles_are_parsed_into_a_series(self):
        series = self.client.candles("btcusdt", "1H", limit=40)
        self.assertEqual(len(series), 40)
        self.assertEqual(series.symbol, "BTCUSDT")
        self.assertEqual(series.timeframe, "1h")
        self.assertTrue(all(a.ts < b.ts for a, b in zip(series, series[1:])))
        self.assertTrue(all(c.high >= c.low for c in series))

    def test_request_carries_product_and_granularity(self):
        self.client.candles("BTCUSDT", "4h", limit=10)
        self.assertEqual(self.fake.last_query["productType"], "usdt-futures")
        self.assertEqual(self.fake.last_query["granularity"], "4H")
        self.assertEqual(self.fake.last_query["symbol"], "BTCUSDT")

    def test_spot_omits_product_type(self):
        spot = client_for(self.fake, "spot")
        spot.candles("BTCUSDT", "1h", limit=5)
        self.assertNotIn("productType", self.fake.last_query)
        self.assertIn("/api/v2/spot/market/candles", self.fake.calls[-1])

    def test_history_pages_backwards(self):
        series = self.client.history("BTCUSDT", "1h", bars=150, page=50)
        self.assertEqual(len(series), 150)
        self.assertTrue(all(a.ts < b.ts for a, b in zip(series, series[1:])))
        self.assertGreater(len(self.fake.calls), 2)
        self.assertIn("history-candles", "".join(self.fake.calls[1:]))

    def test_history_stops_when_the_exchange_runs_out(self):
        series = self.client.history("BTCUSDT", "1h", bars=10_000, page=200)
        self.assertLessEqual(len(series), len(self.fake.rows))
        self.assertGreater(len(series), 0)

    def test_ticker_and_last_price(self):
        self.assertEqual(self.client.ticker("BTCUSDT")["symbol"], "BTCUSDT")
        self.assertAlmostEqual(self.client.last_price("BTCUSDT"), 63500.5)

    def test_symbols(self):
        self.assertEqual(self.client.symbols(), ["BTCUSDT", "ETHUSDT"])

    def test_business_error_code_raises(self):
        self.fake.envelope_code = "40034"
        with self.assertRaises(BitgetError) as ctx:
            self.client.candles("NOPE", "1h")
        self.assertEqual(ctx.exception.code, "40034")

    def test_bad_product_is_rejected(self):
        with self.assertRaises(ValueError):
            BitgetClient("perpetual-swaps")


class TestHttp(unittest.TestCase):
    def test_retries_then_succeeds(self):
        fake = FakeBitget()
        fake.fail_times = 2
        client = client_for(fake, retries=3)
        self.assertTrue(len(client.candles("BTCUSDT", "1h", limit=5)))
        self.assertEqual(len(fake.calls), 3)

    def test_gives_up_after_the_retry_budget(self):
        fake = FakeBitget()
        fake.fail_times = 99
        client = client_for(fake, retries=2)
        with self.assertRaises(urllib.error.URLError):
            client.candles("BTCUSDT", "1h")
        self.assertEqual(len(fake.calls), 3)

    def test_http_error_status_is_reported(self):
        fake = FakeBitget()
        fake.status = 404
        client = client_for(fake, retries=0)
        with self.assertRaises(HttpError) as ctx:
            client.candles("BTCUSDT", "1h")
        self.assertEqual(ctx.exception.status, 404)

    def test_retryable_status_is_retried(self):
        fake = FakeBitget()
        fake.status = 429
        client = client_for(fake, retries=1)
        with self.assertRaises(HttpError):
            client.candles("BTCUSDT", "1h")
        self.assertEqual(len(fake.calls), 2)


class TestLiveFeed(unittest.TestCase):
    def _feed(self, visible=None, **kw):
        self.fake = FakeBitget(bars=visible or 190)
        self.now = self.fake.last_ts + 3600
        feed = LiveFeed(client_for(self.fake), "BTCUSDT", "1h", window=200,
                        clock=lambda: self.now, sleeper=lambda s: None, **kw)
        return feed

    def test_prime_loads_history(self):
        feed = self._feed()
        feed.prime(bars=150)
        self.assertEqual(len(feed.series), 150)
        self.assertEqual(feed.series.timeframe, "1h")

    def test_forming_candle_is_held_out_of_the_series(self):
        feed = self._feed()
        self.now = self.fake.last_ts + 60          # the last bar has not closed yet
        feed.prime(bars=100)
        self.assertEqual(feed.forming.ts, self.fake.last_ts)
        self.assertTrue(all(c.ts < self.fake.last_ts for c in feed.series))

    def test_poll_returns_none_until_a_bar_closes(self):
        feed = self._feed()
        feed.prime(bars=150)
        self.assertIsNone(feed.poll())

    def test_poll_emits_an_event_on_a_new_closed_bar(self):
        feed = self._feed()
        feed.prime(bars=180)
        feed.poll()
        self.fake.advance(1)
        self.now += 3600
        event = feed.poll()
        self.assertIsNotNone(event)
        self.assertEqual(event.candle.ts, self.fake.last_ts)
        self.assertEqual(event.report.bars, len(feed.series))

    def test_the_same_setup_is_not_re_announced(self):
        feed = self._feed()
        feed.prime(bars=180)
        self.fake.advance(1)
        self.now += 3600
        first = feed.poll()
        self.assertTrue(first.new_signals)
        self.fake.advance(1)
        self.now += 3600
        second = feed.poll()
        self.assertLess(len(second.new_signals), len(first.new_signals))
        # a setup carried over from the first bar must not be announced twice
        keys = {feed._signal_key(s) for s in first.new_signals}
        self.assertFalse(keys & {feed._signal_key(s) for s in second.new_signals})

    def test_window_is_bounded(self):
        feed = self._feed(visible=120)
        feed.prime(bars=100)
        feed.window = 60
        for _ in range(5):
            self.fake.advance(1)
            self.now += 3600
            feed.poll()
        self.assertLessEqual(len(feed.series), 60)

    def test_seconds_to_next_close(self):
        feed = self._feed()
        feed.prime(bars=100)
        gap = feed.seconds_to_next_close()
        self.assertGreater(gap, 0)
        self.assertLessEqual(gap, 2 * 3600 + 10)

    def test_poll_seconds_overrides_the_schedule(self):
        feed = self._feed(poll_seconds=30)
        feed.prime(bars=100)
        self.assertEqual(feed.seconds_to_next_close(), 30)

    def test_run_stops_after_max_polls(self):
        feed = self._feed()
        feed.prime(bars=180)
        events = []
        polls = feed.run(events.append, max_polls=3)
        self.assertEqual(polls, 3)

    def test_run_survives_a_transport_failure(self):
        feed = self._feed()
        feed.prime(bars=180)
        self.fake.fail_times = 99
        events = []
        self.assertEqual(feed.run(events.append, max_polls=2), 2)
        self.assertEqual(events, [])

    def test_run_honours_should_stop(self):
        feed = self._feed()
        feed.prime(bars=180)
        self.assertEqual(feed.run(lambda e: None, max_polls=5, should_stop=lambda: True), 0)

    def test_signals_are_scanned_on_closed_bars_only(self):
        feed = self._feed()
        self.now = self.fake.last_ts + 60
        feed.prime(bars=150)
        report = feed.scan_now()
        self.assertTrue(all(s.ts < self.fake.last_ts for s in report.signals))


if __name__ == "__main__":
    unittest.main()
