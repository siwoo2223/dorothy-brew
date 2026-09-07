"""Scan engine: run every registered detector, rank and aggregate the output."""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from .config import ScanConfig
from .core import Series
from .registry import CATEGORY_TITLES, PatternSpec, REGISTRY, all_specs
from .signals import CONFIRMED, FORMING, LONG, RETEST, SHORT, Signal
from . import patterns as _patterns  # noqa: F401  (registers the detectors)


@dataclass
class ScanReport:
    symbol: str
    timeframe: str
    bars: int
    signals: List[Signal] = field(default_factory=list)
    errors: Dict[str, str] = field(default_factory=dict)
    config: Optional[ScanConfig] = None

    # -- views --------------------------------------------------------------
    def actionable(self) -> List[Signal]:
        return [s for s in self.signals if s.status in (CONFIRMED, RETEST)]

    def watchlist(self) -> List[Signal]:
        return [s for s in self.signals if s.status == FORMING]

    def by_category(self) -> Dict[str, List[Signal]]:
        out: Dict[str, List[Signal]] = {k: [] for k in CATEGORY_TITLES}
        for s in self.signals:
            out[REGISTRY[s.pattern_id].category].append(s)
        return out

    def bias(self) -> Dict[str, float]:
        """Net directional conviction across everything that fired."""
        long_w = sum(s.score() for s in self.signals if s.direction == LONG)
        short_w = sum(s.score() for s in self.signals if s.direction == SHORT)
        total = long_w + short_w
        return {"long": round(long_w, 3), "short": round(short_w, 3),
                "net": round((long_w - short_w) / total, 3) if total else 0.0}

    def top(self, n: int = 5) -> List[Signal]:
        return sorted(self.signals, key=lambda s: -s.score())[:n]

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "bars": self.bars,
            "bias": self.bias(),
            "signals": [s.to_dict() for s in sorted(self.signals, key=lambda s: -s.score())],
            "errors": self.errors,
        }


def sanitize(sig: Signal) -> Optional[Signal]:
    """Drop setups that reality has already overtaken.

    A signal is only tradable while its invalidation level still sits on the
    correct side of price and at least one objective is still ahead of it, so
    targets already met are trimmed and a breached stop kills the signal.
    """
    if sig.risk <= 0:
        return None
    long = sig.direction == LONG
    if (long and sig.stop >= sig.entry) or (not long and sig.stop <= sig.entry):
        return None
    ahead = [t for t in sig.targets if (t > sig.entry if long else t < sig.entry)]
    if not ahead:
        return None
    ahead.sort(reverse=not long)
    sig.targets = ahead
    return sig


def _select(patterns: Optional[Sequence[str]], categories: Optional[Sequence[str]]) -> List[PatternSpec]:
    specs = all_specs()
    if categories:
        specs = [s for s in specs if s.category in categories]
    if patterns:
        wanted = set(patterns)
        specs = [s for s in specs if s.id in wanted]
    return specs


def scan(series: Series, cfg: Optional[ScanConfig] = None, *,
         patterns: Optional[Sequence[str]] = None,
         categories: Optional[Sequence[str]] = None,
         debug: bool = False) -> ScanReport:
    """Run the selected detectors over one series."""
    cfg = cfg or ScanConfig()
    report = ScanReport(series.symbol, series.timeframe, len(series), config=cfg)
    if len(series) < 10:
        return report

    for spec in _select(patterns, categories):
        try:
            found = spec.detector(series, cfg) or []
        except Exception as exc:                     # one bad detector must not kill the scan
            report.errors[spec.id] = f"{type(exc).__name__}: {exc}"
            if debug:
                traceback.print_exc()
            continue
        for sig in found:
            if sig.confidence < cfg.min_confidence:
                continue
            if sanitize(sig) is None:
                continue
            if sig.status == FORMING and not cfg.include_forming:
                continue
            sig.meta.setdefault("pattern_name", spec.name)
            sig.meta.setdefault("pattern_name_ko", spec.name_ko)
            sig.meta.setdefault("category", spec.category)
            sig.meta.setdefault("hold", spec.hold)
            sig.meta.setdefault("analysis_tf", spec.analysis_tf)
            sig.meta.setdefault("entry_tf", spec.entry_tf)
            report.signals.append(sig)

    report.signals.sort(key=lambda s: -s.score())
    return report


