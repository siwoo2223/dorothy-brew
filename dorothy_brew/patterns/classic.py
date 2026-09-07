"""Classic chart patterns (playbook 1/3) — trade the structure, not the noise.

Every detector scores the exact key-focus checklist printed on the playbook
card, and the blended score becomes the signal confidence.
"""

from __future__ import annotations

from typing import List, Optional

from ..config import ScanConfig
from ..core import Series, blend, clamp, curvature, line_at, linreg, pct_diff
from ..registry import (ASCENDING_TRIANGLE, BROADENING_WEDGE, BULL_FLAG, CUP_HANDLE,
                        DIAMOND_TOP, DOUBLE_BOTTOM, ENDING_WEDGE, HEAD_SHOULDERS,
                        RECTANGLE_BREAKOUT, TRIPLE_TOUCH_CHANNEL, register)
from ..signals import CONFIRMED, FORMING, LONG, RETEST, SHORT, Signal, Zone, make_signal
from ._common import (channel_width, closes_beyond, convergence, envelope_lines,
                      flat_level, impulse_leg, measured_targets, momentum_divergence,
                      parallelism, pivot_boundaries, pivot_highs, pivot_lows,
                      range_profile, rejection_wick, structure_bias, touch_count,
                      trend_alignment, volume_dryup, volume_expansion)


def _top(cands: List[Signal], cfg: ScanConfig) -> List[Signal]:
    """Keep the strongest, de-duplicated candidates for one pattern."""
    seen = {}
    for s in sorted(cands, key=lambda x: -x.confidence):
        key = (s.direction, s.status, s.end_index)
        if key not in seen:
            seen[key] = s
    out = sorted(seen.values(), key=lambda x: -x.score())
    return [s for s in out if s.confidence >= cfg.min_confidence][:cfg.max_signals_per_pattern]


def _breakout_index(series: Series, i0: int, i1: int, line, direction: str,
                    cfg: ScanConfig) -> Optional[int]:
    """First bar in the window that closes fully outside the boundary."""
    for i in range(i0, i1 + 1):
        if closes_beyond(series[i], line_at(line, i), direction):
            return i
    return None


# ---------------------------------------------------------------------------
# 1. Bull Flag — bullish continuation
# ---------------------------------------------------------------------------

