"""Smart-money concepts (playbook 2/3) — liquidity, structure, order flow."""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..config import ScanConfig
from ..core import Series, blend, body_engulfs, clamp, pct_diff
from ..registry import (BOS, CHOCH, EQUAL_HIGHS_LOWS, FAIR_VALUE_GAP,
                        INSTITUTIONAL_ENGULFING, LIQUIDITY_COMPRESSION,
                        LIQUIDITY_SWEEP, ORDER_BLOCK, PREMIUM_DISCOUNT,
                        SUPPLY_DEMAND_FLIP, register)
from ..signals import CONFIRMED, FORMING, LONG, RETEST, SHORT, Signal, Zone, make_signal
from ._common import (pivot_highs, pivot_lows, rejection_wick, structure_bias,
                      trend_alignment, volume_expansion, volume_slope)
from .classic import _top


def _dealing_range(series: Series, cfg: ScanConfig) -> Optional[Tuple[float, float, int, int]]:
    """The most recent swing high / swing low that define the working range."""
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)
    highs = pivot_highs(piv)
    lows = pivot_lows(piv)
    if not highs or not lows:
        return None
    h, l = highs[-1], lows[-1]
    return h.price, l.price, h.index, l.index


# ---------------------------------------------------------------------------
# 1. Liquidity Sweep — take the wick, reclaim, fade
# ---------------------------------------------------------------------------

