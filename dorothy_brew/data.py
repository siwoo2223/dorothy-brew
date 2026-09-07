"""Loading OHLCV data. CSV / JSON in, :class:`Series` out."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence

from .core import Candle, Series

_TS_KEYS = ("timestamp", "time", "date", "datetime", "open_time", "ts")
_ALIASES = {
    "open": ("open", "o", "opening", "open_price"),
    "high": ("high", "h", "max"),
    "low": ("low", "l", "min"),
    "close": ("close", "c", "closing", "close_price", "last"),
    "volume": ("volume", "vol", "v", "base_volume", "quantity"),
}

TIMEFRAME_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
    "1d": 86400, "3d": 259200, "1w": 604800,
}


def parse_ts(value) -> int:
    """Epoch seconds from an int/float/ISO string, tolerating ms and us."""
    if isinstance(value, (int, float)):
        v = float(value)
    else:
        text = str(value).strip()
        if not text:
            return 0
        try:
            v = float(text)
        except ValueError:
            text = text.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                            "%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%d-%m-%Y"):
                    try:
                        dt = datetime.strptime(text, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    return 0
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
    while v > 1e12:          # ms / us epochs
        v /= 1000.0
    return int(v)


def _pick(header: Sequence[str], names: Iterable[str]) -> Optional[int]:
    low = [h.strip().lower() for h in header]
    for name in names:
        if name in low:
            return low.index(name)
    return None


def from_rows(rows: Iterable[Sequence], symbol: str = "", timeframe: str = "",
              header: Optional[Sequence[str]] = None) -> Series:
    """Build a series from raw rows; column order is detected from the header."""
    rows = list(rows)
    if header is None:
        header = ["timestamp", "open", "high", "low", "close", "volume"]
    idx = {k: _pick(header, v) for k, v in _ALIASES.items()}
    ts_i = _pick(header, _TS_KEYS)
    candles: List[Candle] = []
    for r in rows:
        if not r or all(str(x).strip() == "" for x in r):
            continue
        try:
            candles.append(Candle(
                ts=parse_ts(r[ts_i]) if ts_i is not None else 0,
                open=float(r[idx["open"]]),
                high=float(r[idx["high"]]),
                low=float(r[idx["low"]]),
                close=float(r[idx["close"]]),
                volume=float(r[idx["volume"]]) if idx["volume"] is not None
                and str(r[idx["volume"]]).strip() != "" else 0.0,
            ))
        except (TypeError, ValueError, IndexError):
            continue                      # skip malformed rows rather than abort the scan
    candles.sort(key=lambda c: c.ts)
    return Series(candles, symbol, timeframe)


def load_csv(path: str, symbol: str = "", timeframe: str = "") -> Series:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(fh, dialect)
        rows = list(reader)
    if not rows:
        return Series([], symbol, timeframe)
    header = rows[0]
    has_header = any(str(c).strip().lower() in
                     set(sum(_ALIASES.values(), ())) | set(_TS_KEYS) for c in header)
    body = rows[1:] if has_header else rows
    symbol = symbol or os.path.splitext(os.path.basename(path))[0]
    return from_rows(body, symbol, timeframe, header if has_header else None)


def load_json(path: str, symbol: str = "", timeframe: str = "") -> Series:
    """Accepts a list of lists (exchange kline format) or a list of dicts."""
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    if isinstance(payload, dict):
        for key in ("data", "result", "candles", "klines", "ohlcv"):
            if key in payload:
                payload = payload[key]
                break
    if not payload:
        return Series([], symbol, timeframe)
    if isinstance(payload[0], dict):
        header = list(payload[0].keys())
        rows = [[row.get(k) for k in header] for row in payload]
        return from_rows(rows, symbol, timeframe, header)
    return from_rows(payload, symbol, timeframe)


def load(path: str, symbol: str = "", timeframe: str = "") -> Series:
    if path.lower().endswith(".json"):
        return load_json(path, symbol, timeframe)
    return load_csv(path, symbol, timeframe)


def resample(series: Series, factor: int, timeframe: str = "") -> Series:
    """Aggregate every ``factor`` bars into one — cheap higher-timeframe view."""
    if factor <= 1:
        return series
    out: List[Candle] = []
    for i in range(0, len(series) - factor + 1, factor):
        chunk = series.candles[i:i + factor]
        out.append(Candle(ts=chunk[0].ts, open=chunk[0].open,
                          high=max(c.high for c in chunk),
                          low=min(c.low for c in chunk),
                          close=chunk[-1].close,
                          volume=sum(c.volume for c in chunk)))
    return Series(out, series.symbol, timeframe or f"{series.timeframe}x{factor}")


def write_csv(series: Series, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for c in series:
            w.writerow([c.ts, c.open, c.high, c.low, c.close, c.volume])
