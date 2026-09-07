"""Signal objects produced by the detectors and consumed by the engine."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

LONG = "long"
SHORT = "short"

# lifecycle of a setup — the execution rule from the playbook is
# "never pre-run a pattern; execute only upon structural breakout or retest".
FORMING = "forming"        # geometry valid, trigger not hit yet -> watchlist only
CONFIRMED = "confirmed"    # breakout / structural trigger fired -> executable
RETEST = "retest"          # price came back to the broken level -> preferred entry


@dataclass
class Zone:
    """A price band (order block, FVG, PRZ, demand zone...)."""
    low: float
    high: float
    label: str = ""

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2.0

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high

    def to_dict(self) -> Dict[str, Any]:
        return {"low": self.low, "high": self.high, "mid": self.mid, "label": self.label}


@dataclass
class Signal:
    pattern_id: str
    direction: str                     # LONG | SHORT
    status: str                        # FORMING | CONFIRMED | RETEST
    start_index: int
    end_index: int
    entry: float
    stop: float
    targets: List[float] = field(default_factory=list)
    confidence: float = 0.5            # 0..1, blended from the pattern's key-focus checks
    checks: Dict[str, float] = field(default_factory=dict)
    zones: List[Zone] = field(default_factory=list)
    notes: str = ""
    symbol: str = ""
    timeframe: str = ""
    ts: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    # -- derived ------------------------------------------------------------
    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_risk(self) -> float:
        if not self.targets or self.risk <= 0:
            return 0.0
        return abs(self.targets[-1] - self.entry) / self.risk

    @property
    def first_rr(self) -> float:
        if not self.targets or self.risk <= 0:
            return 0.0
        return abs(self.targets[0] - self.entry) / self.risk

    def score(self) -> float:
        """Ranking score: confidence, weighted by status and by first-target R:R."""
        status_w = {CONFIRMED: 1.0, RETEST: 0.95, FORMING: 0.6}[self.status]
        rr_w = min(1.0, 0.55 + 0.15 * self.first_rr)
        return round(self.confidence * status_w * rr_w, 4)

    def position_size(self, equity: float, risk_pct: float = 1.0) -> float:
        """Units to trade so that a stop-out costs ``risk_pct`` % of equity."""
        if self.risk <= 0:
            return 0.0
        return (equity * risk_pct / 100.0) / self.risk

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["zones"] = [z.to_dict() for z in self.zones]
        d["risk"] = self.risk
        d["reward_risk"] = round(self.reward_risk, 3)
        d["score"] = self.score()
        return d


def make_signal(pattern_id: str, series, *, direction: str, status: str,
                start_index: int, end_index: int, entry: float, stop: float,
                targets: Optional[List[float]] = None, confidence: float = 0.5,
                checks: Optional[Dict[str, float]] = None,
                zones: Optional[List[Zone]] = None, notes: str = "",
                meta: Optional[Dict[str, Any]] = None) -> Signal:
    """Build a signal, stamping it with the series' identity and bar time."""
    end_index = min(end_index, len(series) - 1)
    return Signal(
        pattern_id=pattern_id,
        direction=direction,
        status=status,
        start_index=max(0, start_index),
        end_index=end_index,
        entry=entry,
        stop=stop,
        targets=list(targets or []),
        confidence=round(max(0.0, min(1.0, confidence)), 4),
        checks={k: round(v, 3) for k, v in (checks or {}).items()},
        zones=list(zones or []),
        notes=notes,
        symbol=series.symbol,
        timeframe=series.timeframe,
        ts=series[end_index].ts,
        meta=dict(meta or {}),
    )