@register(LIQUIDITY_SWEEP)
def liquidity_sweep(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    n = len(series)
    if n < 20:
        return out
    end = n - 1
    atr = series.atr(14)
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)

    for i in range(max(1, end - cfg.fresh_bars * 3), end + 1):
        c = series[i]
        prior_highs = [p for p in pivot_highs(piv) if p.index < i - 1]
        prior_lows = [p for p in pivot_lows(piv) if p.index < i - 1]

        for level_p, side in ((prior_highs[-1] if prior_highs else None, "high"),
                              (prior_lows[-1] if prior_lows else None, "low")):
            if level_p is None or i - level_p.index > cfg.lookback:
                continue
            level = level_p.price
            if side == "high":
                pierced = c.high > level and c.close < level
                direction, wick = SHORT, c.upper_wick
            else:
                pierced = c.low < level and c.close > level
                direction, wick = LONG, c.lower_wick
            if not pierced:
                continue

            sweep_depth = clamp((abs(c.high - level) if side == "high"
                                 else abs(level - c.low)) / max(atr * 0.6, 1e-9))
            reclaim = clamp(wick / max(c.range, 1e-9) / 0.5)
            # quick reclaim: how fast price got back on the right side of the level
            speed = clamp(1.0 - (end - i) / max(cfg.fresh_bars * 3, 1))
            opposite = ([p.price for p in prior_lows] or [c.low])[-1] if side == "high" \
                else ([p.price for p in prior_highs] or [c.high])[-1]

            # the entry is the reclaim candle itself — chasing price several
            # bars later turns a 1R idea into a 3R stop
            entry = series[min(i + 1, end)].close
            stop = (c.high + atr * 0.25) if side == "high" else (c.low - atr * 0.25)
            conf = blend((sweep_depth, 2), (reclaim, 3), (speed, 2),
                         (trend_alignment(series, direction, i), 2))
            out.append(make_signal(
                LIQUIDITY_SWEEP.id, series, direction=direction, status=CONFIRMED,
                start_index=level_p.index, end_index=end, entry=entry, stop=stop,
                targets=[(entry + opposite) / 2, opposite],
                confidence=conf,
                checks={"Sweep high/low points": sweep_depth,
                        "Quick reclaim (entry)": reclaim,
                        "Stop below/above wick": 1.0,
                        "Liquidity zones": speed},
                zones=[Zone(min(level, c.low if side == "low" else level),
                            max(level, c.high if side == "high" else level),
                            f"swept {side}")],
                notes=f"swept prior {side} {level:.6g} and closed back inside",
                meta={"level": level, "side": side, "sweep_bar": i}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 2. Change of Character — the first structural break against the trend
# ---------------------------------------------------------------------------

@register(CHOCH)
def choch(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)
    if len(piv) < 4 or len(series) < 30:
        return out
    end = len(series) - 1
    atr = series.atr(14)

    highs, lows = pivot_highs(piv), pivot_lows(piv)
    if len(highs) < 2 or len(lows) < 2:
        return out
    bias_before = structure_bias(series, piv[-1].index)

    if bias_before == "up":
        pivot = lows[-1]
        direction, brk_dir = SHORT, "down"
    elif bias_before == "down":
        pivot = highs[-1]
        direction, brk_dir = LONG, "up"
    else:
        return out

    brk = None
    for i in range(pivot.index + 1, end + 1):
        c = series[i]
        if brk_dir == "down" and c.close < pivot.price:
            brk = i
            break
        if brk_dir == "up" and c.close > pivot.price:
            brk = i
            break
    if brk is None or end - brk > cfg.lookback // 3:
        return out

    impulse = clamp(abs(series[brk].close - pivot.price) / max(atr, 1e-9))
    strength = clamp(series[brk].body / max(series[brk].range, 1e-9))

    # after the break, the retest zone is the FVG / order block left behind
    zone_lo, zone_hi = _last_gap_zone(series, brk, brk_dir)
    retested = 0.0
    status, entry = CONFIRMED, series[brk].close
    for j in range(brk + 1, end + 1):
        if zone_lo <= series[j].high and series[j].low <= zone_hi:
            retested, status, entry = 1.0, RETEST, series[j].close
    stop = (max(series.highs(brk - 3, brk + 1)) + atr * 0.4) if direction == SHORT \
        else (min(series.lows(brk - 3, brk + 1)) - atr * 0.4)
    liquidity_target = (lows[-2].price if direction == SHORT else highs[-2].price)
    conf = blend((impulse, 3), (strength, 2), (retested, 2), (0.8, 1))

    out.append(make_signal(
        CHOCH.id, series, direction=direction, status=status,
        start_index=pivot.index, end_index=end, entry=entry, stop=stop,
        targets=[(entry + liquidity_target) / 2, liquidity_target],
        confidence=conf,
        checks={"Key pivot break": impulse,
                "FVG/OB retest": retested,
                "Trend direction": 1.0 if bias_before != "range" else 0.5,
                "Hold to liquidity zone": strength},
        zones=[Zone(zone_lo, zone_hi, "CHoCH retest zone")],
        notes=f"{bias_before} structure broke at {pivot.price:.6g} -> CHoCH {direction}",
        meta={"prior_bias": bias_before, "break_bar": brk}))
    return _top(out, cfg)


def _last_gap_zone(series: Series, brk: int, brk_dir: str) -> Tuple[float, float]:
    """Imbalance left by the breaking impulse; falls back to the break candle."""
    for i in range(brk, max(1, brk - 6), -1):
        if i + 1 >= len(series) or i - 1 < 0:
            continue
        a, c = series[i - 1], series[i + 1]
        if brk_dir == "up" and c.low > a.high:
            return a.high, c.low
        if brk_dir == "down" and c.high < a.low:
            return c.high, a.low
    c = series[brk]
    return min(c.open, c.close), max(c.open, c.close)


# ---------------------------------------------------------------------------
# 3. Order Block — last opposite candle before the displacement
# ---------------------------------------------------------------------------

@register(ORDER_BLOCK)
def order_block(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    n = len(series)
    if n < 25:
        return out
    end = n - 1
    atr = series.atr(14)
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)
    highs, lows = pivot_highs(piv), pivot_lows(piv)

    start = max(3, end - cfg.lookback)
    for i in range(start, end + 1):
        c = series[i]
        if c.body < atr * 1.3:                      # need displacement, not noise
            continue
        direction = LONG if c.bullish else SHORT
        # structure taken out by the displacement
        if direction == LONG:
            refs = [p for p in highs if p.index < i]
            if not refs or c.close <= refs[-1].price:
                continue
        else:
            refs = [p for p in lows if p.index < i]
            if not refs or c.close >= refs[-1].price:
                continue

        ob_idx = None
        for j in range(i - 1, max(0, i - 6), -1):
            if series[j].bullish != c.bullish:
                ob_idx = j
                break
        if ob_idx is None:
            continue
        ob = series[ob_idx]
        zone_lo, zone_hi = min(ob.open, ob.close, ob.low), max(ob.open, ob.close, ob.high)

        # first touch after the displacement is the tradable rebound
        touch, invalidated = None, False
        for j in range(i + 1, end + 1):
            if direction == LONG and series[j].close < zone_lo:
                invalidated = True
                break
            if direction == SHORT and series[j].close > zone_hi:
                invalidated = True
                break
            if series[j].low <= zone_hi and series[j].high >= zone_lo:
                touch = j
                break
        if invalidated:
            continue
        if touch is not None and end - touch > cfg.fresh_bars:
            continue

        displacement = clamp(c.body / (atr * 2))
        htf = trend_alignment(series, direction, i)
        first_touch = 1.0 if touch is not None else 0.4
        status = RETEST if touch is not None else FORMING
        entry = series[touch].close if touch is not None else (zone_hi if direction == LONG else zone_lo)
        stop = (zone_lo - atr * 0.3) if direction == LONG else (zone_hi + atr * 0.3)
        target_ref = (max(series.highs(i, end + 1)) if direction == LONG
                      else min(series.lows(i, end + 1)))
        ext = abs(target_ref - entry)
        conf = blend((displacement, 3), (first_touch, 2), (htf, 3), (0.7, 1))

        out.append(make_signal(
            ORDER_BLOCK.id, series, direction=direction, status=status,
            start_index=ob_idx, end_index=end, entry=entry, stop=stop,
            targets=[target_ref, entry + (ext * 1.5 if direction == LONG else -ext * 1.5)],
            confidence=conf,
            checks={"Last opposite candle": 1.0,
                    "First touch = rebound": first_touch,
                    "Close below = stop": 1.0,
                    "Follow higher time trend": htf},
            zones=[Zone(zone_lo, zone_hi, f"{'bullish' if direction == LONG else 'bearish'} OB")],
            notes=f"OB at bar {ob_idx} ({zone_lo:.6g}-{zone_hi:.6g}) before "
                  f"{c.body / atr:.1f}x ATR displacement",
            meta={"ob_index": ob_idx, "displacement_atr": c.body / atr}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 4. Fair Value Gap — 3-candle imbalance, 50% (CE) entry
# ---------------------------------------------------------------------------

@register(FAIR_VALUE_GAP)
def fair_value_gap(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    n = len(series)
    if n < 10:
        return out
    end = n - 1
    atr = series.atr(14)

    for i in range(max(1, end - cfg.lookback), end):
        a, b, c = series[i - 1], series[i], series[i + 1]
        bullish = c.low > a.high
        bearish = c.high < a.low
        if not (bullish or bearish):
            continue
        zone_lo, zone_hi = (a.high, c.low) if bullish else (c.high, a.low)
        size = zone_hi - zone_lo
        if size < atr * 0.25:
            continue
        direction = LONG if bullish else SHORT
        ce = (zone_lo + zone_hi) / 2

        filled, touched = False, None
        for j in range(i + 2, end + 1):
            if bullish and series[j].low <= zone_lo:
                filled = True
                break
            if bearish and series[j].high >= zone_hi:
                filled = True
                break
            if (bullish and series[j].low <= ce) or (bearish and series[j].high >= ce):
                touched = j
                break
        if filled:
            continue
        if touched is not None and end - touched > cfg.fresh_bars:
            continue

        gap_q = clamp(size / (atr * 0.8))
        htf = trend_alignment(series, direction, i)
        ce_entry = 1.0 if touched is not None else 0.4
        status = RETEST if touched is not None else FORMING
        entry = ce
        stop = (zone_lo - atr * 0.3) if bullish else (zone_hi + atr * 0.3)
        prior = (max(series.highs(max(0, i - 30), i)) if bullish
                 else min(series.lows(max(0, i - 30), i)))
        target = max(prior, entry + size * 2) if bullish else min(prior, entry - size * 2)
        conf = blend((gap_q, 3), (ce_entry, 2), (htf, 3))

        out.append(make_signal(
            FAIR_VALUE_GAP.id, series, direction=direction, status=status,
            start_index=i - 1, end_index=end, entry=entry, stop=stop,
            targets=[entry + (size * 1.5 if bullish else -size * 1.5), target],
            confidence=conf,
            checks={"3-candle gap": gap_q,
                    "50% fill entry (CE)": ce_entry,
                    "Target previous high/low": 1.0,
                    "Follow trend direction": htf},
            zones=[Zone(zone_lo, zone_hi, "FVG")],
            notes=f"{'bullish' if bullish else 'bearish'} FVG {zone_lo:.6g}-{zone_hi:.6g}, "
                  f"CE {ce:.6g}",
            meta={"ce": ce, "gap_size": size, "gap_bar": i}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 5. Supply/Demand Flip — broken resistance retested as support
# ---------------------------------------------------------------------------

@register(SUPPLY_DEMAND_FLIP)
def supply_demand_flip(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)
    if len(piv) < 3 or len(series) < 25:
        return out
    end = len(series) - 1
    atr = series.atr(14)
    tol = cfg.tol(series)

    for p in piv[-8:]:
        level = p.price
        flip_dir = LONG if p.is_high else SHORT     # break a high -> it becomes support
        brk = None
        for i in range(p.index + 2, end + 1):
            c = series[i]
            if p.is_high and c.close > level + tol * 0.5:
                brk = i
                break
            if (not p.is_high) and c.close < level - tol * 0.5:
                brk = i
                break
        if brk is None:
            continue

        retest, held = None, 0.0
        for j in range(brk + 1, end + 1):
            c = series[j]
            if p.is_high and c.low <= level + tol and c.close > level:
                retest, held = j, 1.0
            elif p.is_high and c.close < level - tol:
                retest = None
                break
            if (not p.is_high) and c.high >= level - tol and c.close < level:
                retest, held = j, 1.0
            elif (not p.is_high) and c.close > level + tol:
                retest = None
                break
        if retest is None or end - retest > cfg.fresh_bars:
            continue

        flip_q = clamp(abs(series[brk].close - level) / max(atr, 1e-9))
        htf = trend_alignment(series, flip_dir, retest)
        entry = series[retest].close
        stop = (level - atr * 0.6) if flip_dir == LONG else (level + atr * 0.6)
        swing = abs(series[brk].close - p.price) + atr
        conf = blend((flip_q, 3), (held, 3), (htf, 2))

        out.append(make_signal(
            SUPPLY_DEMAND_FLIP.id, series, direction=flip_dir, status=RETEST,
            start_index=p.index, end_index=end, entry=entry, stop=stop,
            targets=[entry + (swing if flip_dir == LONG else -swing),
                     entry + (swing * 1.618 if flip_dir == LONG else -swing * 1.618)],
            confidence=conf,
            checks={"Zone flip confirmation": flip_q,
                    "Retest entry": held,
                    "Follow trend": htf,
                    "Invalidation stop": 1.0},
            zones=[Zone(level - tol, level + tol,
                        "resistance->support" if flip_dir == LONG else "support->resistance")],
            notes=f"{'resistance' if p.is_high else 'support'} {level:.6g} flipped and held",
            meta={"level": level, "break_bar": brk, "retest_bar": retest}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 6. Break of Structure — trend continuation with volume
# ---------------------------------------------------------------------------

@register(BOS)
def bos(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)
    if len(piv) < 3 or len(series) < 25:
        return out
    end = len(series) - 1
    atr = series.atr(14)
    highs, lows = pivot_highs(piv), pivot_lows(piv)
    bias = structure_bias(series)

    # the level that was broken is, by definition, no longer the newest pivot —
    # so walk back a few swings and keep every recent break
    candidates = []
    if bias in ("up", "range"):
        candidates += [(p, LONG, "up") for p in highs[-3:]]
    if bias in ("down", "range"):
        candidates += [(p, SHORT, "down") for p in lows[-3:]]

    for pivot, direction, brk_dir in candidates:
        brk = None
        for i in range(pivot.index + 1, end + 1):
            c = series[i]
            if brk_dir == "up" and c.close > pivot.price:
                brk = i
                break
            if brk_dir == "down" and c.close < pivot.price:
                brk = i
                break
        if brk is None or end - brk > cfg.lookback // 4:
            continue

        c = series[brk]
        body_q = clamp(c.body / max(c.range, 1e-9) / 0.6)
        vol = volume_expansion(series, brk, 20)
        vol_q = clamp(vol / cfg.min_volume_ratio)
        cont = 1.0 if bias == ("up" if direction == LONG else "down") else 0.6

        pull_idx, pull_q = None, 0.4
        for j in range(brk + 1, end + 1):
            probe = series[j].low if direction == LONG else series[j].high
            if abs(probe - pivot.price) <= cfg.tol(series):
                pull_idx, pull_q = j, 1.0
        status = RETEST if pull_idx is not None else CONFIRMED
        entry = series[pull_idx].close if pull_idx is not None else series[brk].close
        leg = abs(pivot.price - (min(series.lows(pivot.index, brk + 1)) if direction == LONG
                                 else max(series.highs(pivot.index, brk + 1))))
        stop = (pivot.price - max(leg * 0.5, atr)) if direction == LONG \
            else (pivot.price + max(leg * 0.5, atr))
        conf = blend((body_q, 2), (vol_q, 2), (cont, 3), (pull_q, 2))

        out.append(make_signal(
            BOS.id, series, direction=direction, status=status,
            start_index=pivot.index, end_index=end, entry=entry, stop=stop,
            targets=[entry + (leg if direction == LONG else -leg),
                     entry + (leg * 1.618 if direction == LONG else -leg * 1.618)],
            confidence=conf,
            checks={"Strong candle close": body_q,
                    "Volume confirmation": vol_q,
                    "Trend continuation": cont,
                    "Pullback entry": pull_q},
            notes=f"BOS through {pivot.price:.6g} on bar {brk} ({vol:.1f}x volume)",
            meta={"pivot": pivot.price, "break_bar": brk, "volume_ratio": vol}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 7. Premium & Discount — only buy the discount half of the dealing range
# ---------------------------------------------------------------------------

@register(PREMIUM_DISCOUNT)
def premium_discount(series: Series, cfg: ScanConfig) -> List[Signal]:
    dr = _dealing_range(series, cfg)
    if dr is None or len(series) < 25:
        return []
    high, low, hi_i, lo_i = dr
    if high <= low:
        return []
    end = len(series) - 1
    atr = series.atr(14)
    price = series.last.close
    pos = (price - low) / (high - low)
    mid = (high + low) / 2
    bias = structure_bias(series)

    if pos <= 0.4 and bias != "down":
        direction, zone, zone_q = LONG, Zone(low, mid, "discount"), clamp((0.5 - pos) / 0.35)
    elif pos >= 0.6 and bias != "up":
        direction, zone, zone_q = SHORT, Zone(mid, high, "premium"), clamp((pos - 0.5) / 0.35)
    else:
        return []

    htf = trend_alignment(series, direction)
    reaction = rejection_wick(series.last, direction)
    entry = price
    stop = (low - atr * 0.5) if direction == LONG else (high + atr * 0.5)
    conf = blend((zone_q, 3), (htf, 3), (reaction, 1), (0.7, 1))

    sig = make_signal(
        PREMIUM_DISCOUNT.id, series, direction=direction,
        status=CONFIRMED if reaction > 0.4 else FORMING,
        start_index=min(hi_i, lo_i), end_index=end, entry=entry, stop=stop,
        targets=[mid, high if direction == LONG else low],
        confidence=conf,
        checks={"Trade discount zone": zone_q if direction == LONG else 0.0,
                "Trade premium zone": zone_q if direction == SHORT else 0.0,
                "Midline (0.5)": 1.0,
                "Follow trend": htf},
        zones=[zone],
        notes=f"price at {pos:.0%} of the {low:.6g}-{high:.6g} dealing range",
        meta={"range_position": pos, "midline": mid})
    return _top([sig], cfg)


# ---------------------------------------------------------------------------
# 8. Equal Highs / Lows — the liquidity pool and its false breakout
# ---------------------------------------------------------------------------

@register(EQUAL_HIGHS_LOWS)
def equal_highs_lows(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)
    if len(piv) < 3 or len(series) < 25:
        return out
    end = len(series) - 1
    atr = series.atr(14)

    for pts, side in ((pivot_highs(piv), "high"), (pivot_lows(piv), "low")):
        if len(pts) < 2:
            continue
        a, b = pts[-2], pts[-1]
        if pct_diff(a.price, b.price) > cfg.equal_tol * 3:
            continue
        level = (a.price + b.price) / 2
        pool = clamp(1.0 - pct_diff(a.price, b.price) / (cfg.equal_tol * 3))

        swept, reversal = None, 0.0
        for j in range(b.index + 1, end + 1):
            c = series[j]
            if side == "high" and c.high > level and c.close < level:
                swept, reversal = j, 1.0
            if side == "low" and c.low < level and c.close > level:
                swept, reversal = j, 1.0
        if swept is not None and end - swept > cfg.fresh_bars:
            continue

        if swept is not None:
            direction = SHORT if side == "high" else LONG
            status, entry = CONFIRMED, series[end].close
            stop = (series[swept].high + atr * 0.3) if side == "high" \
                else (series[swept].low - atr * 0.3)
            opp = (min(series.lows(a.index, end + 1)) if side == "high"
                   else max(series.highs(a.index, end + 1)))
            targets = [(entry + opp) / 2, opp]
        else:
            # untouched pool: price is still hunting that liquidity
            direction = LONG if side == "high" else SHORT
            status, entry = FORMING, series.last.close
            stop = (min(series.lows(b.index, end + 1)) - atr * 0.4) if side == "high" \
                else (max(series.highs(b.index, end + 1)) + atr * 0.4)
            targets = [level]

        htf = trend_alignment(series, direction)
        conf = blend((pool, 3), (reversal, 2), (htf, 2), (0.6, 1))
        out.append(make_signal(
            EQUAL_HIGHS_LOWS.id, series, direction=direction, status=status,
            start_index=a.index, end_index=end, entry=entry, stop=stop,
            targets=targets, confidence=conf,
            checks={"Liquidity pool": pool,
                    "False breakout": reversal,
                    "Reversal entry": reversal,
                    "Trend direction": htf},
            zones=[Zone(min(a.price, b.price), max(a.price, b.price), f"equal {side}s")],
            notes=f"equal {side}s at {level:.6g}" +
                  (f", swept on bar {swept}" if swept is not None else ", pool untouched"),
            meta={"level": level, "side": side, "swept_bar": swept}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 9. Institutional Engulfing — a large engulfing body at a key level
# ---------------------------------------------------------------------------

@register(INSTITUTIONAL_ENGULFING)
def institutional_engulfing(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    n = len(series)
    if n < 20:
        return out
    end = n - 1
    atr = series.atr(14)
    tol = cfg.tol(series)
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)

    for i in range(max(1, end - cfg.fresh_bars * 2), end + 1):
        c, p = series[i], series[i - 1]
        if not body_engulfs(p, c) or c.body < atr:
            continue
        direction = LONG if c.bullish else SHORT
        body_q = clamp(c.body / (atr * 1.6))
        wick = rejection_wick(c, direction)

        levels = [q.price for q in piv if q.index < i - 1]
        at_level = 0.0
        nearest = None
        for lv in levels[-10:]:
            probe = c.low if direction == LONG else c.high
            if abs(probe - lv) <= tol * 1.5:
                at_level = 1.0
                nearest = lv
        htf = trend_alignment(series, direction, i)
        entry = c.close
        stop = (c.low - atr * 0.2) if direction == LONG else (c.high + atr * 0.2)
        risk = abs(entry - stop)
        conf = blend((body_q, 3), (wick, 2), (at_level, 2), (htf, 2))

        out.append(make_signal(
            INSTITUTIONAL_ENGULFING.id, series, direction=direction, status=CONFIRMED,
            start_index=i - 1, end_index=end, entry=entry, stop=stop,
            targets=[entry + (risk * 2 if direction == LONG else -risk * 2),
                     entry + (risk * 3 if direction == LONG else -risk * 3)],
            confidence=conf,
            checks={"Strong body": body_q, "Rejection wick": wick,
                    "Key level": at_level, "Follow trend": htf},
            notes=f"{c.body / atr:.1f}x ATR engulfing bar" +
                  (f" at level {nearest:.6g}" if nearest else ""),
            meta={"body_atr": c.body / atr, "level": nearest}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 10. Liquidity Compression — squeeze then expansion
# ---------------------------------------------------------------------------

@register(LIQUIDITY_COMPRESSION)
def liquidity_compression(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    n = len(series)
    if n < 40:
        return out
    end = n - 1

    for win in (10, 15, 20, 30):
        i0 = end - win + 1
        if i0 < 20:
            continue
        rng = max(series.highs(i0, end + 1)) - min(series.lows(i0, end + 1))
        ref_atr = series.atr(14, i0 - 1)
        if ref_atr <= 0:
            continue
        tight = clamp(1.0 - (rng / (ref_atr * win * 0.45)))
        if tight < 0.35:
            continue
        vol_build = clamp(volume_slope(series.volumes(i0, end + 1)) / 0.6)

        hi = max(series.highs(i0, end + 1))
        lo = min(series.lows(i0, end + 1))
        c = series.last
        if c.close > hi - (hi - lo) * 0.15:
            direction, status, break_q = LONG, CONFIRMED, 1.0
        elif c.close < lo + (hi - lo) * 0.15:
            direction, status, break_q = SHORT, CONFIRMED, 1.0
        else:
            direction = LONG if structure_bias(series) != "down" else SHORT
            status, break_q = FORMING, 0.35

        entry = c.close if status == CONFIRMED else (hi if direction == LONG else lo)
        stop = (lo - ref_atr * 0.3) if direction == LONG else (hi + ref_atr * 0.3)
        height = max(hi - lo, ref_atr * 1.5)
        htf = trend_alignment(series, direction)
        conf = blend((tight, 3), (vol_build, 2), (break_q, 2), (htf, 1))

        out.append(make_signal(
            LIQUIDITY_COMPRESSION.id, series, direction=direction, status=status,
            start_index=i0, end_index=end, entry=entry, stop=stop,
            targets=[entry + (height if direction == LONG else -height),
                     entry + (height * 2 if direction == LONG else -height * 2)],
            confidence=conf,
            checks={"Tight range": tight, "Volume build-up": vol_build,
                    "Breakout direction": break_q, "Fade / reversal": htf},
            zones=[Zone(lo, hi, "compression")],
            notes=f"{win}-bar squeeze, range {rng:.4g} vs ATR {ref_atr:.4g}",
            meta={"window": win, "range": rng, "atr_ref": ref_atr}))
    return _top(out, cfg)
