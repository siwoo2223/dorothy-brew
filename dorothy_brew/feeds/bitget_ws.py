"""Bitget public v2 websocket stream and a live feed built on it.

The REST feed polls; this one is pushed, so a pattern is scanned within a
second of the bar closing rather than at the next poll. The scanning, merging
and de-duplication rules are inherited from :class:`LiveFeed` — only the
transport changes.
"""

from __future__ import annotations

import json
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..core import Candle
from ..data import from_rows
from .bitget import (BitgetClient, BitgetError, LiveEvent, LiveFeed,
                     normalize_timeframe)
from .websocket import WebSocket, WebSocketClosed, WebSocketError

WS_PUBLIC_URL = "wss://ws.bitget.com/v2/ws/public"

INST_TYPES = {
    "spot": "SPOT",
    "usdt-futures": "USDT-FUTURES",
    "coin-futures": "COIN-FUTURES",
    "usdc-futures": "USDC-FUTURES",
}

# canonical timeframe -> the candle channel name
CANDLE_CHANNELS = {
    "1m": "candle1m", "3m": "candle3m", "5m": "candle5m", "15m": "candle15m",
    "30m": "candle30m", "1h": "candle1H", "4h": "candle4H", "6h": "candle6H",
    "12h": "candle12H", "1d": "candle1D", "3d": "candle3D", "1w": "candle1W",
    "1M": "candle1M",
}

PING_TEXT = "ping"
PONG_TEXT = "pong"


def candle_channel(timeframe: str) -> str:
    return CANDLE_CHANNELS[normalize_timeframe(timeframe)]


class BitgetStream:
    """One public websocket connection: subscribe, then read decoded messages.

    Bitget closes a connection that has been silent for 30 seconds, so a plain
    ``"ping"`` text frame is sent on a timer; the server answers ``"pong"``.
    """

    def __init__(self, product: str = "spot", url: str = WS_PUBLIC_URL,
                 connect: Callable[..., WebSocket] = WebSocket.connect,
                 ping_interval: float = 20.0, timeout: float = 10.0,
                 clock: Callable[[], float] = time.monotonic):
        if product not in INST_TYPES:
            raise ValueError(f"product must be one of {sorted(INST_TYPES)}")
        self.product = product
        self.inst_type = INST_TYPES[product]
        self.url = url
        self._connect = connect
        self.ping_interval = ping_interval
        self.timeout = timeout
        self.clock = clock
        self.ws: Optional[WebSocket] = None
        self.subscriptions: List[Dict[str, str]] = []
        self._last_ping = 0.0

    @property
    def connected(self) -> bool:
        return self.ws is not None and not self.ws.closed

    def open(self) -> "BitgetStream":
        self.ws = self._connect(self.url, timeout=self.timeout)
        self._last_ping = self.clock()
        for arg in list(self.subscriptions):
            self._send({"op": "subscribe", "args": [arg]})
        return self

    def _send(self, payload: Dict) -> None:
        if self.ws is None:
            raise WebSocketError("stream is not open")
        self.ws.send(json.dumps(payload))

    def subscribe(self, channel: str, inst_id: str) -> Dict[str, str]:
        arg = {"instType": self.inst_type, "channel": channel,
               "instId": inst_id.upper()}
        if arg not in self.subscriptions:
            self.subscriptions.append(arg)
        if self.connected:
            self._send({"op": "subscribe", "args": [arg]})
        return arg

    def unsubscribe(self, arg: Dict[str, str]) -> None:
        if arg in self.subscriptions:
            self.subscriptions.remove(arg)
        if self.connected:
            self._send({"op": "unsubscribe", "args": [arg]})

    def heartbeat(self) -> None:
        if self.connected and self.clock() - self._last_ping >= self.ping_interval:
            self.ws.send(PING_TEXT)
            self._last_ping = self.clock()

    def next_message(self, timeout: float = 5.0) -> Optional[Dict]:
        """Next decoded message, or None on an idle timeout / keepalive."""
        if self.ws is None:
            raise WebSocketError("stream is not open")
        self.heartbeat()
        raw = self.ws.recv(timeout)
        if raw is None or raw == PONG_TEXT:
            return None
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(message, dict) and message.get("event") == "error":
            raise BitgetError(str(message.get("code", "")), str(message.get("msg", "")))
        return message if isinstance(message, dict) else None

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
            self.ws = None


