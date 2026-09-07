"""Command line interface: ``dorothy <command>`` (or ``python -m dorothy_brew``)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Optional

from . import data, synth
from .config import ScanConfig
from .core import Series
from .engine import MultiReport, ScanReport, plans, scan, scan_multi
from .registry import CATEGORY_TITLES, REGISTRY, all_specs
from .report import card, catalogue, render_multi, render_report


def _cfg_from_args(a: argparse.Namespace) -> ScanConfig:
    cfg = ScanConfig()
    for name in ("lookback", "min_confidence", "equity", "risk_pct", "fresh_bars",
                 "ratio_tol", "tol_atr"):
        value = getattr(a, name, None)
        if value is not None:
            setattr(cfg, name, value)
    if getattr(a, "require_volume", False):
        cfg.require_volume = True
    if getattr(a, "no_forming", False):
        cfg.include_forming = False
    return cfg


def _load(path: str, symbol: str, timeframe: str) -> Series:
    if path.startswith("demo:"):
        name = path.split(":", 1)[1]
        if name not in synth.SCENARIOS:
            raise SystemExit(f"unknown demo scenario '{name}'. "
                             f"available: {', '.join(sorted(synth.SCENARIOS))}")
        series = synth.SCENARIOS[name]()
        series.timeframe = timeframe or series.timeframe
        return series
    series = data.load(path, symbol, timeframe)
    if not len(series):
        raise SystemExit(f"no candles parsed from {path}")
    return series


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--symbol", default="", help="symbol label for the output")
    p.add_argument("--timeframe", "--tf", dest="timeframe", default="",
                   help="timeframe label, e.g. 15m / 1H / 4H / 1D")
    p.add_argument("--category", action="append", choices=sorted(CATEGORY_TITLES),
                   help="restrict to a playbook (repeatable)")
    p.add_argument("--pattern", action="append", help="restrict to pattern ids (repeatable)")
    p.add_argument("--lookback", type=int, help="bars searched (default 240)")
    p.add_argument("--min-confidence", dest="min_confidence", type=float,
                   help="drop signals below this confidence (default 0.45)")
    p.add_argument("--fresh-bars", dest="fresh_bars", type=int,
                   help="how recently the trigger must have fired (default 5)")
    p.add_argument("--tol-atr", dest="tol_atr", type=float, help="level tolerance in ATR units")
    p.add_argument("--ratio-tol", dest="ratio_tol", type=float, help="harmonic ratio slack")
    p.add_argument("--equity", type=float, help="account equity for position sizing")
    p.add_argument("--risk", dest="risk_pct", type=float, help="%% of equity risked per trade")
    p.add_argument("--require-volume", action="store_true",
                   help="reject breakouts without volume expansion")
    p.add_argument("--no-forming", action="store_true", help="executable signals only")
    p.add_argument("--top", type=int, default=None, help="show only the N best per section")
    p.add_argument("--json", action="store_true", help="machine-readable output")


def cmd_scan(a: argparse.Namespace) -> int:
    cfg = _cfg_from_args(a)
    series = _load(a.file, a.symbol, a.timeframe)
    report = scan(series, cfg, patterns=a.pattern, categories=a.category, debug=a.debug)
    if a.json:
        payload = report.to_dict()
        payload["plans"] = [p.to_dict() for p in plans(report, cfg)]
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render_report(report, cfg, top=a.top))
    return 0 if report.signals else 1


def cmd_multi(a: argparse.Namespace) -> int:
    cfg = _cfg_from_args(a)
    series_by_tf: Dict[str, Series] = {}
    for item in a.inputs:
        if ":" not in item or item.startswith("demo:"):
            raise SystemExit("each input must be given as <timeframe>:<file>, e.g. 15m:btc_15m.csv")
        tf, path = item.split(":", 1)
        series_by_tf[tf] = _load(path, a.symbol, tf)
    multi = scan_multi(series_by_tf, cfg, categories=a.category,
                       respect_playbook_tf=not a.any_timeframe)
    if a.json:
        print(json.dumps(multi.to_dict(), indent=2, default=str))
    else:
        print(render_multi(multi, cfg, top=a.top))
    return 0


def cmd_patterns(a: argparse.Namespace) -> int:
    specs = all_specs(a.category[0] if a.category else None)
    if a.json:
        print(json.dumps([s.to_dict() for s in specs], indent=2, ensure_ascii=False))
    else:
        print(catalogue(specs))
    return 0


def cmd_explain(a: argparse.Namespace) -> int:
    if a.pattern_id not in REGISTRY:
        raise SystemExit(f"unknown pattern '{a.pattern_id}'. "
                         f"run 'dorothy patterns' for the list")
    spec = REGISTRY[a.pattern_id]
    if a.json:
        print(json.dumps(spec.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(card(spec))
    return 0


def cmd_demo(a: argparse.Namespace) -> int:
    cfg = _cfg_from_args(a)
    names = [a.scenario] if a.scenario else sorted(synth.SCENARIOS)
    for name in names:
        if name not in synth.SCENARIOS:
            raise SystemExit(f"unknown scenario '{name}'")
        series = synth.SCENARIOS[name]()
        series.timeframe = a.timeframe or "1h"
        report = scan(series, cfg, categories=a.category, patterns=a.pattern)
        print(f"\n=== demo scenario: {name} ===")
        print(render_report(report, cfg, top=a.top or 3))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dorothy",
        description="Pattern-based auto trading AI — 30 detectors across the classic, "
                    "smart-money and harmonic playbooks.")
    p.add_argument("--debug", action="store_true", help="print detector tracebacks")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="scan one OHLCV file (csv/json) or demo:<scenario>")
    s.add_argument("file")
    _add_common(s)
    s.set_defaults(func=cmd_scan)

    m = sub.add_parser("multi", help="scan several timeframes: 15m:a.csv 1h:b.csv 4h:c.csv")
    m.add_argument("inputs", nargs="+")
    m.add_argument("--any-timeframe", action="store_true",
                   help="run every pattern on every timeframe instead of the playbook's")
    _add_common(m)
    m.set_defaults(func=cmd_multi)

    c = sub.add_parser("patterns", help="list the pattern catalogue")
    c.add_argument("--category", action="append", choices=sorted(CATEGORY_TITLES))
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_patterns)

    e = sub.add_parser("explain", help="print one pattern's playbook card")
    e.add_argument("pattern_id")
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=cmd_explain)

    d = sub.add_parser("demo", help="run the detectors on built-in synthetic charts")
    d.add_argument("scenario", nargs="?")
    _add_common(d)
    d.set_defaults(func=cmd_demo)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
