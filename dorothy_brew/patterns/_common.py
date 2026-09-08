"""Shared geometry / confirmation helpers used by the detectors."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from ..core import (Candle, Pivot, Series, line_at, line_through, linreg, near,
                    volume_slope, clamp)

_MISS = object()


def last_pivots(series: Series, n: int, left: int = 3, right: int = 3) -> List[Pivot]:
    return series.pivots(left, right)[-n:]


def pivot_highs(pivots: Sequence[Pivot]) -> List[Pivot]:
    return [p for p in pivots if p.is_high]


def pivot_lows(pivots: Sequence[Pivot]) -> List[Pivot]:
    return [p for p in pivots if not p.is_high]


def fit_boundary(points: Sequence[Pivot]) -> Optional[Tuple[float, float]]:
    """Line through the first and last pivot of a boundary."""
    if len(points) < 2:
        return None
    return line_through((points[0].index, points[0].price),
                        (points[-1].index, points[-1].price))


def touch_count(series: Series, line: Tuple[float, float], i0: int, i1: int,
                kind: str, tol: float) -> int:
    """How many bars in [i0, i1] tag the line within ``tol`` price units."""
    hits = 0
    prev_hit = -99
    for i in range(max(0, i0), min(len(series), i1 + 1)):
        level = line_at(line, i)
        price = series[i].high if kind == "high" else series[i].low
        if abs(price - level) <= tol and i - prev_hit > 1:
            hits += 1
            prev_hit = i
    return hits


def closes_beyond(candle: Candle, level: float, direction: str,
                  body_only: bool = True) -> bool:
    """Full-candle close outside a level (the classic breakout filter)."""
    ref = candle.close
    if direction == "up":
        return ref > level and (not body_only or min(candle.open, candle.close) > level * 0.999)
    return ref < level and (not body_only or max(candle.open, candle.close) < level * 1.001)


def volume_expansion(series: Series, idx: int, lookback: int = 20) -> float:
    """Breakout volume relative to its recent average (1.0 = average)."""
    lo = max(0, idx - lookback)
    hist = [c.volume for c in series[lo:idx]]
    avg = sum(hist) / len(hist) if hist else 0.0
    if avg <= 0:
        return 1.0
    return series[idx].volume / avg


def volume_dryup(series: Series, i0: int, i1: int) -> float:
    """1.0 when volume is clearly drying up across [i0, i1], 0.0 when building."""
    slope = volume_slope([c.volume for c in series[i0:i1 + 1]])
    return clamp(0.5 - slope)


def impulse_leg(series: Series, end: int, min_bars: int = 4, max_bars: int = 40,
                direction: str = "up") -> Optional[Tuple[int, int, float]]:
    """Strongest contiguous leg inside the ``max_bars`` window ending at ``end``.

    Returns ``(start_index, end_index, size)`` — the low/high pair (or high/low
    for a down leg) that spans the most price. One backward pass with a running
    extreme instead of testing every window, because the backtester calls this
    on every bar.
    """
    n = len(series)
    if end < min_bars or end >= n:
        return None
    key = (end, min_bars, max_bars, direction)
    cached = series.leg_cache.get(key, _MISS)
    if cached is not _MISS:
        return cached

    lo_bound = max(0, end - max_bars)
    window = series.candles[lo_bound:end + 1]
    highs = [c.high for c in window]
    lows = [c.low for c in window]
    best: Optional[Tuple[int, int, float]] = None
    run_extreme, run_index = None, -1

    for j in range(min_bars, len(window)):
        i = j - min_bars                       # the leg needs at least min_bars
        if direction == "up":
            if run_extreme is None or lows[i] < run_extreme:
                run_extreme, run_index = lows[i], i
            size = highs[j] - run_extreme
        else:
            if run_extreme is None or highs[i] > run_extreme:
                run_extreme, run_index = highs[i], i
            size = run_extreme - lows[j]
        if best is None or size > best[2]:
            best = (lo_bound + run_index, lo_bound + j, size)

    atr = series.atr(14, end) or 1e-9
    if best is None or best[2] / atr < 2.0:
        best = None
    series.leg_cache[key] = best
    return best


def momentum_divergence(series: Series, i0: int, i1: int, kind: str) -> float:
    """RSI divergence strength between two structural extremes (0..1)."""
    if i0 >= i1 or i1 >= len(series):
        return 0.0
    r0, r1 = series.rsi(14, i0), series.rsi(14, i1)
    if kind == "bearish":          # higher price high, lower RSI
        if series[i1].high > series[i0].high and r1 < r0:
            return clamp((r0 - r1) / 15.0)
    else:                          # lower price low, higher RSI
        if series[i1].low < series[i0].low and r1 > r0:
            return clamp((r1 - r0) / 15.0)
    return 0.0


def measured_targets(entry: float, height: float, direction: str,
                     ratios: Sequence[float] = (0.618, 1.0, 1.618)) -> List[float]:
    sign = 1.0 if direction == "up" else -1.0
    return [round(entry + sign * height * r, 10) for r in ratios]


def structure_bias(series: Series, at: Optional[int] = None) -> str:
    """Swing-structure trend: HH/HL = up, LH/LL = down."""
    piv = series.pivots(3, 3)
    if at is not None:
        piv = [p for p in piv if p.index <= at]
    highs = [p for p in piv if p.is_high][-2:]
    lows = [p for p in piv if not p.is_high][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return "range"
    hh = highs[-1].price > highs[-2].price
    hl = lows[-1].price > lows[-2].price
    if hh and hl:
        return "up"
    if not hh and not hl:
        return "down"
    return "range"


def trend_alignment(series: Series, direction: str, at: Optional[int] = None) -> float:
    """1.0 when the setup trades with structure, 0.35 when against it."""
    bias = structure_bias(series, at)
    want = "up" if direction == "long" else "down"
    if bias == want:
        return 1.0
    if bias == "range":
        return 0.6
    return 0.35


def rejection_wick(candle: Candle, direction: str) -> float:
    """Wick rejection quality at a level (0..1)."""
    if candle.range <= 0:
        return 0.0
    wick = candle.lower_wick if direction == "long" else candle.upper_wick
    return clamp(wick / candle.range / 0.5)


def swept_level(series: Series, idx: int, level: float, direction: str) -> bool:
    """Wick pierced the level but the candle closed back inside."""
    c = series[idx]
    if direction == "up":
        return c.high > level and c.close < level
    return c.low < level and c.close > level


def window_bounds(series: Series, lookback: int) -> Tuple[int, int]:
    end = len(series) - 1
    return max(0, end - lookback + 1), end


def envelope_lines(series: Series, i0: int, i1: int) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Upper/lower envelope of a window, in absolute-index coordinates.

    The regression slope of the highs (resp. lows) is kept, then the line is
    shifted until every bar sits inside it — that is the drawn trendline a
    chartist would use.
    """
    highs = series.highs(i0, i1 + 1)
    lows = series.lows(i0, i1 + 1)
    sh, ih, _ = linreg(highs)
    sl, il, _ = linreg(lows)
    up_shift = max(h - (sh * k + ih) for k, h in enumerate(highs))
    lo_shift = min(l - (sl * k + il) for k, l in enumerate(lows))
    upper = (sh, ih + up_shift - sh * i0)
    lower = (sl, il + lo_shift - sl * i0)
    return upper, lower


