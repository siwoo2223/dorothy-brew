"""Core market-data primitives shared by every pattern detector.

Pure standard library on purpose: the scanner has to run inside cron jobs,
lambdas and CI images where numpy is not guaranteed to exist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Candle:
    ts: int          # epoch seconds of the bar open
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def bullish(self) -> bool:
        return self.close >= self.open

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    def body_ratio(self) -> float:
        return self.body / self.range if self.range > 0 else 0.0


@dataclass(frozen=True)
class Pivot:
    """A confirmed swing point (fractal)."""
    index: int
    price: float
    kind: str        # "high" | "low"

    @property
    def is_high(self) -> bool:
        return self.kind == "high"


class Series:
    """An OHLCV series for one symbol on one timeframe."""

    def __init__(self, candles: Sequence[Candle], symbol: str = "", timeframe: str = ""):
        self.candles: List[Candle] = list(candles)
        self.symbol = symbol
        self.timeframe = timeframe
        self._atr_cache: dict = {}
        self._tr_cache: Optional[List[float]] = None
        self._pivot_cache: dict = {}
        self.leg_cache: dict = {}   # memoised impulse legs, keyed by detector args

    # -- container protocol -------------------------------------------------
    def __len__(self) -> int:
        return len(self.candles)

    def __getitem__(self, item):
        if isinstance(item, slice):
            return Series(self.candles[item], self.symbol, self.timeframe)
        return self.candles[item]

    def __iter__(self):
        return iter(self.candles)

    # -- column views -------------------------------------------------------
    def highs(self, a: int = 0, b: Optional[int] = None) -> List[float]:
        return [c.high for c in self.candles[a:b]]

    def lows(self, a: int = 0, b: Optional[int] = None) -> List[float]:
        return [c.low for c in self.candles[a:b]]

    def closes(self, a: int = 0, b: Optional[int] = None) -> List[float]:
        return [c.close for c in self.candles[a:b]]

    def opens(self, a: int = 0, b: Optional[int] = None) -> List[float]:
        return [c.open for c in self.candles[a:b]]

    def volumes(self, a: int = 0, b: Optional[int] = None) -> List[float]:
        return [c.volume for c in self.candles[a:b]]

    @property
    def last(self) -> Candle:
        return self.candles[-1]

    # -- indicators ---------------------------------------------------------
    def true_ranges(self) -> List[float]:
        if self._tr_cache is None:
            out = [self.candles[0].range] if self.candles else []
            for prev, cur in zip(self.candles, self.candles[1:]):
                out.append(max(cur.high - cur.low,
                               abs(cur.high - prev.close),
                               abs(cur.low - prev.close)))
            self._tr_cache = out
        return self._tr_cache

    def atr(self, period: int = 14, at: Optional[int] = None) -> float:
        """Wilder-smoothed ATR evaluated at bar ``at`` (default: last bar)."""
        at = len(self.candles) - 1 if at is None else at
        key = (period, at)
        if key in self._atr_cache:
            return self._atr_cache[key]
        if at < 1:
            return self.candles[at].range if self.candles else 0.0
        tr = self.true_ranges()
        window = tr[max(1, at - period + 1): at + 1]
        value = sum(window) / len(window) if window else 0.0
        self._atr_cache[key] = value
        return value

    def sma(self, period: int, at: Optional[int] = None, field: str = "close") -> float:
        at = len(self.candles) - 1 if at is None else at
        lo = max(0, at - period + 1)
        vals = [getattr(c, field) for c in self.candles[lo: at + 1]]
        return sum(vals) / len(vals) if vals else 0.0

    def rsi(self, period: int = 14, at: Optional[int] = None) -> float:
        at = len(self.candles) - 1 if at is None else at
        if at < period:
            return 50.0
        gains = losses = 0.0
        for i in range(at - period + 1, at + 1):
            delta = self.candles[i].close - self.candles[i - 1].close
            if delta >= 0:
                gains += delta
            else:
                losses -= delta
        if losses == 0:
            return 100.0
        rs = (gains / period) / (losses / period)
        return 100.0 - 100.0 / (1.0 + rs)

    # -- structure ----------------------------------------------------------
    def pivots(self, left: int = 3, right: int = 3) -> List[Pivot]:
        """Fractal swing points. A pivot is confirmed only after ``right`` bars."""
        key = (left, right)
        if key in self._pivot_cache:
            return self._pivot_cache[key]
        out: List[Pivot] = []
        n = len(self.candles)
        for i in range(left, n - right):
            c = self.candles[i]
            window = self.candles[i - left: i + right + 1]
            if all(c.high >= o.high for o in window) and any(c.high > o.high for o in window):
                out.append(Pivot(i, c.high, "high"))
            if all(c.low <= o.low for o in window) and any(c.low < o.low for o in window):
                out.append(Pivot(i, c.low, "low"))
        out.sort(key=lambda p: p.index)
        out = _dedupe_alternating(out)
        self._pivot_cache[key] = out
        return out

    def swings(self, left: int = 3, right: int = 3) -> List[Pivot]:
        """Strictly alternating high/low sequence — the zig-zag skeleton."""
        return self.pivots(left, right)

    def trend(self, lookback: int = 50, at: Optional[int] = None) -> str:
        """Coarse regime label: ``up`` / ``down`` / ``range``."""
        at = len(self.candles) - 1 if at is None else at
        lo = max(0, at - lookback + 1)
        closes = self.closes(lo, at + 1)
        if len(closes) < 5:
            return "range"
        slope, _, r2 = linreg(closes)
        span = max(closes) - min(closes)
        if span == 0:
            return "range"
        drift = slope * len(closes) / span
        if r2 < 0.25 or abs(drift) < 0.35:
            return "range"
        return "up" if slope > 0 else "down"


def _dedupe_alternating(pivots: Sequence[Pivot]) -> List[Pivot]:
    """Collapse consecutive same-kind pivots, keeping the most extreme one."""
    out: List[Pivot] = []
    for p in pivots:
        if out and out[-1].kind == p.kind:
            keep_new = p.price > out[-1].price if p.is_high else p.price < out[-1].price
            if keep_new:
                out[-1] = p
            continue
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# geometry / math helpers
# ---------------------------------------------------------------------------

def linreg(values: Sequence[float]) -> Tuple[float, float, float]:
    """Least-squares fit over x = 0..n-1. Returns (slope, intercept, r2)."""
    n = len(values)
    if n < 2:
        return 0.0, (values[0] if values else 0.0), 0.0
    xs = range(n)
    mx = (n - 1) / 2.0
    my = sum(values) / n
    sxy = sum((x - mx) * (v - my) for x, v in zip(xs, values))
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my, 0.0
    slope = sxy / sxx
    intercept = my - slope * mx
    syy = sum((v - my) ** 2 for v in values)
    r2 = (sxy ** 2) / (sxx * syy) if syy > 0 else 0.0
    return slope, intercept, r2


def line_through(p1: Tuple[float, float], p2: Tuple[float, float]) -> Tuple[float, float]:
    """Slope/intercept of the line through two (x, y) points."""
    (x1, y1), (x2, y2) = p1, p2
    if x2 == x1:
        return 0.0, y1
    slope = (y2 - y1) / (x2 - x1)
    return slope, y1 - slope * x1


def line_at(line: Tuple[float, float], x: float) -> float:
    slope, intercept = line
    return slope * x + intercept


def curvature(values: Sequence[float]) -> float:
    """Quadratic coefficient of a y = ax^2 + bx + c fit (a > 0 => U shape)."""
    n = len(values)
    if n < 3:
        return 0.0
    xs = [float(i) for i in range(n)]
    # normalise x to keep the normal equations well conditioned
    mx = sum(xs) / n
    xs = [x - mx for x in xs]
    s0, s1, s2 = float(n), sum(xs), sum(x * x for x in xs)
    s3, s4 = sum(x ** 3 for x in xs), sum(x ** 4 for x in xs)
    t0 = sum(values)
    t1 = sum(x * y for x, y in zip(xs, values))
    t2 = sum(x * x * y for x, y in zip(xs, values))
    # solve the 3x3 system by Cramer's rule
    m = [[s0, s1, s2], [s1, s2, s3], [s2, s3, s4]]
    det = _det3(m)
    if abs(det) < 1e-12:
        return 0.0
    ma = [[s0, s1, t0], [s1, s2, t1], [s2, s3, t2]]
    return _det3(ma) / det


def _det3(m: Sequence[Sequence[float]]) -> float:
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def pct_diff(a: float, b: float) -> float:
    """Relative difference between two prices, on the larger magnitude."""
    denom = max(abs(a), abs(b))
    return abs(a - b) / denom if denom else 0.0


def near(a: float, b: float, tol: float) -> bool:
    return pct_diff(a, b) <= tol


def in_range(value: float, lo: float, hi: float, tol: float = 0.0) -> bool:
    return (lo - abs(lo) * tol) <= value <= (hi + abs(hi) * tol)


def ratio_score(value: float, lo: float, hi: float, tol: float) -> float:
    """1.0 inside [lo, hi], decaying to 0 across the tolerance band."""
    if lo <= value <= hi:
        return 1.0
    slack = max(abs(lo), abs(hi)) * tol
    if slack <= 0:
        return 0.0
    dist = (lo - value) if value < lo else (value - hi)
    return max(0.0, 1.0 - dist / slack)


def fib_level(start: float, end: float, ratio: float) -> float:
    """Retracement/extension level of the ``start -> end`` leg."""
    return end - (end - start) * ratio


def volume_slope(volumes: Sequence[float]) -> float:
    """Normalised volume drift: negative = drying up, positive = building."""
    vals = [v for v in volumes]
    if len(vals) < 3 or sum(vals) == 0:
        return 0.0
    slope, _, _ = linreg(vals)
    mean = sum(vals) / len(vals)
    return slope * len(vals) / mean if mean else 0.0


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def blend(*parts: Tuple[float, float]) -> float:
    """Weighted average of (score, weight) pairs, clamped to [0, 1]."""
    total_w = sum(w for _, w in parts)
    if total_w <= 0:
        return 0.0
    return clamp(sum(s * w for s, w in parts) / total_w)


def highest(candles: Sequence[Candle]) -> float:
    return max(c.high for c in candles)


def lowest(candles: Sequence[Candle]) -> float:
    return min(c.low for c in candles)


def body_engulfs(prev: Candle, cur: Candle) -> bool:
    return (min(cur.open, cur.close) <= min(prev.open, prev.close)
            and max(cur.open, cur.close) >= max(prev.open, prev.close)
            and cur.bullish != prev.bullish)


def rr(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    return abs(target - entry) / risk if risk > 0 else 0.0


def iter_windows(n: int, size: int, step: int = 1) -> Iterable[Tuple[int, int]]:
    for start in range(0, max(0, n - size + 1), step):
        yield start, start + size


def is_finite(*vals: float) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)
