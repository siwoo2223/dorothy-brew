"""Deterministic synthetic price generator.

Used by the tests and by ``dorothy demo`` to produce charts that contain a
known pattern, so detectors can be exercised without market data.
"""

from __future__ import annotations

import math
import random
from typing import List, Optional

from .core import Candle, Series

HOUR = 3600


class PriceBuilder:
    """Append price legs and get back a candle series."""

    def __init__(self, start: float = 100.0, seed: int = 7, noise: float = 0.0025,
                 base_volume: float = 1000.0, step_seconds: int = HOUR,
                 start_ts: int = 1_700_000_000):
        self.rng = random.Random(seed)
        self.price = start
        self.noise = noise
        self.base_volume = base_volume
        self.step = step_seconds
        self.ts = start_ts
        self.candles: List[Candle] = []

    # -- primitives ---------------------------------------------------------
    def _push(self, close: float, vol_mult: float = 1.0, wick: float = 1.0) -> None:
        open_ = self.price
        rng_scale = abs(close - open_) + self.price * self.noise * max(wick, 0.05)
        up_wick = abs(self.rng.gauss(0, 0.4)) * rng_scale * wick
        dn_wick = abs(self.rng.gauss(0, 0.4)) * rng_scale * wick
        high = max(open_, close) + up_wick
        low = min(open_, close) - dn_wick
        volume = max(1.0, self.base_volume * vol_mult * (1 + self.rng.gauss(0, 0.12)))
        self.candles.append(Candle(self.ts, open_, high, low, close, volume))
        self.price = close
        self.ts += self.step

    def leg(self, bars: int, pct: float, vol_mult: float = 1.0,
            noise_mult: float = 1.0, wick: float = 1.0) -> "PriceBuilder":
        """Directional move of ``pct`` (0.10 = +10%) spread over ``bars``."""
        target = self.price * (1 + pct)
        for i in range(bars):
            frac = (i + 1) / bars
            path = self.price + (target - self.price) * (1 / max(1, bars - i))
            drift = path * (1 + self.rng.gauss(0, self.noise * noise_mult))
            self._push(drift, vol_mult * (1 + 0.3 * math.sin(frac * math.pi)), wick)
        return self

    def chop(self, bars: int, width_pct: float = 0.01, vol_mult: float = 1.0,
             drift_pct: float = 0.0, wick: float = 1.0) -> "PriceBuilder":
        """Sideways range of the given width, optionally drifting."""
        center = self.price
        for i in range(bars):
            center *= (1 + drift_pct / max(1, bars))
            offset = math.sin(i * 1.9) * width_pct * 0.5
            self._push(center * (1 + offset + self.rng.gauss(0, self.noise * 0.5)),
                       vol_mult, wick)
        return self

    def arc(self, bars: int, depth_pct: float, vol_mult: float = 0.8) -> "PriceBuilder":
        """Smooth rounded bottom (negative depth for a rounded top)."""
        start = self.price
        for i in range(bars):
            frac = i / max(1, bars - 1)
            shape = -math.sin(frac * math.pi)          # 0 -> -1 -> 0
            target = start * (1 + depth_pct * shape)
            self._push(target * (1 + self.rng.gauss(0, self.noise * 0.4)),
                       vol_mult * (0.6 + 0.8 * abs(shape)))
        return self

    def spike(self, pct: float, vol_mult: float = 3.0, wick: float = 0.2) -> "PriceBuilder":
        self._push(self.price * (1 + pct), vol_mult, wick)
        return self

    def wick_sweep(self, pct: float, direction: str = "down",
                   vol_mult: float = 2.5) -> "PriceBuilder":
        """A bar that pierces a level and closes back inside it."""
        open_ = self.price
        close = open_ * (1 + (0.002 if direction == "down" else -0.002))
        if direction == "down":
            low = open_ * (1 - abs(pct))
            high = max(open_, close) * 1.001
        else:
            high = open_ * (1 + abs(pct))
            low = min(open_, close) * 0.999
        self.candles.append(Candle(self.ts, open_, high, low, close,
                                   self.base_volume * vol_mult))
        self.price = close
        self.ts += self.step
        return self

    def gap(self, pct: float, vol_mult: float = 3.0) -> "PriceBuilder":
        """Displacement bar large enough to leave a fair value gap behind it."""
        open_ = self.price
        close = open_ * (1 + pct)
        high = max(open_, close) * (1 + 0.0005)
        low = min(open_, close) * (1 - 0.0005)
        self.candles.append(Candle(self.ts, open_, high, low, close,
                                   self.base_volume * vol_mult))
        self.price = close
        self.ts += self.step
        return self

    def series(self, symbol: str = "SYNTH", timeframe: str = "1h") -> Series:
        return Series(self.candles, symbol, timeframe)


