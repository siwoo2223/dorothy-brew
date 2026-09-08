"""Bitget websocket stream and feed, driven by scripted messages (no network)."""

import json
import unittest

from dorothy_brew import synth
from dorothy_brew.config import ScanConfig
from dorothy_brew.feeds import (BitgetClient, BitgetError, BitgetStream,
                                BitgetWebSocketFeed, JsonHttp, candle_channel,
                                candles_from_message)
from dorothy_brew.feeds.bitget_ws import INST_TYPES, PING_TEXT, PONG_TEXT
from dorothy_brew.feeds.websocket import WebSocketClosed, WebSocketError
from tests.test_feeds import FakeBitget, client_for


class FakeSocket:
    """Stands in for a WebSocket: scripted inbound, recorded outbound."""

    def __init__(self, inbound=None):
        self.inbound = list(inbound or [])
        self.sent = []
        self.closed = False

    def send(self, text):
        self.sent.append(text)

    def recv(self, timeout=5.0):
        if self.closed:
            raise WebSocketClosed(1006, "closed")
        if not self.inbound:
            return None
        item = self.inbound.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item)

    def close(self):
        self.closed = True


def candle_message(row, channel="candle1H", inst="BTCUSDT", action="update"):
    return {"action": action,
            "arg": {"instType": "USDT-FUTURES", "channel": channel, "instId": inst},
            "data": [row]}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TestChannels(unittest.TestCase):
    def test_channel_names(self):
        self.assertEqual(candle_channel("1H"), "candle1H")
        self.assertEqual(candle_channel("15m"), "candle15m")
        self.assertEqual(candle_channel("1d"), "candle1D")
        self.assertEqual(candle_channel("1w"), "candle1W")

    def test_unsupported_timeframe(self):
        with self.assertRaises(ValueError):
            candle_channel("2h")

    def test_inst_types_cover_every_product(self):
        from dorothy_brew.feeds import PRODUCTS
        self.assertEqual(sorted(INST_TYPES), sorted(PRODUCTS))


class TestStream(unittest.TestCase):
    def _stream(self, inbound=None, **kw):
        self.sock = FakeSocket(inbound)
        self.clock = Clock()
        return BitgetStream("usdt-futures", connect=lambda *a, **k: self.sock,
                            clock=self.clock, **kw)

    def test_subscribe_payload(self):
        stream = self._stream().open()
        stream.subscribe("candle1H", "btcusdt")
        payload = json.loads(self.sock.sent[-1])
        self.assertEqual(payload["op"], "subscribe")
        self.assertEqual(payload["args"], [{"instType": "USDT-FUTURES",
                                            "channel": "candle1H",
                                            "instId": "BTCUSDT"}])

    def test_subscriptions_are_replayed_on_reopen(self):
        stream = self._stream()
        stream.subscribe("candle1H", "BTCUSDT")      # before connecting
        self.assertEqual(stream.subscriptions and self.sock.sent, [])
        stream.open()
        self.assertEqual(len(self.sock.sent), 1)
        stream.open()
        self.assertEqual(len(self.sock.sent), 2)

    def test_duplicate_subscription_is_not_stored_twice(self):
        stream = self._stream().open()
        stream.subscribe("candle1H", "BTCUSDT")
        stream.subscribe("candle1H", "BTCUSDT")
        self.assertEqual(len(stream.subscriptions), 1)

    def test_unsubscribe(self):
        stream = self._stream().open()
        arg = stream.subscribe("candle1H", "BTCUSDT")
        stream.unsubscribe(arg)
        self.assertEqual(stream.subscriptions, [])
        self.assertEqual(json.loads(self.sock.sent[-1])["op"], "unsubscribe")

    def test_heartbeat_is_sent_on_schedule(self):
        stream = self._stream(ping_interval=20.0).open()
        stream.next_message(1)
        self.assertNotIn(PING_TEXT, self.sock.sent)
        self.clock.t = 25.0
        stream.next_message(1)
        self.assertIn(PING_TEXT, self.sock.sent)

    def test_pong_is_not_a_message(self):
        stream = self._stream([PONG_TEXT]).open()
        self.assertIsNone(stream.next_message(1))

    def test_garbage_is_ignored(self):
        stream = self._stream(["<html>maintenance</html>"]).open()
        self.assertIsNone(stream.next_message(1))

    def test_error_event_raises(self):
        stream = self._stream([{"event": "error", "code": "30001",
                                "msg": "channel does not exist"}]).open()
        with self.assertRaises(BitgetError) as ctx:
            stream.next_message(1)
        self.assertEqual(ctx.exception.code, "30001")

    def test_subscribe_ack_passes_through(self):
        stream = self._stream([{"event": "subscribe", "arg": {"channel": "candle1H"}}]).open()
        self.assertEqual(stream.next_message(1)["event"], "subscribe")

    def test_use_before_open_raises(self):
        stream = self._stream()
        with self.assertRaises(WebSocketError):
            stream.next_message(1)

    def test_bad_product(self):
        with self.assertRaises(ValueError):
            BitgetStream("perpetuals")


