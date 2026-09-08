"""Command line interface: ``dorothy <command>`` (or ``python -m dorothy_brew``)."""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import URLError
from typing import Dict, List, Optional

from . import data, synth
from .backtester import BacktestConfig, backtest
from .config import ScanConfig
from .core import Series
from .engine import MultiReport, ScanReport, plans, scan, scan_multi
from .feeds import BASE_URL, PRODUCTS, BitgetClient, BitgetError, LiveFeed
from .feeds.http import HttpError
from .registry import CATEGORY_TITLES, REGISTRY, all_specs
from .report import (card, catalogue, render_backtest, render_live_event,
                     render_multi, render_report)


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


def _bt_from_args(a: argparse.Namespace) -> BacktestConfig:
    bt = BacktestConfig()
    for arg, field in (("equity", "initial_equity"), ("risk_pct", "risk_pct"),
                       ("warmup", "warmup"), ("window", "window"), ("step", "step"),
                       ("max_open", "max_open"), ("fee_bps", "fee_bps"),
                       ("slippage_bps", "slippage_bps"), ("max_bars", "max_bars_in_trade"),
                       ("cooldown", "cooldown_bars"), ("entry_mode", "entry_mode")):
        value = getattr(a, arg, None)
        if value is not None:
            setattr(bt, field, value)
    if getattr(a, "no_shorts", False):
        bt.allow_shorts = False
    if getattr(a, "no_compound", False):
        bt.compound = False
    return bt


def cmd_backtest(a: argparse.Namespace) -> int:
    cfg = _cfg_from_args(a)
    bt = _bt_from_args(a)
    series = _load(a.file, a.symbol, a.timeframe)
    result = backtest(series, cfg, bt, patterns=a.pattern, categories=a.category)
    if a.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        print(render_backtest(result, show_trades=a.trades))
    return 0 if result.trades else 1


def _client(a: argparse.Namespace) -> BitgetClient:
    return BitgetClient(product=a.product, timeout=a.timeout)


def _network_guard(fn, *args):
    """Turn transport failures into a one-line message instead of a traceback."""
    try:
        return fn(*args)
    except BitgetError as exc:
        raise SystemExit(f"bitget rejected the request: {exc}")
    except HttpError as exc:
        raise SystemExit(f"bitget returned {exc.status}: {exc.body[:200]}")
    except (URLError, OSError) as exc:
        raise SystemExit(f"could not reach {BASE_URL}: {exc}. Check connectivity, "
                         f"any HTTPS_PROXY setting, and whether the API is "
                         f"reachable from your region.")


def cmd_fetch(a: argparse.Namespace) -> int:
    client = _client(a)
    series = _network_guard(lambda: client.history(a.symbol, a.timeframe, bars=a.bars))
    if not len(series):
        raise SystemExit(f"bitget returned no candles for {a.symbol} {a.timeframe}")
    if a.out:
        data.write_csv(series, a.out)
        print(f"{len(series)} candles -> {a.out} "
              f"({series[0].ts} .. {series[-1].ts})")
    if a.scan or not a.out:
        cfg = _cfg_from_args(a)
        report = scan(series, cfg, patterns=a.pattern, categories=a.category)
        print(json.dumps(report.to_dict(), indent=2, default=str) if a.json
              else render_report(report, cfg, top=a.top))
    return 0


def cmd_live(a: argparse.Namespace) -> int:
    cfg = _cfg_from_args(a)
    feed = LiveFeed(_client(a), a.symbol, a.timeframe, scan_cfg=cfg, window=a.window,
                    patterns=a.pattern, categories=a.category,
                    poll_seconds=a.poll_seconds)
    _network_guard(feed.prime)
    print(f"primed {len(feed.series)} bars of {feed.symbol} {feed.timeframe} "
          f"({a.product}); waiting for the next close. Ctrl-C to stop.")
    if a.once:
        report = feed.scan_now()
        print(json.dumps(report.to_dict(), indent=2, default=str) if a.json
              else render_report(report, cfg, top=a.top))
        return 0

    def emit(event) -> None:
        text = render_live_event(event, cfg, actionable_only=a.no_forming)
        print(text, flush=True)

    try:
        feed.run(emit, max_polls=a.max_polls)
    except KeyboardInterrupt:
        print("\nstopped")
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

    b = sub.add_parser("backtest", help="replay a file bar by bar and score the signals")
    b.add_argument("file")
    _add_common(b)
    b.add_argument("--warmup", type=int, help="bars before the first scan (default 150)")
    b.add_argument("--window", type=int, help="bars handed to the scanner (default 320)")
    b.add_argument("--step", type=int, help="scan every N bars (default 1)")
    b.add_argument("--max-open", dest="max_open", type=int, help="concurrent positions")
    b.add_argument("--fee-bps", dest="fee_bps", type=float, help="fee per side in bps")
    b.add_argument("--slippage-bps", dest="slippage_bps", type=float, help="slippage in bps")
    b.add_argument("--max-bars", dest="max_bars", type=int, help="time stop, in bars")
    b.add_argument("--cooldown", type=int, help="bars before re-entering the same pattern")
    b.add_argument("--entry-mode", dest="entry_mode", choices=["next_open", "signal_close"])
    b.add_argument("--no-shorts", action="store_true")
    b.add_argument("--no-compound", action="store_true")
    b.add_argument("--trades", type=int, default=10, help="how many recent trades to print")
    b.set_defaults(func=cmd_backtest)

    f = sub.add_parser("fetch", help="download Bitget candles (public API, no key)")
    f.add_argument("symbol", help="e.g. BTCUSDT")
    f.add_argument("--product", default="usdt-futures", choices=list(PRODUCTS))
    f.add_argument("--bars", type=int, default=500, help="how many candles (default 500)")
    f.add_argument("--out", help="write the candles to this CSV")
    f.add_argument("--scan", action="store_true", help="also scan what was downloaded")
    f.add_argument("--timeout", type=float, default=10.0)
    _add_common(f)
    f.set_defaults(func=cmd_fetch, timeframe="1h")

    live = sub.add_parser("live", help="poll Bitget and scan every closed bar")
    live.add_argument("symbol", help="e.g. BTCUSDT")
    live.add_argument("--product", default="usdt-futures", choices=list(PRODUCTS))
    live.add_argument("--window", type=int, default=400, help="rolling bars kept in memory")
    live.add_argument("--poll-seconds", dest="poll_seconds", type=float,
                      help="override the poll interval (default: the next bar close)")
    live.add_argument("--max-polls", dest="max_polls", type=int, help="stop after N polls")
    live.add_argument("--once", action="store_true", help="scan the current bars and exit")
    live.add_argument("--timeout", type=float, default=10.0)
    _add_common(live)
    live.set_defaults(func=cmd_live, timeframe="1h")

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