@register(BULL_FLAG)
def bull_flag(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    n = len(series)
    end = n - 1
    if n < 30 or series.atr(14) <= 0:
        return out
    # the flag may already have broken out a few bars ago, so let its right
    # edge slide back over the freshness window
    for flag_end in range(end, max(20, end - cfg.fresh_bars) - 1, -1):
        for flag_len in range(5, 31):
            sig = _bull_flag_candidate(series, cfg, flag_end, flag_len)
            if sig is not None:
                out.append(sig)
    return _top(out, cfg)


def _bull_flag_candidate(series: Series, cfg: ScanConfig, flag_end: int,
                         flag_len: int) -> Optional[Signal]:
    end = len(series) - 1
    atr = series.atr(14)
    f0 = flag_end - flag_len + 1
    if f0 < 12:
        return None
    pole = impulse_leg(series, f0 - 1, 4, 40, "up")
    if not pole:
        return None
    p_start, p_end, p_size = pole
    if p_size < 2.5 * atr or p_end >= f0:
        return None

    upper, lower = envelope_lines(series, f0, flag_end)
    flag_low = min(series.lows(f0, flag_end + 1))
    retrace = (series[p_end].high - flag_low) / p_size
    if retrace > 0.55:                          # deeper than half the pole -> not a flag
        return None
    if upper[0] * flag_len / p_size > 0.25:     # the flag must drift down or sideways
        return None

    brk = _breakout_index(series, flag_end + 1, end, upper, "up", cfg)
    if brk is None and flag_end != end:
        return None                             # only the live flag may stay "forming"

    pole_strength = clamp(p_size / (atr * 6.0))
    vol_dry = volume_dryup(series, f0, flag_end)
    shallow = clamp(1.0 - retrace / 0.55)
    channel = parallelism(upper, lower, max(1e-9, atr / max(1, flag_len)) * 3)

    level = line_at(upper, brk if brk is not None else end)
    vol_ratio = volume_expansion(series, brk, 20) if brk is not None else 0.0
    vol_ok = clamp(vol_ratio / cfg.min_volume_ratio) if brk is not None else 0.4
    if brk is not None and cfg.require_volume and vol_ratio < cfg.min_volume_ratio:
        return None

    status = CONFIRMED if brk is not None else FORMING
    entry = series[brk].close if brk is not None else level
    stop = min(flag_low, entry - atr * 0.8)
    conf = blend((pole_strength, 3), (vol_dry, 2), (shallow, 2), (channel, 1),
                 (vol_ok, 2), (trend_alignment(series, LONG), 2))

    return make_signal(
        BULL_FLAG.id, series, direction=LONG, status=status,
        start_index=p_start, end_index=end, entry=entry, stop=stop,
        targets=measured_targets(entry, p_size, "up", (0.618, 1.0, 1.618)),
        confidence=conf,
        checks={"Strong pole momentum": pole_strength,
                "Falling volume in flag": vol_dry,
                "Breakout with volume": vol_ok,
                "Measured move target": shallow},
        zones=[Zone(min(flag_low, level - atr), level, "flag channel")],
        notes=f"pole {p_size:.4g} over {p_end - p_start} bars, {flag_len}-bar flag, "
              f"retrace {retrace:.0%}",
        meta={"pole_size": p_size, "flag_len": flag_len, "retrace": retrace})

# ---------------------------------------------------------------------------
# 2. Head & Shoulders (and the inverse) — reversal
# ---------------------------------------------------------------------------

@register(HEAD_SHOULDERS)
def head_shoulders(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)
    if len(piv) < 5 or len(series) < 40:
        return out
    end = len(series) - 1
    atr = series.atr(14)
    tol = cfg.tol(series)

    for i in range(len(piv) - 5, -1, -1):
        seq = piv[i:i + 5]
        if seq[0].index < end - cfg.lookback:
            break
        kinds = "".join("H" if p.is_high else "L" for p in seq)
        if kinds == "HLHLH":
            ls, t1, head, t2, rs = seq
            bearish = True
        elif kinds == "LHLHL":
            ls, t1, head, t2, rs = seq
            bearish = False
        else:
            continue

        if bearish:
            if not (head.price > ls.price and head.price > rs.price):
                continue
        else:
            if not (head.price < ls.price and head.price < rs.price):
                continue

        symmetry = clamp(1.0 - pct_diff(ls.price, rs.price) / 0.06)
        head_prom = clamp(abs(head.price - max(ls.price, rs.price) if bearish
                              else min(ls.price, rs.price) - head.price) / (atr * 1.5))
        neckline = (t2.price - t1.price) / max(1, (t2.index - t1.index)), 0.0
        neckline = (neckline[0], t1.price - neckline[0] * t1.index)

        head_vol = volume_expansion(series, head.index, 20)
        head_vol_ok = clamp(head_vol / 1.3)
        rs_lo, rs_hi = max(0, rs.index - 3), min(end, rs.index + 3)
        shoulder_vol = volume_expansion(series, rs.index, 20)
        rs_fade = clamp(1.4 - shoulder_vol)

        direction = SHORT if bearish else LONG
        brk_dir = "down" if bearish else "up"
        brk = _breakout_index(series, rs.index, end, neckline, brk_dir, cfg)
        if brk is None:
            status, trigger_i = FORMING, end
        elif end - brk > cfg.lookback // 4:
            continue
        else:
            status, trigger_i = CONFIRMED, brk
            # neckline retest: price came back to the broken line afterwards
            for j in range(brk + 1, end + 1):
                lvl = line_at(neckline, j)
                if abs((series[j].high if bearish else series[j].low) - lvl) <= tol:
                    status, trigger_i = RETEST, j

        level = line_at(neckline, trigger_i)
        height = abs(head.price - line_at(neckline, head.index))
        entry = series[trigger_i].close if status != FORMING else level
        stop = (rs.price + atr * 0.5) if bearish else (rs.price - atr * 0.5)
        conf = blend((head_vol_ok, 2), (rs_fade, 2), (symmetry, 3), (head_prom, 2),
                     (1.0 if status == RETEST else 0.7, 1))

        out.append(make_signal(
            HEAD_SHOULDERS.id, series, direction=direction, status=status,
            start_index=ls.index, end_index=end, entry=entry, stop=stop,
            targets=measured_targets(entry, height, brk_dir, (0.5, 1.0, 1.618)),
            confidence=conf,
            checks={"High-volume head": head_vol_ok,
                    "Decreasing right shoulder": rs_fade,
                    "Neckline retest entry": 1.0 if status == RETEST else 0.5,
                    "Structural reversal": symmetry},
            notes=("head & shoulders" if bearish else "inverse head & shoulders")
                  + f", shoulders within {pct_diff(ls.price, rs.price):.1%}",
            meta={"inverse": not bearish, "head_index": head.index,
                  "neckline_slope": neckline[0]}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 3. Ascending Triangle — bullish continuation
# ---------------------------------------------------------------------------

@register(ASCENDING_TRIANGLE)
def ascending_triangle(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    n = len(series)
    if n < 30:
        return out
    end = n - 1
    atr = series.atr(14)
    tol = cfg.tol(series)
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)

    for win in (30, 45, 60, 90):
        i0 = end - win + 1
        if i0 < 5:
            continue
        highs = [p for p in pivot_highs(piv) if p.index >= i0]
        lows = [p for p in pivot_lows(piv) if p.index >= i0]
        if len(highs) < 2 or len(lows) < 2:
            continue

        resistance = flat_level(highs[-3:], tol * 1.2)
        if resistance is None:
            continue
        rising = all(b.price > a.price for a, b in zip(lows[-3:], lows[-2:]))
        if not rising:
            continue

        low_line = (lambda pts: ((pts[-1].price - pts[0].price) / max(1, pts[-1].index - pts[0].index)))(lows[-3:] if len(lows) >= 3 else lows[-2:])
        base = lows[-1]
        lower = (low_line, base.price - low_line * base.index)
        res_line = (0.0, resistance)

        higher_lows = clamp((lows[-1].price - lows[0].price) / max(atr, 1e-9) / 2.0)
        touches = touch_count(series, res_line, i0, end, "high", tol)
        liquidity = clamp(touches / 3.0)
        squeeze = clamp(convergence(res_line, lower, i0, end) / 0.5)

        brk = _breakout_index(series, max(i0, lows[-1].index), end, res_line, "up", cfg)
        if brk is not None and end - brk > cfg.fresh_bars:
            continue
        vol_ratio = volume_expansion(series, brk, 20) if brk is not None else 0.0
        expansion = clamp(vol_ratio / cfg.min_volume_ratio) if brk is not None else 0.4
        if brk is not None and cfg.require_volume and vol_ratio < cfg.min_volume_ratio:
            continue

        status = CONFIRMED if brk is not None else FORMING
        entry = series[brk].close if brk is not None else resistance
        height = resistance - min(series.lows(i0, end + 1))
        stop = max(line_at(lower, end) - atr * 0.5, entry - height * 0.6)
        conf = blend((higher_lows, 3), (liquidity, 2), (expansion, 2), (squeeze, 1),
                     (trend_alignment(series, LONG), 2))

        out.append(make_signal(
            ASCENDING_TRIANGLE.id, series, direction=LONG, status=status,
            start_index=max(i0, lows[0].index), end_index=end, entry=entry, stop=stop,
            targets=measured_targets(entry, height, "up", (0.618, 1.0, 1.618)),
            confidence=conf,
            checks={"Continuous higher lows": higher_lows,
                    "Horizontal liquidity test": liquidity,
                    "Clean close above resistance": expansion if brk else 0.0,
                    "Expansion phase": squeeze},
            zones=[Zone(resistance - tol, resistance + tol, "horizontal resistance")],
            notes=f"{touches} touches of {resistance:.6g} with rising lows",
            meta={"resistance": resistance, "touches": touches}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 4. Rectangle Breakout — range expansion
# ---------------------------------------------------------------------------

@register(RECTANGLE_BREAKOUT)
def rectangle_breakout(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    n = len(series)
    if n < 25:
        return out
    end = n - 1
    atr = series.atr(14)
    tol = cfg.tol(series)
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)

    for win in (20, 30, 45, 60):
        i0 = end - win + 1
        if i0 < 3:
            continue
        highs = [p for p in pivot_highs(piv) if p.index >= i0][-3:]
        lows = [p for p in pivot_lows(piv) if p.index >= i0][-3:]
        if len(highs) < 2 or len(lows) < 2:
            continue
        res = flat_level(highs, tol * 1.3)
        sup = flat_level(lows, tol * 1.3)
        if res is None or sup is None or res <= sup:
            continue

        width = res - sup
        if width < atr * 1.2:
            continue
        res_line, sup_line = (0.0, res), (0.0, sup)
        rejections = touch_count(series, res_line, i0, end, "high", tol) + \
            touch_count(series, sup_line, i0, end, "low", tol)
        boundary = clamp(rejections / 4.0)

        early_atr = series.atr(14, i0 + max(3, win // 4))
        contraction = clamp((early_atr - atr) / max(early_atr, 1e-9) / 0.3)

        up_brk = _breakout_index(series, i0 + 2, end, res_line, "up", cfg)
        dn_brk = _breakout_index(series, i0 + 2, end, sup_line, "down", cfg)
        # keep the most recent side that actually broke
        brk, direction, brk_dir, level = None, None, None, None
        if up_brk is not None and (dn_brk is None or up_brk > dn_brk):
            brk, direction, brk_dir, level = up_brk, LONG, "up", res
        elif dn_brk is not None:
            brk, direction, brk_dir, level = dn_brk, SHORT, "down", sup

        if brk is not None and end - brk > cfg.fresh_bars:
            brk = None
        if brk is None:
            if not cfg.include_forming:
                continue
            direction = LONG if series.last.close > (res + sup) / 2 else SHORT
            brk_dir = "up" if direction == LONG else "down"
            level = res if direction == LONG else sup
            status, entry, close_out = FORMING, level, 0.3
        else:
            status, entry, close_out = CONFIRMED, series[brk].close, 1.0

        stop = (sup + atr * 0.2) if direction == LONG else (res - atr * 0.2)
        conf = blend((boundary, 3), (contraction, 2), (close_out, 2),
                     (clamp(width / (atr * 3)), 1))

        out.append(make_signal(
            RECTANGLE_BREAKOUT.id, series, direction=direction, status=status,
            start_index=i0, end_index=end, entry=entry, stop=stop,
            targets=measured_targets(entry, width, brk_dir, (0.5, 1.0, 1.5)),
            confidence=conf,
            checks={"Range boundary rejection": boundary,
                    "Volatility contraction": contraction,
                    "Full candle close outside": close_out,
                    "Target opposite width": clamp(width / (atr * 3))},
            zones=[Zone(sup, res, "range")],
            notes=f"range {sup:.6g}-{res:.6g}, width {width:.4g} ({rejections} rejections)",
            meta={"support": sup, "resistance": res, "width": width}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 5. Cup and Handle — bullish continuation
# ---------------------------------------------------------------------------

@register(CUP_HANDLE)
def cup_handle(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    n = len(series)
    if n < 45:
        return out
    end = n - 1
    atr = series.atr(14)

    for handle_len in range(4, 21):
        h0 = end - handle_len + 1
        for cup_len in (30, 40, 55, 70, 90):
            c0 = h0 - cup_len
            if c0 < 0:
                continue
            cup = series[c0:h0]
            if len(cup) < 20:
                continue
            closes = cup.closes()
            curv = curvature(closes)
            left_rim = max(cup.highs(0, max(2, len(cup) // 5)))
            right_rim = max(cup.highs(len(cup) - max(2, len(cup) // 5), len(cup)))
            cup_low = min(cup.lows())
            depth = min(left_rim, right_rim) - cup_low
            if depth <= atr * 2 or curv <= 0:
                continue
            rim = min(left_rim, right_rim)
            rim_sym = clamp(1.0 - pct_diff(left_rim, right_rim) / 0.08)
            # a real cup is round, not a V: the low sits in the middle third
            low_pos = cup.lows().index(cup_low) / max(1, len(cup) - 1)
            roundness = clamp(1.0 - abs(low_pos - 0.5) / 0.35) * clamp(curv * len(closes) ** 2 / depth)

            handle = series[h0:end + 1]
            handle_low = min(handle.lows())
            pullback = (rim - handle_low) / depth
            if pullback > 0.4 or handle_low < cup_low:
                continue
            shallow = clamp(1.0 - pullback / 0.4)
            dry = volume_dryup(series, h0, end)

            res_line = (0.0, rim)
            brk = _breakout_index(series, h0, end, res_line, "up", cfg)
            if brk is not None and end - brk > cfg.fresh_bars:
                continue
            status = CONFIRMED if brk is not None else FORMING
            entry = series[brk].close if brk is not None else rim
            stop = handle_low - atr * 0.4
            conf = blend((roundness, 3), (shallow, 2), (dry, 2), (rim_sym, 2))

            out.append(make_signal(
                CUP_HANDLE.id, series, direction=LONG, status=status,
                start_index=c0, end_index=end, entry=entry, stop=stop,
                targets=measured_targets(entry, depth, "up", (0.618, 1.0, 1.618)),
                confidence=conf,
                checks={"Smooth rounded bottom": roundness,
                        "Shallow handle pullback": shallow,
                        "Volume dry-up in handle": dry,
                        "Macro breakout signal": rim_sym},
                zones=[Zone(handle_low, rim, "handle")],
                notes=f"cup {cup_len} bars deep {depth:.4g}, handle {handle_len} bars "
                      f"({pullback:.0%} of cup)",
                meta={"cup_len": cup_len, "handle_len": handle_len, "depth": depth}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 6. Ending Wedge (rising = bearish, falling = bullish)
# ---------------------------------------------------------------------------

@register(ENDING_WEDGE)
def ending_wedge(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    n = len(series)
    if n < 30:
        return out
    end = n - 1
    atr = series.atr(14)

    for win in (25, 40, 55, 80):
        i0 = end - win + 1
        if i0 < 2:
            continue
        geo = pivot_boundaries(series, i0, end, cfg.pivot_left, cfg.pivot_right)
        if geo is None:
            continue
        upper, lower, hs, ls = geo
        x0, x1 = min(hs[0].index, ls[0].index), max(hs[-1].index, ls[-1].index)
        conv = convergence(upper, lower, x0, x1)
        if conv < 0.3 or channel_width(upper, lower, x1) <= 0:
            continue                          # boundaries must actually squeeze
        rising = upper[0] > 0 and lower[0] > 0
        falling = upper[0] < 0 and lower[0] < 0
        if not (rising or falling):
            continue

        direction = SHORT if rising else LONG
        brk_dir = "down" if rising else "up"
        boundary = lower if rising else upper
        if rising:
            ext_i = max(range(i0, end + 1), key=lambda i: series[i].high)
        else:
            ext_i = min(range(i0, end + 1), key=lambda i: series[i].low)
        div = max(momentum_divergence(series, i0 + 2, ext_i,
                                      "bearish" if rising else "bullish"),
                  momentum_divergence(series, i0 + win // 3, end,
                                      "bearish" if rising else "bullish"))

        brk = _breakout_index(series, hs[-1].index if rising else ls[-1].index,
                              end, boundary, brk_dir, cfg)
        if brk is not None and end - brk > cfg.fresh_bars + 2:
            continue
        status = CONFIRMED if brk is not None else FORMING
        trigger_i = brk if brk is not None else end
        entry = series[trigger_i].close if brk is not None else line_at(boundary, end)
        height = max(series.highs(i0, end + 1)) - min(series.lows(i0, end + 1))
        stop = (max(series.highs(max(i0, end - 5), end + 1)) + atr * 0.3) if rising \
            else (min(series.lows(max(i0, end - 5), end + 1)) - atr * 0.3)
        counter = clamp(series[trigger_i].body / max(atr, 1e-9))
        conf = blend((div, 2), (clamp(conv / 0.6), 3), (counter, 2),
                     (1.0 if brk is not None else 0.4, 2))

        out.append(make_signal(
            ENDING_WEDGE.id, series, direction=direction, status=status,
            start_index=x0, end_index=end, entry=entry, stop=stop,
            targets=measured_targets(entry, height, brk_dir, (0.5, 1.0, 1.272)),
            confidence=conf,
            checks={"Bearish/Bullish divergence": div,
                    "Trendline breakdown": 1.0 if brk is not None else 0.3,
                    "Sharp counter-strike": counter,
                    "Reversal target base": clamp(conv / 0.6)},
            notes=f"{'rising' if rising else 'falling'} wedge, "
                  f"{conv:.0%} convergence over {win} bars",
            meta={"rising": rising, "convergence": conv}))
    return _top(out, cfg)

# ---------------------------------------------------------------------------
# 7. Double Bottom (and double top) — reversal
# ---------------------------------------------------------------------------

@register(DOUBLE_BOTTOM)
def double_bottom(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)
    if len(piv) < 3 or len(series) < 25:
        return out
    end = len(series) - 1
    atr = series.atr(14)
    tol = cfg.tol(series)

    for i in range(len(piv) - 3, -1, -1):
        a, mid, b = piv[i:i + 3]
        if a.index < end - cfg.lookback:
            break
        bottom = not a.is_high
        if bottom and not (not a.is_high and mid.is_high and not b.is_high):
            continue
        if not bottom and not (a.is_high and not mid.is_high and b.is_high):
            continue
        if abs(a.price - b.price) > tol * 1.5:
            continue

        neckline = mid.price
        height = abs(neckline - (a.price + b.price) / 2)
        if height < atr * 1.5:
            continue
        equality = clamp(1.0 - pct_diff(a.price, b.price) / 0.02)
        div = momentum_divergence(series, a.index, b.index,
                                  "bullish" if bottom else "bearish")
        rejection = rejection_wick(series[b.index], LONG if bottom else SHORT)

        direction = LONG if bottom else SHORT
        brk_dir = "up" if bottom else "down"
        line = (0.0, neckline)
        brk = _breakout_index(series, b.index, end, line, brk_dir, cfg)
        status, trigger_i = (CONFIRMED, brk) if brk is not None else (FORMING, end)
        if brk is not None:
            if end - brk > cfg.lookback // 4:
                continue
            for j in range(brk + 1, end + 1):
                probe = series[j].low if bottom else series[j].high
                if abs(probe - neckline) <= tol:
                    status, trigger_i = RETEST, j

        entry = series[trigger_i].close if status != FORMING else neckline
        stop = (min(a.price, b.price) - atr * 0.4) if bottom else (max(a.price, b.price) + atr * 0.4)
        conf = blend((equality, 3), (div, 2), (rejection, 2),
                     (1.0 if status in (CONFIRMED, RETEST) else 0.35, 2))

        out.append(make_signal(
            DOUBLE_BOTTOM.id, series, direction=direction, status=status,
            start_index=a.index, end_index=end, entry=entry, stop=stop,
            targets=measured_targets(entry, height, brk_dir, (0.618, 1.0, 1.618)),
            confidence=conf,
            checks={"Second leg rejection": rejection,
                    "Bullish divergence": div,
                    "Neckline confirmation": 1.0 if status in (CONFIRMED, RETEST) else 0.3,
                    "Height projection TP": equality},
            zones=[Zone(min(a.price, b.price), max(a.price, b.price), "double base")],
            notes=("double bottom" if bottom else "double top") +
                  f", legs within {pct_diff(a.price, b.price):.2%}, neckline {neckline:.6g}",
            meta={"neckline": neckline, "double_top": not bottom}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 8. Diamond Top — expansion then contraction, bearish
# ---------------------------------------------------------------------------

@register(DIAMOND_TOP)
def diamond_top(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    n = len(series)
    if n < 40:
        return out
    end = n - 1
    atr = series.atr(14)

    for win in (30, 45, 60, 90):
        i0 = end - win + 1
        if i0 < 2:
            continue
        prof = range_profile(series, i0, end, 3)
        if len(prof) != 3 or min(prof) <= 0:
            continue
        expansion = (prof[1] - prof[0]) / prof[0]        # widening first
        contraction = (prof[1] - prof[2]) / prof[1]      # then squeezing
        if expansion < 0.2 or contraction < 0.2:
            continue

        mid = i0 + win // 2
        geo = pivot_boundaries(series, mid, end, cfg.pivot_left, cfg.pivot_right)
        lower = geo[1] if geo else (0.0, min(series.lows(mid, end + 1)))

        top_i = max(range(i0, end + 1), key=lambda i: series[i].high)
        topping = clamp(1.0 - abs((top_i - i0) / win - 0.5) / 0.4)
        buyers_lost = clamp((series[top_i].high - series.last.close) / max(atr * 2, 1e-9))
        height = max(series.highs(i0, end + 1)) - min(series.lows(i0, end + 1))

        brk = _breakout_index(series, mid, end, lower, "down", cfg)
        if brk is not None and end - brk > cfg.fresh_bars:
            continue
        status = CONFIRMED if brk is not None else FORMING
        entry = series[brk].close if brk is not None else line_at(lower, end)
        stop = max(series.highs(max(i0, end - 8), end + 1)) + atr * 0.4
        conf = blend((clamp(expansion / 0.6), 3), (buyers_lost, 2),
                     (1.0 if brk is not None else 0.3, 3), (topping, 2))

        out.append(make_signal(
            DIAMOND_TOP.id, series, direction=SHORT, status=status,
            start_index=i0, end_index=end, entry=entry, stop=stop,
            targets=measured_targets(entry, height, "down", (0.5, 1.0, 1.272)),
            confidence=conf,
            checks={"High volatility expansion": clamp(expansion / 0.6),
                    "Loss of buyer control": buyers_lost,
                    "Lower boundary breach": 1.0 if brk is not None else 0.3,
                    "High-conviction short": topping},
            notes=f"amplitude expanded {expansion:.0%} then contracted {contraction:.0%}",
            meta={"expansion": expansion, "contraction": contraction,
                  "range_profile": prof}))
    return _top(out, cfg)

# ---------------------------------------------------------------------------
# 9. Triple Touch Channel — bullish continuation inside a rising channel
# ---------------------------------------------------------------------------

@register(TRIPLE_TOUCH_CHANNEL)
def triple_touch_channel(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    n = len(series)
    if n < 40:
        return out
    end = n - 1
    atr = series.atr(14)
    tol = cfg.tol(series)

    for win in (40, 60, 90, 120):
        i0 = end - win + 1
        if i0 < 2:
            continue
        upper, lower = envelope_lines(series, i0, end)
        if lower[0] <= 0 or upper[0] <= 0:      # only rising channels here
            continue
        width = line_at(upper, end) - line_at(lower, end)
        if width < atr * 1.5:
            continue
        para = parallelism(upper, lower, max(1e-9, atr / win) * 6)
        lower_touches = touch_count(series, lower, i0, end, "low", tol)
        if lower_touches < 3:
            continue
        confluence = clamp(lower_touches / 4.0)

        midline = ((upper[0] + lower[0]) / 2,
                   (upper[1] + lower[1]) / 2)
        above_mid = sum(1 for i in range(i0, end + 1)
                        if series[i].close > line_at(midline, i)) / win
        acceptance = clamp(above_mid / 0.6)

        # trigger: bullish engulfing / strong reclaim at the lower boundary
        trigger_i, trig = None, 0.0
        for j in range(max(i0, end - cfg.fresh_bars), end + 1):
            c, p = series[j], series[j - 1]
            near_lower = abs(c.low - line_at(lower, j)) <= tol * 1.5
            engulf = c.bullish and not p.bullish and c.close > p.open and c.open <= p.close
            if near_lower and (engulf or rejection_wick(c, LONG) > 0.5):
                trigger_i = j
                trig = 1.0 if engulf else 0.7
        status = CONFIRMED if trigger_i is not None else FORMING
        entry = series[trigger_i].close if trigger_i is not None else line_at(lower, end)
        stop = line_at(lower, end) - atr * 0.7
        target_top = line_at(upper, min(end + 10, end) )
        conf = blend((acceptance, 2), (confluence, 3), (trig, 2), (para, 2))

        out.append(make_signal(
            TRIPLE_TOUCH_CHANNEL.id, series, direction=LONG, status=status,
            start_index=i0, end_index=end, entry=entry, stop=stop,
            targets=[line_at(midline, end), target_top, target_top + width * 0.5],
            confidence=conf,
            checks={"Mid-line acceptance": acceptance,
                    "Boundary confluence": confluence,
                    "Bullish engulfing trigger": trig,
                    "Target upper channel": para},
            zones=[Zone(line_at(lower, end), line_at(upper, end), "rising channel")],
            notes=f"rising channel, {lower_touches} lower-boundary touches",
            meta={"touches": lower_touches, "width": width}))
    return _top(out, cfg)


# ---------------------------------------------------------------------------
# 10. Broadening Wedge — volatility expansion / emotion trap
# ---------------------------------------------------------------------------

@register(BROADENING_WEDGE)
def broadening_wedge(series: Series, cfg: ScanConfig) -> List[Signal]:
    out: List[Signal] = []
    n = len(series)
    if n < 30:
        return out
    end = n - 1
    atr = series.atr(14)
    tol = cfg.tol(series)
    piv = series.pivots(cfg.pivot_left, cfg.pivot_right)

    for win in (25, 40, 60):
        i0 = end - win + 1
        if i0 < 2:
            continue
        geo = pivot_boundaries(series, i0, end, cfg.pivot_left, cfg.pivot_right)
        if geo is None:
            continue
        upper, lower, highs, lows = geo
        widening = -convergence(upper, lower, highs[0].index, end)
        if widening < 0.3 or upper[0] <= 0 or lower[0] >= 0:
            continue
        trap = clamp(widening / 0.8)
        swept_up = highs[-1].price > highs[-2].price
        swept_dn = lows[-1].price < lows[-2].price
        both_sides = 1.0 if (swept_up and swept_dn) else 0.5

        # fade the last sweep: reclaim back inside the structure
        last_sweep = max(highs[-1].index, lows[-1].index)
        from_high = highs[-1].index >= lows[-1].index
        direction = SHORT if from_high else LONG
        boundary = line_at(upper, end) if from_high else line_at(lower, end)
        reclaimed = 0.0
        for j in range(last_sweep, end + 1):
            c = series[j]
            if from_high and c.close < line_at(upper, j):
                reclaimed = 1.0
            if (not from_high) and c.close > line_at(lower, j):
                reclaimed = 1.0
        status = CONFIRMED if reclaimed and end - last_sweep <= cfg.fresh_bars + 3 else FORMING
        entry = series.last.close if status == CONFIRMED else boundary
        stop = (highs[-1].price + atr * 0.4) if from_high else (lows[-1].price - atr * 0.4)
        mid = (line_at(upper, end) + line_at(lower, end)) / 2
        opposite = line_at(lower, end) if from_high else line_at(upper, end)
        counter = clamp(abs(entry - mid) / max(atr, 1e-9) / 2)
        conf = blend((trap, 3), (both_sides, 2), (reclaimed, 3), (counter, 1))

        out.append(make_signal(
            BROADENING_WEDGE.id, series, direction=direction, status=status,
            start_index=i0, end_index=end, entry=entry, stop=stop,
            targets=[mid, opposite],
            confidence=conf,
            checks={"Extreme emotion trap": trap,
                    "Liquidity sweep both sides": both_sides,
                    "Structural reclamation": reclaimed,
                    "Counter-momentum move": counter},
            zones=[Zone(line_at(lower, end), line_at(upper, end), "broadening structure")],
            notes=f"broadening {widening:.0%} over {win} bars, fading the "
                  f"{'high' if from_high else 'low'} sweep",
            meta={"widening": widening, "swept_high": from_high}))
    return _top(out, cfg)