class TestMessageParsing(unittest.TestCase):
    def test_candles_are_parsed(self):
        row = ["1700000000000", "100.5", "101", "99.5", "100.8", "12.5", "1250"]
        candles = candles_from_message(candle_message(row), "BTCUSDT", "1h")
        self.assertEqual(len(candles), 1)
        candle = candles[0]
        self.assertEqual(candle.ts, 1700000000)
        self.assertAlmostEqual(candle.close, 100.8)
        self.assertAlmostEqual(candle.volume, 12.5)

    def test_non_candle_channels_are_ignored(self):
        message = {"arg": {"channel": "ticker", "instId": "BTCUSDT"},
                   "data": [{"lastPr": "100"}]}
        self.assertEqual(candles_from_message(message, "BTCUSDT", "1h"), [])

    def test_events_without_data_are_ignored(self):
        for message in ({"event": "subscribe", "arg": {"channel": "candle1H"}},
                        {"arg": {"channel": "candle1H"}, "data": []},
                        {"arg": {"channel": "candle1H"}, "data": [{"not": "a row"}]},
                        {}):
            self.assertEqual(candles_from_message(message, "BTCUSDT", "1h"), [])


class TestWebSocketFeed(unittest.TestCase):
    def setUp(self):
        self.source = synth.trending_market()
        self.rows = [[str(c.ts * 1000), f"{c.open}", f"{c.high}", f"{c.low}",
                      f"{c.close}", f"{c.volume}", "0"] for c in self.source]
        self.rest = FakeBitget(synth.trending_market(), bars=len(self.rows) - 4)
        self.client = client_for(self.rest)
        self.now = self.source[len(self.rows) - 5].ts + 60      # last bar still forming

    def _feed(self, inbound, **kw):
        # a reconnect must get a working socket again, sharing the same script
        self.script = list(inbound)
        self.sockets = []

        def connect(*args, **kwargs):
            sock = FakeSocket(self.script)
            sock.inbound = self.script            # the script is consumed in place
            self.sockets.append(sock)
            return sock

        stream = BitgetStream("usdt-futures", connect=connect)
        feed = BitgetWebSocketFeed(self.client, "BTCUSDT", "1h", stream=stream,
                                   window=200, scan_cfg=ScanConfig(min_confidence=0.7),
                                   clock=lambda: self.now, sleeper=lambda s: None,
                                   reconnect_delays=(0.0,), **kw)
        feed.prime(bars=180)
        return feed

    def test_channel_and_subscription(self):
        feed = self._feed([])
        feed.open()
        self.assertEqual(feed.channel, "candle1H")
        payload = json.loads(self.sockets[-1].sent[-1])
        self.assertEqual(payload["args"][0]["instId"], "BTCUSDT")
        self.assertEqual(payload["args"][0]["channel"], "candle1H")

    def test_forming_updates_do_not_emit(self):
        last = len(self.rows) - 5
        feed = self._feed([candle_message(self.rows[last]),
                           candle_message(self.rows[last])])
        events = []
        self.assertEqual(feed.run(events.append, max_messages=2), 0)
        self.assertEqual(events, [])

    def test_a_new_bar_closes_the_previous_one(self):
        last = len(self.rows) - 5
        feed = self._feed([candle_message(self.rows[last]),
                           candle_message(self.rows[last + 1])])
        events = []
        feed.run(events.append, max_messages=4)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].candle.ts, self.source[last].ts)
        self.assertTrue(events[0].report.signals)

    def test_signals_are_only_announced_once(self):
        last = len(self.rows) - 5
        feed = self._feed([candle_message(self.rows[last]),
                           candle_message(self.rows[last + 1]),
                           candle_message(self.rows[last + 2])])
        events = []
        feed.run(events.append, max_messages=6)
        self.assertEqual(len(events), 2)
        first = {feed._signal_key(s) for s in events[0].new_signals}
        second = {feed._signal_key(s) for s in events[1].new_signals}
        self.assertFalse(first & second)

    def test_max_events_stops_the_loop(self):
        last = len(self.rows) - 5
        feed = self._feed([candle_message(self.rows[i])
                           for i in range(last, last + 4)])
        events = []
        self.assertEqual(feed.run(events.append, max_events=1, max_messages=8), 1)

    def test_should_stop_is_honoured(self):
        feed = self._feed([])
        self.assertEqual(feed.run(lambda e: None, should_stop=lambda: True), 0)

    def test_reconnect_resubscribes_and_backfills(self):
        last = len(self.rows) - 5
        feed = self._feed([WebSocketClosed(1006, "dropped"),
                           candle_message(self.rows[last]),
                           candle_message(self.rows[last + 1])])
        rest_calls_before = len(self.rest.calls)
        events = []
        feed.run(events.append, max_messages=5)
        self.assertGreaterEqual(feed.reconnects, 0)
        subscribes = [json.loads(m) for sock in self.sockets
                      for m in sock.sent if "subscribe" in m]
        self.assertGreaterEqual(len(subscribes), 2)      # initial + after reconnect
        self.assertGreater(len(self.rest.calls), rest_calls_before)   # gap fill
        self.assertEqual(len(events), 1)

    def test_reconnect_after_a_business_error(self):
        feed = self._feed([BitgetError("30001", "bad channel")])
        feed.run(lambda e: None, max_messages=2)
        self.assertGreaterEqual(feed.reconnects, 1)
        self.assertGreater(len(self.sockets), 1)

    def test_successful_bar_resets_the_backoff(self):
        last = len(self.rows) - 5
        feed = self._feed([WebSocketClosed(1006, "dropped"),
                           candle_message(self.rows[last]),
                           candle_message(self.rows[last + 1])])
        feed.run(lambda e: None, max_messages=5)
        self.assertEqual(feed.reconnects, 0)

    def test_context_manager_opens_and_closes(self):
        feed = self._feed([])
        with feed:
            self.assertTrue(feed.stream.connected)
        self.assertFalse(feed.stream.connected)

    def test_idle_messages_are_skipped(self):
        feed = self._feed([PONG_TEXT, None])
        events = []
        self.assertEqual(feed.run(events.append, max_messages=2), 0)


if __name__ == "__main__":
    unittest.main()
