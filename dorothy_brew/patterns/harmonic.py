"""Harmonic & Fibonacci systems (playbook 3/3) — precision ratios.

The XABCD engine walks the zig-zag skeleton and scores each candidate against
the ratio table of every harmonic; a pattern only fires when price is actually
inside the Potential Reversal Zone.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from ..config import ScanConfig
from ..core import Pivot, Series, blend, clamp, fib_level, line_at, ratio_score
from ..registry import (ABCD, BAT, BUTTERFLY, CRAB, DEEP_CRAB, FIB_CLUSTER,
                        FIB_EXTENSION, FIB_TREND_FAN, GOLDEN_RETRACEMENT, SHARK,
                        register)
from ..signals import CONFIRMED, FORMING, LONG, RETEST, SHORT, Signal, Zone, make_signal
from ._common import (impulse_leg, pivot_highs, pivot_lows, rejection_wick,
                      structure_bias, trend_alignment, volume_expansion)
from .classic import _top

# ratio windows: (low, high) per leg. ``ad`` is the D-point completion on XA.
RATIO_TABLES: Dict[str, Dict[str, Tuple[float, float]]] = {
    "bat":       {"ab": (0.382, 0.500), "bc": (0.382, 0.886), "cd": (1.618, 2.618), "ad": (0.886, 0.886)},
    "butterfly": {"ab": (0.786, 0.786), "bc": (0.382, 0.886), "cd": (1.618, 2.240), "ad": (1.272, 1.414)},
    "crab":      {"ab": (0.382, 0.618), "bc": (0.382, 0.886), "cd": (2.240, 3.618), "ad": (1.618, 1.618)},
    "deep_crab": {"ab": (0.886, 0.886), "bc": (0.382, 0.886), "cd": (2.000, 3.618), "ad": (1.618, 1.618)},
    # shark is labelled O-X-A-B-C in the literature; mapped onto XABCD the
    # rules become BC = 1.13-1.618 of AB, CD = 1.618-2.24 of BC and the D
    # completion = 0.886-1.13 of the XA leg.
    "shark":     {"ab": (0.300, 1.200), "bc": (1.130, 1.618), "cd": (1.618, 2.240), "ad": (0.886, 1.130)},
}


def _quintets(series: Series, cfg: ScanConfig) -> List[Tuple[Pivot, ...]]:
    """Every alternating 5-pivot sequence whose D point is still actionable."""
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)
    end = len(series) - 1
    out = []
    for i in range(len(piv) - 5, -1, -1):
        seq = tuple(piv[i:i + 5])
        if seq[0].index < end - cfg.lookback:
            break
        kinds = [p.is_high for p in seq]
        if any(kinds[j] == kinds[j + 1] for j in range(4)):
            continue
        if end - seq[-1].index > cfg.lookback // 3:
            continue
        out.append(seq)
    return out


def _legs(seq: Sequence[Pivot]) -> Optional[Dict[str, float]]:
    x, a, b, c, d = [p.price for p in seq]
    xa, ab, bc, cd, ad = abs(a - x), abs(b - a), abs(c - b), abs(d - c), abs(d - a)
    if min(xa, ab, bc, cd) <= 0:
        return None
    return {"ab": ab / xa, "bc": bc / ab, "cd": cd / bc, "ad": ad / xa,
            "xa": xa, "ab_abs": ab, "bc_abs": bc, "cd_abs": cd}


def _match(name: str, legs: Dict[str, float], cfg: ScanConfig) -> Dict[str, float]:
    table = RATIO_TABLES[name]
    return {key: ratio_score(legs[key], lo, hi, cfg.ratio_tol * 3)
            for key, (lo, hi) in table.items()}


def _harmonic_signal(spec, name: str, series: Series, cfg: ScanConfig,
                     checks_map: Dict[str, str]) -> List[Signal]:
    """Shared body for the five XABCD harmonics."""
    out: List[Signal] = []
    end = len(series) - 1
    atr = series.atr(14)
    for seq in _quintets(series, cfg):
        legs = _legs(seq)
        if not legs:
            continue
        scores = _match(name, legs, cfg)
        if min(scores.values()) <= 0:
            continue
        x, a, b, c, d = seq
        bullish = not d.is_high                      # D at a low -> long the PRZ
        direction = LONG if bullish else SHORT

        prz_lo, prz_hi = (d.price - atr * 0.5, d.price + atr * 0.5)
        in_prz = 1.0 if prz_lo <= series.last.close <= prz_hi else \
            clamp(1.0 - abs(series.last.close - d.price) / max(atr * 2, 1e-9))
        reaction = max(rejection_wick(series[d.index], direction),
                       rejection_wick(series.last, direction))
        exhaustion = clamp(volume_expansion(series, d.index, 20) / 1.4)

        status = CONFIRMED if (in_prz > 0.5 and reaction > 0.35) else FORMING
        entry = series.last.close if status == CONFIRMED else d.price
        stop = (min(d.price, prz_lo) - atr * 0.6) if bullish else (max(d.price, prz_hi) + atr * 0.6)
        cd_leg = abs(d.price - c.price)
        t1 = fib_level(c.price, d.price, 0.382)
        t2 = fib_level(c.price, d.price, 0.618)
        t3 = b.price
        precision = sum(scores.values()) / len(scores)
        conf = blend((precision, 4), (in_prz, 2), (reaction, 2), (exhaustion, 1),
                     (trend_alignment(series, direction, d.index), 1))

        keys = list(checks_map.items())
        out.append(make_signal(
            spec.id, series, direction=direction, status=status,
            start_index=x.index, end_index=end, entry=entry, stop=stop,
            targets=[t1, t2, t3], confidence=conf,
            checks={keys[0][0]: scores.get(keys[0][1], precision),
                    keys[1][0]: in_prz,
                    keys[2][0]: reaction,
                    keys[3][0]: exhaustion},
            zones=[Zone(prz_lo, prz_hi, "PRZ")],
            notes=(f"XABCD {x.price:.6g}/{a.price:.6g}/{b.price:.6g}/{c.price:.6g}/{d.price:.6g} "
                   f"AB={legs['ab']:.3f} BC={legs['bc']:.3f} CD={legs['cd']:.3f} AD={legs['ad']:.3f}"),
            meta={"ratios": {k: round(legs[k], 4) for k in ("ab", "bc", "cd", "ad")},
                  "ratio_scores": {k: round(v, 3) for k, v in scores.items()},
                  "points": {n: p.price for n, p in zip("XABCD", seq)},
                  "point_bars": {n: p.index for n, p in zip("XABCD", seq)},
                  "cd_leg": cd_leg}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 1. Golden Retracement — the 61.8% pullback
# ---------------------------------------------------------------------------

@register(GOLDEN_RETRACEMENT)
def golden_retracement(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    n = len(series)
    if n < 30:
        return out
    end = n - 1
    atr = series.atr(14)
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)
    if len(piv) < 2:
        return out

    for k in range(len(piv) - 2, max(-1, len(piv) - 8), -1):
        p0, p1 = piv[k], piv[k + 1]
        if p0.is_high == p1.is_high:
            continue
        up = (not p0.is_high) and p1.is_high
        leg = abs(p1.price - p0.price)
        if leg < atr * 3:
            continue
        direction = LONG if up else SHORT
        z_lo = fib_level(p0.price, p1.price, 0.65)
        z_hi = fib_level(p0.price, p1.price, 0.618)
        lo, hi = min(z_lo, z_hi), max(z_lo, z_hi)
        lo, hi = lo - atr * 0.25, hi + atr * 0.25

        touch = None
        for j in range(p1.index + 1, end + 1):
            probe = series[j].low if up else series[j].high
            if lo <= probe <= hi:
                touch = j
            broke = (series[j].close < fib_level(p0.price, p1.price, 0.9)) if up else \
                    (series[j].close > fib_level(p0.price, p1.price, 0.9))
            if broke:
                touch = None
                break
        if touch is None or end - touch > cfg.fresh_bars:
            continue

        zone_q = 1.0
        rejection = rejection_wick(series[touch], direction)
        confluence = 0.0
        for q in piv[:k]:
            if lo <= q.price <= hi:
                confluence = 1.0
        status = CONFIRMED if rejection > 0.35 else FORMING
        entry = series[touch].close if status == CONFIRMED else (hi if up else lo)
        stop = (fib_level(p0.price, p1.price, 0.9)) if up else fib_level(p0.price, p1.price, 0.9)
        ext = fib_level(p0.price, p1.price, -0.618)     # 1.618 extension of the leg
        conf = blend((zone_q, 2), (confluence, 2), (rejection, 3),
                     (trend_alignment(series, direction, touch), 2))

        out.append(make_signal(
            GOLDEN_RETRACEMENT.id, series, direction=direction, status=status,
            start_index=p0.index, end_index=end, entry=entry, stop=stop,
            targets=[p1.price, ext], confidence=conf,
            checks={"Fib 0.618 / 0.65 zone": zone_q,
                    "Confluence with S/R": confluence,
                    "Candlestick rejection": rejection,
                    "Target 1.618 extension": 1.0},
            zones=[Zone(lo, hi, "0.618-0.65 golden zone")],
            notes=f"{'up' if up else 'down'} leg {p0.price:.6g}->{p1.price:.6g} "
                  f"retraced into {lo:.6g}-{hi:.6g}",
            meta={"leg": leg, "golden_zone": [lo, hi], "extension_1618": ext}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 2-4, 7, 9. The XABCD family
# ---------------------------------------------------------------------------

@register(BAT)
def bat(series: Series, cfg: ScanConfig) -> List[Signal]:
    return _harmonic_signal(BAT, "bat", series, cfg, {
        "Precise 0.886 XA completion": "ad",
        "Tight PRZ alignment": "prz",
        "Low risk high R:R": "reaction",
        "Target 0.382 CD wave": "volume"})


@register(BUTTERFLY)
def butterfly(series: Series, cfg: ScanConfig) -> List[Signal]:
    return _harmonic_signal(BUTTERFLY, "butterfly", series, cfg, {
        "D point extension 1.272-1.414": "ad",
        "Deep XA extension": "prz",
        "Structural reversal zone": "reaction",
        "Target B point level": "volume"})


@register(CRAB)
def crab(series: Series, cfg: ScanConfig) -> List[Signal]:
    return _harmonic_signal(CRAB, "crab", series, cfg, {
        "D point 1.618 extension": "ad",
        "High-volume exhaustion": "prz",
        "Tight stop above D": "reaction",
        "Aggressive mean-reversion": "volume"})


@register(DEEP_CRAB)
def deep_crab(series: Series, cfg: ScanConfig) -> List[Signal]:
    return _harmonic_signal(DEEP_CRAB, "deep_crab", series, cfg, {
        "Gartley 0.786 PRZ": "ab",
        "Market structure confluence": "prz",
        "Clear stop beyond X": "reaction",
        "High-probability bounce": "volume"})


@register(SHARK)
def shark(series: Series, cfg: ScanConfig) -> List[Signal]:
    return _harmonic_signal(SHARK, "shark", series, cfg, {
        "Failed breakout trigger": "ab",
        "Rapid PRZ response": "prz",
        "Strong 50% Fib target": "reaction",
        "High velocity scalp": "volume"})


# ---------------------------------------------------------------------------
# 5. AB=CD — symmetry in price and time
# ---------------------------------------------------------------------------

@register(ABCD)
def abcd(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)
    if len(piv) < 4 or len(series) < 25:
        return out
    end = len(series) - 1
    atr = series.atr(14)

    for i in range(len(piv) - 4, -1, -1):
        a, b, c, d = piv[i:i + 4]
        if a.index < end - cfg.lookback:
            break
        kinds = [p.is_high for p in (a, b, c, d)]
        if any(kinds[j] == kinds[j + 1] for j in range(3)):
            continue
        ab, bc, cd = abs(b.price - a.price), abs(c.price - b.price), abs(d.price - c.price)
        if min(ab, bc, cd) <= 0 or ab < atr * 2:
            continue
        price_sym = ratio_score(cd / ab, 0.95, 1.05, cfg.ratio_tol * 4)
        bc_ret = ratio_score(bc / ab, 0.618, 0.786, cfg.ratio_tol * 3)
        t_ab, t_cd = b.index - a.index, d.index - c.index
        time_sym = ratio_score(t_cd / max(1, t_ab), 0.7, 1.4, 0.5)
        if price_sym <= 0 or bc_ret <= 0:
            continue

        bullish = not d.is_high
        direction = LONG if bullish else SHORT
        reaction = max(rejection_wick(series[d.index], direction),
                       rejection_wick(series.last, direction))
        status = CONFIRMED if reaction > 0.35 and end - d.index <= cfg.lookback // 4 else FORMING
        entry = series.last.close if status == CONFIRMED else d.price
        stop = (d.price - atr * 0.7) if bullish else (d.price + atr * 0.7)
        conf = blend((price_sym, 3), (bc_ret, 2), (time_sym, 2), (reaction, 2))

        out.append(make_signal(
            ABCD.id, series, direction=direction, status=status,
            start_index=a.index, end_index=end, entry=entry, stop=stop,
            targets=[fib_level(c.price, d.price, 0.382),
                     fib_level(c.price, d.price, 0.618), b.price],
            confidence=conf,
            checks={"1:1 time & price confluence": time_sym,
                    "BC 0.618/0.786 retracement": bc_ret,
                    "Symmetric leg completion": price_sym,
                    "Clean structural entry": reaction},
            zones=[Zone(min(d.price, entry) - atr * 0.4, max(d.price, entry) + atr * 0.4, "D completion")],
            notes=f"AB={ab:.4g} CD={cd:.4g} (ratio {cd / ab:.3f}), BC retrace {bc / ab:.3f}",
            meta={"cd_ab": cd / ab, "bc_ab": bc / ab, "time_ratio": t_cd / max(1, t_ab)}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 6. Fibonacci Extension — the 1.618 profit-lock / exhaustion zone
# ---------------------------------------------------------------------------

@register(FIB_EXTENSION)
def fib_extension(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)
    if len(piv) < 3 or len(series) < 25:
        return out
    end = len(series) - 1
    atr = series.atr(14)

    for i in range(len(piv) - 3, max(-1, len(piv) - 8), -1):
        p0, p1, p2 = piv[i:i + 3]
        if p0.is_high == p1.is_high or p1.is_high == p2.is_high:
            continue
        up = (not p0.is_high) and p1.is_high
        leg = abs(p1.price - p0.price)
        if leg < atr * 3:
            continue
        ext_1618 = p2.price + (leg * 1.618 if up else -leg * 1.618)
        ext_1000 = p2.price + (leg if up else -leg)
        price = series.last.close
        reached = clamp(1.0 - abs(price - ext_1618) / max(atr * 2, 1e-9))
        if reached <= 0.2:
            continue

        direction = SHORT if up else LONG          # fade / lock in at the extension
        overbought = clamp(abs(series.rsi(14) - 50) / 25.0)
        exhaust = rejection_wick(series.last, direction)
        status = CONFIRMED if exhaust > 0.4 else FORMING
        entry = price
        stop = (max(series.highs(end - 3, end + 1)) + atr * 0.5) if up \
            else (min(series.lows(end - 3, end + 1)) - atr * 0.5)
        conf = blend((reached, 3), (overbought, 2), (exhaust, 3))

        out.append(make_signal(
            FIB_EXTENSION.id, series, direction=direction, status=status,
            start_index=p0.index, end_index=end, entry=entry, stop=stop,
            targets=[ext_1000, p2.price], confidence=conf,
            checks={"Trend expansion target": reached,
                    "Over-bought boundary": overbought,
                    "Exhaustion candle test": exhaust,
                    "Profit lock-in zone": 1.0},
            zones=[Zone(min(ext_1618 - atr * 0.5, ext_1618 + atr * 0.5),
                        max(ext_1618 - atr * 0.5, ext_1618 + atr * 0.5), "1.618 extension")],
            notes=f"price {price:.6g} at the 1.618 extension {ext_1618:.6g} — "
                  f"scale out / fade, not a fresh trend entry",
            meta={"ext_1000": ext_1000, "ext_1618": ext_1618, "leg": leg}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 8. Fib Cluster Zone — where levels from several swings overlap
# ---------------------------------------------------------------------------

@register(FIB_CLUSTER)
def fib_cluster(series: Series, cfg: ScanConfig) -> List[Signal]:
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)
    if len(piv) < 4 or len(series) < 30:
        return []
    end = len(series) - 1
    atr = series.atr(14)
    tol = max(cfg.tol(series), atr * 0.4)

    levels: List[Tuple[float, int]] = []
    swings = 0
    for a, b in zip(piv[-8:], piv[-7:]):
        if a.is_high == b.is_high:
            continue
        if abs(b.price - a.price) < atr * 2:
            continue
        swings += 1
        for r in (0.382, 0.5, 0.618, 0.786):
            levels.append((fib_level(a.price, b.price, r), swings))
    if len(levels) < 4:
        return []

    levels.sort(key=lambda t: t[0])
    best: Optional[Tuple[float, float, int, int]] = None
    for i, (lv, _) in enumerate(levels):
        group = [(p, s) for p, s in levels if abs(p - lv) <= tol]
        sources = len({s for _, s in group})
        if len(group) < 3 or sources < 2:
            continue
        lo, hi = min(p for p, _ in group), max(p for p, _ in group)
        if best is None or (len(group), sources) > (best[2], best[3]):
            best = (lo, hi, len(group), sources)
    if best is None:
        return []

    lo, hi, count, sources = best
    price = series.last.close
    mid = (lo + hi) / 2
    direction = LONG if price >= mid else SHORT
    if structure_bias(series) == "up":
        direction = LONG
    elif structure_bias(series) == "down":
        direction = SHORT

    distance = clamp(1.0 - abs(price - mid) / max(atr * 3, 1e-9))
    conviction = clamp(count / 5.0)
    barrier = clamp(sources / 3.0)
    status = CONFIRMED if lo - atr * 0.5 <= price <= hi + atr * 0.5 else FORMING
    entry = price if status == CONFIRMED else mid
    stop = (lo - atr * 0.8) if direction == LONG else (hi + atr * 0.8)
    span = abs(entry - stop)
    conf = blend((conviction, 3), (barrier, 3), (distance, 2))

    sig = make_signal(
        FIB_CLUSTER.id, series, direction=direction, status=status,
        start_index=piv[-8].index if len(piv) >= 8 else piv[0].index, end_index=end,
        entry=entry, stop=stop,
        targets=[entry + (span * 2 if direction == LONG else -span * 2),
                 entry + (span * 3 if direction == LONG else -span * 3)],
        confidence=conf,
        checks={"Multi-wave confluence": conviction,
                "High-conviction node": distance,
                "Institutional barrier": barrier,
                "Position sizing boost": conviction},
        zones=[Zone(lo, hi, "fib cluster")],
        notes=f"{count} fib levels from {sources} swings cluster in {lo:.6g}-{hi:.6g}",
        meta={"levels_in_cluster": count, "distinct_swings": sources})
    return _top([sig], cfg)


# ---------------------------------------------------------------------------
# 10. Fib Trend Fan — dynamic angular support
# ---------------------------------------------------------------------------

@register(FIB_TREND_FAN)
def fib_trend_fan(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)
    if len(piv) < 2 or len(series) < 40:
        return out
    end = len(series) - 1
    atr = series.atr(14)
    tol = max(cfg.tol(series), atr * 0.5)

    for a, b in zip(piv[-6:], piv[-5:]):
        if a.is_high == b.is_high:
            continue
        up = (not a.is_high) and b.is_high
        leg = abs(b.price - a.price)
        span = b.index - a.index
        if leg < atr * 4 or span < 8:
            continue
        base_slope = (b.price - a.price) / span
        fans = {r: (base_slope * (1 - r), a.price - base_slope * (1 - r) * a.index)
                for r in (0.382, 0.5, 0.618)}

        held_ratio, held_line, touches = None, None, 0
        for r, line in fans.items():
            hits = 0
            for j in range(b.index, end + 1):
                lvl = line_at(line, j)
                probe = series[j].low if up else series[j].high
                if abs(probe - lvl) <= tol and (series[j].close > lvl if up else series[j].close < lvl):
                    hits += 1
            if hits > touches:
                held_ratio, held_line, touches = r, line, hits
        if held_line is None or touches < 2:
            continue

        level_now = line_at(held_line, end)
        price = series.last.close
        on_fan = clamp(1.0 - abs(price - level_now) / max(tol * 2, 1e-9))
        direction = LONG if up else SHORT
        accel = clamp(abs(base_slope) * span / max(leg, 1e-9))
        status = CONFIRMED if on_fan > 0.4 and (price > level_now if up else price < level_now) \
            else FORMING
        entry = price if status == CONFIRMED else level_now
        stop = (level_now - atr) if up else (level_now + atr)
        conf = blend((clamp(touches / 3.0), 3), (on_fan, 3), (accel, 1),
                     (trend_alignment(series, direction), 2))

        out.append(make_signal(
            FIB_TREND_FAN.id, series, direction=direction, status=status,
            start_index=a.index, end_index=end, entry=entry, stop=stop,
            targets=[b.price + (leg * 0.618 if up else -leg * 0.618),
                     b.price + (leg if up else -leg)],
            confidence=conf,
            checks={"Geometric trendline": clamp(touches / 3.0),
                    "Dynamic angle support": on_fan,
                    "Trend acceleration": accel,
                    "Trailing stop anchor": 1.0},
            zones=[Zone(level_now - tol, level_now + tol, f"{held_ratio} fan line")],
            notes=f"{held_ratio} fan from {a.price:.6g} held {touches} times, "
                  f"now at {level_now:.6g}",
            meta={"fan_ratio": held_ratio, "fan_level": level_now, "touches": touches,
                  "trail_stop": stop}))
    return _top(out, cfg)