# ---------------------------------------------------------------------------
# scenarios — each returns a series that contains the named structure
# ---------------------------------------------------------------------------

def bull_flag(seed: int = 3) -> Series:
    b = PriceBuilder(seed=seed)
    b.chop(30, 0.012, 0.8)
    b.leg(14, 0.16, vol_mult=2.2, noise_mult=0.5)       # pole
    b.chop(14, 0.008, 0.45, drift_pct=-0.035)           # flag, volume drying up
    b.leg(4, 0.05, vol_mult=2.6, noise_mult=0.4)        # breakout
    return b.series("BULLFLAG")


def double_bottom(seed: int = 5) -> Series:
    b = PriceBuilder(seed=seed)
    b.chop(20, 0.01, 0.9)
    b.leg(12, -0.14, vol_mult=1.6)
    b.leg(6, 0.07, vol_mult=1.0)
    b.leg(8, -0.065, vol_mult=1.1)                      # equal second low
    b.leg(10, 0.10, vol_mult=1.8)                       # neckline break
    b.chop(4, 0.006, 1.0)
    return b.series("DBOTTOM")


def head_and_shoulders(seed: int = 11) -> Series:
    b = PriceBuilder(seed=seed)
    b.chop(18, 0.01, 0.9)
    b.leg(8, 0.09, vol_mult=1.4)      # left shoulder
    b.leg(6, -0.06, vol_mult=1.0)
    b.leg(9, 0.13, vol_mult=2.4)      # head, high volume
    b.leg(7, -0.10, vol_mult=1.1)
    b.leg(7, 0.075, vol_mult=0.7)     # right shoulder, fading volume
    b.leg(9, -0.11, vol_mult=1.9)     # neckline break
    b.leg(3, 0.02, vol_mult=0.8)      # retest
    return b.series("HNS")


def ascending_triangle(seed: int = 17) -> Series:
    b = PriceBuilder(seed=seed)
    b.chop(16, 0.01, 0.9)
    b.leg(10, 0.10, vol_mult=1.5)
    for depth, bars in ((-0.055, 6), (-0.04, 6), (-0.025, 5), (-0.015, 4)):
        b.leg(bars, depth, vol_mult=0.8)
        b.leg(bars, -depth / (1 + depth), vol_mult=1.0)  # back to the same high
    b.leg(4, 0.06, vol_mult=2.4)
    return b.series("ASCTRI")


def rectangle(seed: int = 23) -> Series:
    b = PriceBuilder(seed=seed, noise=0.0015)
    b.leg(12, 0.08, vol_mult=1.2)
    for _ in range(3):
        b.leg(5, -0.045, vol_mult=0.9)
        b.leg(5, 0.047, vol_mult=0.9)
    b.leg(3, 0.05, vol_mult=2.5)
    return b.series("RECT")


def cup_and_handle(seed: int = 29) -> Series:
    b = PriceBuilder(seed=seed, noise=0.002)
    b.leg(12, 0.10, vol_mult=1.2)
    b.arc(46, 0.16, vol_mult=0.8)                       # the cup
    b.chop(9, 0.006, 0.4, drift_pct=-0.03)              # the handle
    b.leg(4, 0.05, vol_mult=2.4)
    return b.series("CUP")


def falling_wedge(seed: int = 31) -> Series:
    b = PriceBuilder(seed=seed)
    b.chop(15, 0.01, 0.9)
    moves = [(-0.09, 0.055), (-0.07, 0.045), (-0.05, 0.035), (-0.035, 0.025), (-0.02, 0.015)]
    for down, up in moves:
        b.leg(5, down, vol_mult=1.1)
        b.leg(4, up, vol_mult=0.8)
    b.leg(4, 0.06, vol_mult=2.2)
    return b.series("WEDGE")


def liquidity_sweep(seed: int = 37) -> Series:
    b = PriceBuilder(seed=seed)
    b.chop(24, 0.012, 1.0)
    b.leg(8, -0.06, vol_mult=1.2)
    b.leg(7, 0.04, vol_mult=1.0)
    b.leg(5, -0.03, vol_mult=0.9)
    b.wick_sweep(0.035, "down")                         # takes the prior low, closes back up
    b.leg(4, 0.05, vol_mult=1.9)
    return b.series("SWEEP")


