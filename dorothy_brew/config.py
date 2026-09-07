"""Tunable knobs for the scan. One object is threaded through every detector."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict


@dataclass
class ScanConfig:
    lookback: int = 240            # bars searched for a pattern
    pivot_left: int = 3            # fractal strength (left bars)
    pivot_right: int = 3           # fractal strength (right bars) — confirmation lag
    tol_atr: float = 0.6           # "same level" tolerance, in ATR units
    ratio_tol: float = 0.10        # harmonic ratio slack (10% of the target ratio)
    equal_tol: float = 0.0015      # equal highs/lows tolerance (0.15%)
    min_volume_ratio: float = 1.2  # breakout volume vs. average
    require_volume: bool = False   # hard-fail a breakout without volume expansion
    include_forming: bool = True   # keep pre-trigger setups on the watchlist
    min_confidence: float = 0.45   # drop anything weaker
    max_signals_per_pattern: int = 3
    fresh_bars: int = 5            # a signal is "fresh" if it triggered this recently
    risk_pct: float = 1.0          # % of equity risked per trade
    equity: float = 10_000.0

    def tol(self, series, at=None) -> float:
        """Absolute price tolerance derived from ATR."""
        atr = series.atr(14, at)
        if atr <= 0 and len(series):
            atr = abs(series[-1].close) * 0.002
        return atr * self.tol_atr

    def to_dict(self) -> Dict:
        return asdict(self)
