"""Bitget market data (public v2 REST) and a polling live feed.

Public candle endpoints need no API key. Order placement is deliberately not
implemented — this package produces signals, it does not send orders.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from ..config import ScanConfig
from ..core import Candle, Series
from ..data import TIMEFRAME_SECONDS, from_rows
from ..engine import ScanReport, scan
from ..signals import Signal
from .http import JsonHttp

BASE_URL = "https://api.bitget.com"
SPOT = "spot"
PRODUCTS = ("spot", "usdt-futures", "coin-futures", "usdc-futures")

# canonical timeframe -> the string each product family expects
SPOT_GRANULARITY = {
    "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "4h": "4h", "6h": "6h", "12h": "12h",
    "1d": "1day", "3d": "3day", "1w": "1week", "1M": "1M",
}
MIX_GRANULARITY = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1H", "4h": "4H", "6h": "6H", "12h": "12H",
    "1d": "1D", "3d": "3D", "1w": "1W", "1M": "1M",
}
MAX_LIMIT = 1000


class BitgetError(RuntimeError):
    """A non-zero business code in the Bitget response envelope."""

    def __init__(self, code: str, msg: str):
        super().__init__(f"bitget {code}: {msg}")
        self.code = code
        self.msg = msg


def normalize_timeframe(timeframe: str) -> str:
    """'1H', '1hour', '60m' ... -> the canonical '1h'."""
    tf = (timeframe or "").strip()
    if not tf:
        raise ValueError("timeframe is required")
    if tf == "1M":                       # month, the one case where case matters
        return "1M"
    tf = tf.lower().replace("min", "m").replace("hour", "h").replace("hr", "h")
    tf = tf.replace("day", "d").replace("week", "w")
    aliases = {"60m": "1h", "240m": "4h", "24h": "1d", "7d": "1w"}
    tf = aliases.get(tf, tf)
    if tf not in SPOT_GRANULARITY:
        raise ValueError(f"unsupported timeframe '{timeframe}'. "
                         f"supported: {', '.join(SPOT_GRANULARITY)}")
    return tf


def timeframe_seconds(timeframe: str) -> int:
    tf = normalize_timeframe(timeframe)
    if tf == "1M":
        return 30 * 86400
    return TIMEFRAME_SECONDS[tf]


class BitgetClient:
    """Read-only client for Bitget spot and futures candles."""

    def __init__(self, product: str = SPOT, base_url: str = BASE_URL,
                 http: Optional[JsonHttp] = None, timeout: float = 10.0):
        if product not in PRODUCTS:
            raise ValueError(f"product must be one of {PRODUCTS}")
        self.product = product
        self.base_url = base_url.rstrip("/")
        self.http = http or JsonHttp(timeout=timeout)

    # -- plumbing -----------------------------------------------------------
    @property
    def is_spot(self) -> bool:
        return self.product == SPOT

    def granularity(self, timeframe: str) -> str:
        tf = normalize_timeframe(timeframe)
        return (SPOT_GRANULARITY if self.is_spot else MIX_GRANULARITY)[tf]

    def _request(self, path: str, params: Dict) -> object:
        payload = self.http.get(f"{self.base_url}{path}", params)
        if isinstance(payload, dict):
            code = str(payload.get("code", "00000"))
            if code not in ("00000", "0"):
                raise BitgetError(code, str(payload.get("msg", "")))
            return payload.get("data", [])
        return payload

    def _market_params(self, symbol: str, extra: Optional[Dict] = None) -> Dict:
        params = {"symbol": symbol.upper()}
        if not self.is_spot:
            params["productType"] = self.product
        params.update(extra or {})
        return params

    # -- market data --------------------------------------------------------
    def candles(self, symbol: str, timeframe: str = "1h", limit: int = 200,
                start_ms: Optional[int] = None, end_ms: Optional[int] = None,
                history: bool = False) -> Series:
        """Most recent candles (oldest first). ``history=True`` walks backwards."""
        family = "spot" if self.is_spot else "mix"
        endpoint = "history-candles" if history else "candles"
        path = f"/api/v2/{family}/market/{endpoint}"
        params = self._market_params(symbol, {
            "granularity": self.granularity(timeframe),
            "limit": max(1, min(int(limit), MAX_LIMIT)),
            "startTime": start_ms,
            "endTime": end_ms,
        })
        rows = self._request(path, params) or []
        series = from_rows(rows, symbol.upper(), normalize_timeframe(timeframe))
        return series

    def history(self, symbol: str, timeframe: str = "1h", bars: int = 1000,
                page: int = MAX_LIMIT) -> Series:
        """Page backwards until ``bars`` candles are collected."""
        collected: Dict[int, Candle] = {}
        end_ms: Optional[int] = None
        tf = normalize_timeframe(timeframe)
        while len(collected) < bars:
            chunk = self.candles(symbol, tf, limit=min(page, bars - len(collected) + 1),
                                 end_ms=end_ms, history=end_ms is not None)
            if not len(chunk):
                break
            before = len(collected)
            for candle in chunk:
                collected[candle.ts] = candle
            if len(collected) == before:                # no new bars: we hit the start
                break
            end_ms = min(collected) * 1000
        candles = [collected[ts] for ts in sorted(collected)][-bars:]
        return Series(candles, symbol.upper(), tf)

    def ticker(self, symbol: str) -> Dict:
        path = "/api/v2/spot/market/tickers" if self.is_spot else "/api/v2/mix/market/ticker"
        data = self._request(path, self._market_params(symbol))
        if isinstance(data, list):
            return data[0] if data else {}
        return data or {}

    def last_price(self, symbol: str) -> float:
        row = self.ticker(symbol)
        for key in ("lastPr", "last", "close", "lastPrice"):
            if row.get(key) not in (None, ""):
                return float(row[key])
        raise BitgetError("00000", f"no price field in ticker response: {sorted(row)}")

    def symbols(self) -> List[str]:
        path = "/api/v2/spot/public/symbols" if self.is_spot else "/api/v2/mix/market/contracts"
        data = self._request(path, {} if self.is_spot else {"productType": self.product})
        return sorted(str(row.get("symbol", "")) for row in (data or []) if row.get("symbol"))


# ---------------------------------------------------------------------------
# live feed
# ---------------------------------------------------------------------------

@dataclass
class LiveEvent:
    """One closed bar and whatever the scan found on it."""
    candle: Candle
    report: ScanReport
    new_signals: List[Signal] = field(default_factory=list)


class LiveFeed:
    """Polls closed candles and scans each newly closed bar.

    Bitget's REST candle endpoint returns the still-forming bar as its last
    row; that bar is held out of the scan so patterns are only ever confirmed
    on closed data — the same rule the backtester enforces.
    """

    def __init__(self, client: BitgetClient, symbol: str, timeframe: str = "1h",
                 scan_cfg: Optional[ScanConfig] = None, window: int = 400,
                 patterns: Optional[Sequence[str]] = None,
                 categories: Optional[Sequence[str]] = None,
                 poll_seconds: Optional[float] = None, close_delay: float = 3.0,
                 clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep):
        self.client = client
        self.symbol = symbol.upper()
        self.timeframe = normalize_timeframe(timeframe)
        self.tf_seconds = timeframe_seconds(timeframe)
        self.scan_cfg = scan_cfg or ScanConfig()
        self.window = window
        self.patterns = patterns
        self.categories = categories
        self.poll_seconds = poll_seconds
        self.close_delay = close_delay
        self.clock = clock
        self.sleeper = sleeper
        self.series = Series([], self.symbol, self.timeframe)
        self.forming: Optional[Candle] = None
        self._seen: set = set()

    # -- candle bookkeeping -------------------------------------------------
    def _is_closed(self, candle: Candle) -> bool:
        return candle.ts + self.tf_seconds <= self.clock()

    def _merge(self, incoming: Iterable[Candle]) -> List[Candle]:
        """Add closed candles to the series; keep the forming one aside.

        A bar is closed either because the clock says so, or because a bar with
        a later timestamp exists — the second rule is what lets the websocket
        feed close a bar the instant the next one starts streaming, instead of
        waiting on clock skew.
        """
        known = {c.ts for c in self.series}
        fresh: List[Candle] = []
        for candle in sorted(incoming, key=lambda c: c.ts):
            forming = self.forming
            if forming is not None and candle.ts > forming.ts:
                if forming.ts not in known:
                    fresh.append(forming)
                    known.add(forming.ts)
                self.forming = None
            if self._is_closed(candle):
                if candle.ts not in known:
                    fresh.append(candle)
                    known.add(candle.ts)
            else:
                self.forming = candle
        if fresh:
            fresh.sort(key=lambda c: c.ts)
            candles = self.series.candles + fresh
            self.series = Series(candles[-self.window:], self.symbol, self.timeframe)
        return fresh

    def prime(self, bars: Optional[int] = None) -> Series:
        """Load history so the first poll already has a full lookback."""
        bars = bars or self.window
        series = self.client.history(self.symbol, self.timeframe, bars=bars)
        self.series = Series([], self.symbol, self.timeframe)
        self._merge(series.candles)
        return self.series

    def fetch(self, limit: int = 100) -> List[Candle]:
        chunk = self.client.candles(self.symbol, self.timeframe, limit=limit)
        return self._merge(chunk.candles)

    # -- scanning -----------------------------------------------------------
    def scan_now(self) -> ScanReport:
        return scan(self.series, self.scan_cfg, patterns=self.patterns,
                    categories=self.categories)

    def _signal_key(self, sig: Signal):
        """Identify a setup by its structure, not by the bar it was scanned on.

        ``sig.ts`` is the scan time and changes every bar, so keying on it would
        re-announce the same flag on every poll. The bar the structure starts on
        stays put while the setup lives, and a genuinely new setup of the same
        pattern starts somewhere else.
        """
        start_ts = self.series[sig.start_index].ts if sig.start_index < len(self.series) else 0
        return (sig.pattern_id, sig.direction, sig.status, start_ts)

    def _fresh_signals(self, report: ScanReport) -> List[Signal]:
        out = []
        for sig in report.signals:
            key = self._signal_key(sig)
            if key in self._seen:
                continue
            self._seen.add(key)
            out.append(sig)
        if len(self.series):                    # forget setups that fell out of the window
            oldest = self.series[0].ts
            self._seen = {k for k in self._seen if k[-1] >= oldest}
        return out

    def poll(self) -> Optional[LiveEvent]:
        """One cycle: fetch, and if a bar closed, scan it. None when nothing closed."""
        fresh = self.fetch()
        if not fresh:
            return None
        report = self.scan_now()
        return LiveEvent(fresh[-1], report, self._fresh_signals(report))

    def seconds_to_next_close(self) -> float:
        if self.poll_seconds:
            return max(1.0, float(self.poll_seconds))
        last = self.series[-1].ts if len(self.series) else int(self.clock())
        next_close = last + 2 * self.tf_seconds        # last closed bar + the forming one
        return max(1.0, next_close - self.clock() + self.close_delay)

    def run(self, on_event: Callable[[LiveEvent], None],
            max_polls: Optional[int] = None,
            should_stop: Optional[Callable[[], bool]] = None) -> int:
        """Poll forever (or ``max_polls`` times), calling back on each closed bar."""
        if not len(self.series):
            self.prime()
        polls = 0
        while max_polls is None or polls < max_polls:
            if should_stop and should_stop():
                break
            self.sleeper(self.seconds_to_next_close())
            polls += 1
            try:
                event = self.poll()
            except (BitgetError, OSError, RuntimeError):
                continue                               # transient: try again next cycle
            if event is not None:
                on_event(event)
        return polls