def parallelism(upper: Tuple[float, float], lower: Tuple[float, float],
                scale: float) -> float:
    """1.0 when both boundaries share a slope, 0.0 when they diverge hard."""
    if scale <= 0:
        return 0.0
    return clamp(1.0 - abs(upper[0] - lower[0]) / scale)


def channel_width(upper: Tuple[float, float], lower: Tuple[float, float], x: float) -> float:
    return line_at(upper, x) - line_at(lower, x)


def convergence(upper: Tuple[float, float], lower: Tuple[float, float],
                i0: int, i1: int) -> float:
    """Positive when the boundaries squeeze together over the window."""
    w0 = channel_width(upper, lower, i0)
    w1 = channel_width(upper, lower, i1)
    if w0 <= 0:
        return 0.0
    return (w0 - w1) / w0


def flat_level(pivots: Sequence[Pivot], tol: float) -> Optional[float]:
    """Average level if the pivots form a horizontal band, else None."""
    if len(pivots) < 2:
        return None
    prices = [p.price for p in pivots]
    if max(prices) - min(prices) > tol:
        return None
    return sum(prices) / len(prices)


def bars_since(idx: int, series: Series) -> int:
    return len(series) - 1 - idx


def pivot_boundaries(series: Series, i0: int, i1: int, left: int = 3, right: int = 3):
    """Trendlines drawn through the swing highs and swing lows of a window.

    Unlike :func:`envelope_lines` these sit on the actual pivots, so the width
    between them reflects real swing amplitude — what wedge/triangle geometry
    needs to measure convergence.
    """
    piv = series.pivots(left, right)
    hs = [p for p in piv if i0 <= p.index <= i1 and p.is_high]
    ls = [p for p in piv if i0 <= p.index <= i1 and not p.is_high]
    if len(hs) < 2 or len(ls) < 2:
        return None
    upper, lower = fit_boundary(hs), fit_boundary(ls)
    if upper is None or lower is None:
        return None
    return upper, lower, hs, ls


def range_profile(series: Series, i0: int, i1: int, parts: int = 3) -> List[float]:
    """High-low range of each equal slice of the window."""
    span = i1 - i0 + 1
    if span < parts * 2:
        return []
    size = span // parts
    out = []
    for k in range(parts):
        a = i0 + k * size
        b = i1 if k == parts - 1 else a + size - 1
        out.append(max(series.highs(a, b + 1)) - min(series.lows(a, b + 1)))
    return out