# ---------------------------------------------------------------------------
# multi-timeframe
# ---------------------------------------------------------------------------

@dataclass
class MultiReport:
    symbol: str
    reports: Dict[str, ScanReport] = field(default_factory=dict)

    def all_signals(self) -> List[Signal]:
        out: List[Signal] = []
        for r in self.reports.values():
            out.extend(r.signals)
        return sorted(out, key=lambda s: -s.score())

    def bias(self) -> Dict[str, float]:
        long_w = sum(s.score() for s in self.all_signals() if s.direction == LONG)
        short_w = sum(s.score() for s in self.all_signals() if s.direction == SHORT)
        total = long_w + short_w
        return {"long": round(long_w, 3), "short": round(short_w, 3),
                "net": round((long_w - short_w) / total, 3) if total else 0.0}

    def to_dict(self) -> Dict:
        return {"symbol": self.symbol,
                "bias": self.bias(),
                "timeframes": {tf: r.to_dict() for tf, r in self.reports.items()}}


def scan_multi(series_by_tf: Dict[str, Series], cfg: Optional[ScanConfig] = None, *,
               respect_playbook_tf: bool = True,
               categories: Optional[Sequence[str]] = None) -> MultiReport:
    """Scan several timeframes and boost signals confirmed by the higher one.

    ``respect_playbook_tf`` runs each pattern only on the entry timeframes its
    playbook card prescribes (when that timeframe is present in the input).
    """
    cfg = cfg or ScanConfig()
    multi = MultiReport(next(iter(series_by_tf.values())).symbol if series_by_tf else "")
    order = list(series_by_tf.keys())

    for tf, series in series_by_tf.items():
        wanted = None
        if respect_playbook_tf:
            wanted = [s.id for s in all_specs()
                      if tf in s.entry_tf or not any(t in series_by_tf for t in s.entry_tf)]
        multi.reports[tf] = scan(series, cfg, patterns=wanted, categories=categories)

    # higher-timeframe agreement bumps confidence, disagreement trims it
    htf_bias = {tf: multi.reports[tf].bias()["net"] for tf in order}
    for i, tf in enumerate(order):
        higher = order[i + 1:]
        if not higher:
            continue
        net = sum(htf_bias[h] for h in higher) / len(higher)
        for sig in multi.reports[tf].signals:
            want = 1 if sig.direction == LONG else -1
            agree = want * net
            sig.confidence = round(max(0.0, min(1.0, sig.confidence * (1.0 + 0.2 * agree))), 4)
            sig.meta["htf_alignment"] = round(agree, 3)
        multi.reports[tf].signals.sort(key=lambda s: -s.score())
    return multi


# ---------------------------------------------------------------------------
# trade plan
# ---------------------------------------------------------------------------

@dataclass
class TradePlan:
    signal: Signal
    size: float
    risk_amount: float
    take_profits: List[Dict[str, float]]

    def to_dict(self) -> Dict:
        return {"signal": self.signal.to_dict(), "size": round(self.size, 8),
                "risk_amount": round(self.risk_amount, 4),
                "take_profits": self.take_profits}


def build_plan(sig: Signal, cfg: ScanConfig) -> TradePlan:
    """Turn a signal into a sized plan with scale-out levels."""
    size = sig.position_size(cfg.equity, cfg.risk_pct)
    risk_amount = cfg.equity * cfg.risk_pct / 100.0
    weights = [0.5, 0.3, 0.2][:len(sig.targets)] or [1.0]
    if len(weights) < len(sig.targets):
        weights += [0.0] * (len(sig.targets) - len(weights))
    tps = []
    for tgt, w in zip(sig.targets, weights):
        rr = abs(tgt - sig.entry) / sig.risk if sig.risk else 0.0
        tps.append({"price": round(tgt, 10), "size_pct": round(w * 100, 1), "rr": round(rr, 2)})
    return TradePlan(sig, size, risk_amount, tps)


def plans(report: ScanReport, cfg: Optional[ScanConfig] = None,
          actionable_only: bool = True) -> List[TradePlan]:
    cfg = cfg or report.config or ScanConfig()
    sigs = report.actionable() if actionable_only else report.signals
    return [build_plan(s, cfg) for s in sigs]