def candles_from_message(message: Dict, symbol: str, timeframe: str) -> List[Candle]:
    """Pull candle rows out of a push message (same row shape as the REST API)."""
    arg = message.get("arg") or {}
    if not str(arg.get("channel", "")).startswith("candle"):
        return []
    rows = message.get("data") or []
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], (list, tuple)):
        return []
    return list(from_rows(rows, symbol, timeframe).candles)


class BitgetWebSocketFeed(LiveFeed):
    """Live feed pushed by the websocket, primed and gap-filled over REST."""

    def __init__(self, client: BitgetClient, symbol: str, timeframe: str = "1h",
                 stream: Optional[BitgetStream] = None, recv_timeout: float = 5.0,
                 reconnect_delays: Sequence[float] = (1.0, 2.0, 5.0, 10.0, 30.0),
                 **kwargs):
        super().__init__(client, symbol, timeframe, **kwargs)
        self.stream = stream or BitgetStream(client.product, timeout=client.http.timeout)
        self.recv_timeout = recv_timeout
        self.reconnect_delays = list(reconnect_delays)
        self.reconnects = 0

    @property
    def channel(self) -> str:
        return candle_channel(self.timeframe)

    def open(self) -> "BitgetWebSocketFeed":
        self.stream.open()
        self.stream.subscribe(self.channel, self.symbol)
        return self

    def _reconnect(self) -> None:
        """Reopen the stream, then backfill whatever was missed while it was down."""
        delay = self.reconnect_delays[min(self.reconnects, len(self.reconnect_delays) - 1)]
        self.reconnects += 1
        try:
            self.stream.close()
        except (OSError, WebSocketError):
            pass
        self.sleeper(delay)
        self.open()
        try:
            self.fetch()                     # REST gap-fill: the stream only pushes new bars
        except (BitgetError, OSError, RuntimeError):
            pass

    def handle(self, message: Dict) -> Optional[LiveEvent]:
        """Merge a push message; return an event when it closed a bar."""
        candles = candles_from_message(message, self.symbol, self.timeframe)
        if not candles:
            return None
        fresh = self._merge(candles)
        if not fresh:
            return None
        report = self.scan_now()
        return LiveEvent(fresh[-1], report, self._fresh_signals(report))

    def run(self, on_event: Callable[[LiveEvent], None],
            max_events: Optional[int] = None,
            should_stop: Optional[Callable[[], bool]] = None,
            max_messages: Optional[int] = None) -> int:
        """Stream until ``max_events`` bars have closed (or forever).

        A dropped connection is reconnected with backoff and the gap is filled
        over REST, so the loop survives an exchange restart. ``max_messages``
        bounds the total iterations — failed reads included — which is what
        keeps a permanently broken endpoint from spinning.
        """
        if not len(self.series):
            self.prime()
        if not self.stream.connected:
            self.open()
        events = messages = 0
        while max_events is None or events < max_events:
            if should_stop and should_stop():
                break
            if max_messages is not None and messages >= max_messages:
                break
            messages += 1                    # a failed read is still an iteration,
            try:                             # so max_messages bounds reconnect storms
                message = self.stream.next_message(self.recv_timeout)
            except (WebSocketClosed, WebSocketError, OSError, BitgetError):
                self._reconnect()
                continue
            if message is None:
                continue
            event = self.handle(message)
            if event is not None:
                self.reconnects = 0          # a healthy stream resets the backoff
                events += 1
                on_event(event)
        return events

    def close(self) -> None:
        self.stream.close()

    def __enter__(self) -> "BitgetWebSocketFeed":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()
