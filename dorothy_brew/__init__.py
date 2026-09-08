"""dorothy-brew — pattern-based auto trading AI.

Thirty detectors covering three playbooks (classic chart patterns, smart money
concepts, harmonic & fibonacci), a scan engine that ranks what fired, a trade
plan for each signal, an event-driven backtester and a Bitget live feed. Pure
standard library.

    from dorothy_brew import load, scan, render_report

    series = load("btc_1h.csv", "BTCUSDT", "1H")
    report = scan(series)
    print(render_report(report))

    from dorothy_brew import backtest, render_backtest
    print(render_backtest(backtest(series)))

    from dorothy_brew import BitgetClient, LiveFeed
    feed = LiveFeed(BitgetClient("usdt-futures"), "BTCUSDT", "1H")
    feed.run(lambda event: print(event.new_signals))
"""

from .backtester import BacktestConfig, BacktestResult, Trade, backtest
from .config import ScanConfig
from .core import Candle, Series
from .data import load, load_csv, load_json, resample
from .engine import MultiReport, ScanReport, TradePlan, build_plan, plans, scan, scan_multi
from .feeds import BitgetClient, BitgetError, LiveEvent, LiveFeed
from .registry import CLASSIC, HARMONIC, SMC, all_specs, get as get_spec
from .report import (card, catalogue, render_backtest, render_live_event,
                     render_multi, render_report, signal_block)
from .signals import CONFIRMED, FORMING, LONG, RETEST, SHORT, Signal, Zone

__version__ = "0.1.0"
__all__ = [
    "Candle", "Series", "ScanConfig", "Signal", "Zone",
    "load", "load_csv", "load_json", "resample",
    "scan", "scan_multi", "ScanReport", "MultiReport", "TradePlan", "build_plan", "plans",
    "render_report", "render_multi", "render_backtest", "render_live_event",
    "signal_block", "card", "catalogue",
    "backtest", "BacktestConfig", "BacktestResult", "Trade",
    "BitgetClient", "BitgetError", "LiveFeed", "LiveEvent",
    "all_specs", "get_spec", "CLASSIC", "SMC", "HARMONIC",
    "LONG", "SHORT", "CONFIRMED", "FORMING", "RETEST", "__version__",
]