def fvg_impulse(seed: int = 41) -> Series:
    b = PriceBuilder(seed=seed)
    b.chop(25, 0.01, 1.0)
    b.leg(5, 0.02, vol_mult=1.1)
    b.gap(0.05)                                         # displacement leaves an FVG
    b.leg(4, 0.03, vol_mult=1.4)
    b.leg(5, -0.035, vol_mult=0.8)                      # retrace into the gap
    b.leg(4, 0.03, vol_mult=1.3)
    return b.series("FVG")


def range_walk(seed: int = 2, bars: int = 220) -> Series:
    """Featureless noise — used to check the false-positive rate."""
    b = PriceBuilder(seed=seed, noise=0.006)
    b.chop(bars, 0.02, 1.0)
    return b.series("NOISE")


SCENARIOS = {
    "bull_flag": bull_flag,
    "double_bottom": double_bottom,
    "head_and_shoulders": head_and_shoulders,
    "ascending_triangle": ascending_triangle,
    "rectangle": rectangle,
    "cup_and_handle": cup_and_handle,
    "falling_wedge": falling_wedge,
    "liquidity_sweep": liquidity_sweep,
    "fvg_impulse": fvg_impulse,
    "range_walk": range_walk,
}


# ---------------------------------------------------------------------------
# harmonic scenarios — legs are placed on exact ratio levels
# ---------------------------------------------------------------------------

def from_levels(levels: List[float], bars_per_leg: int = 7, base: float = 100.0,
                span_pct: float = 0.25, seed: int = 13, symbol: str = "XABCD",
                lead_in: int = 22, tail: int = 4) -> Series:
    """Draw a series that visits ``levels`` (normalised 0..1) in order."""
    b = PriceBuilder(start=base, seed=seed, noise=0.0009)
    b.chop(lead_in, 0.006, 1.0)
    for lv in levels:
        target = base * (1 + lv * span_pct)
        pct = target / b.price - 1
        b.leg(bars_per_leg, pct, vol_mult=1.2, noise_mult=0.35, wick=0.25)
    if tail:
        b.chop(tail, 0.004, 1.0, wick=0.3)
    return b.series(symbol)


# X, A, B, C, D as fractions of the XA leg (X = 0, A = 1)
HARMONIC_LEVELS = {
    "bat":       [0.0, 1.0, 0.500, 0.809, 0.114],
    "butterfly": [0.0, 1.0, 0.214, 0.700, -0.272],
    "crab":      [0.0, 1.0, 0.382, 0.930, -0.618],
    "deep_crab": [0.0, 1.0, 0.114, 0.662, -0.618],
    "shark":     [0.0, 1.0, 0.500, 1.150, 0.000],
    "abcd":      [0.0, 1.0, 0.500, 0.809, 0.309],
}


def harmonic(name: str, seed: int = 13) -> Series:
    """A chart that completes the named harmonic at its D point."""
    return from_levels(HARMONIC_LEVELS[name], seed=seed, symbol=name.upper())


def trend_with_bos(seed: int = 47) -> Series:
    """Clean uptrend making higher highs — BOS / order block / OB retest."""
    b = PriceBuilder(seed=seed)
    b.chop(20, 0.01, 0.9)
    for i in range(4):
        b.leg(6, 0.055 - i * 0.005, vol_mult=1.8)
        b.leg(5, -0.025, vol_mult=0.7)
    b.leg(5, -0.03, vol_mult=0.8)      # pullback into the last order block
    b.leg(6, 0.11, vol_mult=2.6)       # break of structure on volume
    b.leg(3, -0.02, vol_mult=0.8)      # shallow pullback entry
    return b.series("TREND")


def trending_market(seed: int = 71, legs: int = 14, drift: float = 0.05) -> Series:
    """A persistent uptrend of impulse + shallow pullback — flags, BOS, channels."""
    b = PriceBuilder(seed=seed, noise=0.003)
    b.chop(24, 0.012, 0.9)
    for i in range(legs):
        b.leg(7, drift * (1 + 0.15 * ((i % 3) - 1)), vol_mult=1.9, noise_mult=0.6)
        b.leg(6, -drift * 0.35, vol_mult=0.7)
    return b.series("TREND-UP")


def random_walk(seed: int = 101, bars: int = 300, vol: float = 0.008) -> Series:
    """Structureless geometric random walk — the false-positive control."""
    b = PriceBuilder(seed=seed, noise=vol)
    for _ in range(bars):
        b._push(b.price * (1 + b.rng.gauss(0, vol)), 1.0)
    return b.series("RANDOM")


SCENARIOS.update({
    "trend_with_bos": trend_with_bos,
    "trending_market": trending_market,
    "random_walk": random_walk,
    **{f"harmonic_{k}": (lambda k=k: harmonic(k)) for k in HARMONIC_LEVELS},
})
