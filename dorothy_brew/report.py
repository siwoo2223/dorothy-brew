"""Terminal rendering of scans, signals and playbook cards."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Optional

from .config import ScanConfig
from .engine import MultiReport, ScanReport, build_plan
from .registry import (CATEGORY_TITLES, CORE_PRINCIPLES, EXECUTION_RULES,
                       PatternSpec, REGISTRY, all_specs)
from .signals import CONFIRMED, FORMING, LONG, RETEST, Signal

BAR = "-" * 78
STATUS_MARK = {CONFIRMED: "[EXECUTE]", RETEST: "[RETEST ]", FORMING: "[WATCH  ]"}


def _fmt_price(v: float) -> str:
    if v == 0:
        return "0"
    a = abs(v)
    digits = 2 if a >= 100 else 4 if a >= 1 else 6 if a >= 0.01 else 8
    return f"{v:,.{digits}f}"


def _fmt_time(ts: int) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _bars(score: float, width: int = 10) -> str:
    filled = int(round(score * width))
    return "#" * filled + "." * (width - filled)


def signal_block(sig: Signal, cfg: Optional[ScanConfig] = None, indent: str = "") -> str:
    spec = REGISTRY[sig.pattern_id]
    cfg = cfg or ScanConfig()
    plan = build_plan(sig, cfg)
    lines = [
        f"{indent}{STATUS_MARK[sig.status]} {spec.name} ({spec.name_ko}) "
        f"| {sig.direction.upper()} | score {sig.score():.2f} conf {sig.confidence:.2f}",
        f"{indent}  {CATEGORY_TITLES[spec.category]} #{spec.number} - {spec.kind} "
        f"| analysis {'/'.join(spec.analysis_tf)} -> entry {'/'.join(spec.entry_tf)} "
        f"| hold {spec.hold}",
        f"{indent}  entry {_fmt_price(sig.entry)}   stop {_fmt_price(sig.stop)}   "
        f"risk {_fmt_price(sig.risk)}   size {plan.size:.4f} @ {cfg.risk_pct}% of {cfg.equity:,.0f}",
    ]
    if plan.take_profits:
        tps = "  ".join(f"TP{i + 1} {_fmt_price(t['price'])} ({t['rr']:.1f}R, {t['size_pct']:.0f}%)"
                        for i, t in enumerate(plan.take_profits))
        lines.append(f"{indent}  {tps}")
    for name, score in sig.checks.items():
        lines.append(f"{indent}    {_bars(score)} {score:>4.2f}  {name}")
    if sig.zones:
        zones = ", ".join(f"{z.label} {_fmt_price(z.low)}-{_fmt_price(z.high)}" for z in sig.zones)
        lines.append(f"{indent}  zones: {zones}")
    if sig.notes:
        lines.append(f"{indent}  note: {sig.notes}")
    lines.append(f"{indent}  bar {sig.end_index} @ {_fmt_time(sig.ts)}")
    return "\n".join(lines)


def render_report(report: ScanReport, cfg: Optional[ScanConfig] = None,
                  top: Optional[int] = None, show_watchlist: bool = True) -> str:
    cfg = cfg or report.config or ScanConfig()
    bias = report.bias()
    arrow = "LONG" if bias["net"] > 0.15 else "SHORT" if bias["net"] < -0.15 else "NEUTRAL"
    out = [BAR,
           f" {report.symbol or 'series'} | {report.timeframe or '?'} | {report.bars} bars "
           f"| net bias {arrow} ({bias['net']:+.2f}) | long {bias['long']:.2f} / short {bias['short']:.2f}",
           BAR]

    actionable = report.actionable()
    watch = report.watchlist()
    if top:
        actionable, watch = actionable[:top], watch[:top]

    out.append(f"\nEXECUTABLE ({len(actionable)}) — breakout or retest already triggered")
    if not actionable:
        out.append("  (nothing triggered; never pre-run a pattern)")
    for sig in actionable:
        out.append(signal_block(sig, cfg, "  "))
        out.append("")

    if show_watchlist:
        out.append(f"WATCHLIST ({len(watch)}) — geometry valid, waiting on the trigger")
        for sig in watch:
            out.append(signal_block(sig, cfg, "  "))
            out.append("")

    if report.errors:
        out.append("detector errors: " + ", ".join(f"{k} ({v})" for k, v in report.errors.items()))
    return "\n".join(out)


def render_multi(multi: MultiReport, cfg: Optional[ScanConfig] = None,
                 top: Optional[int] = 5) -> str:
    out = [BAR, f" {multi.symbol} | multi-timeframe scan | net bias {multi.bias()['net']:+.2f}", BAR]
    for tf, rep in multi.reports.items():
        out.append(render_report(rep, cfg, top=top))
    return "\n".join(out)


def card(spec: PatternSpec) -> str:
    lines = [
        BAR,
        f" {CATEGORY_TITLES[spec.category]} #{spec.number}: {spec.name} ({spec.name_ko})",
        f" {spec.kind}  |  id: {spec.id}",
        BAR,
        f" timeframe : {' - '.join(spec.analysis_tf)}  ->  {' - '.join(spec.entry_tf)}",
        f" hold      : {spec.hold}",
        " key focus :",
    ]
    lines += [f"   - {k}" for k in spec.key_focus]
    lines += [f" principle : {CORE_PRINCIPLES[spec.category]}",
              f" execution : {EXECUTION_RULES[spec.category]}"]
    return "\n".join(lines)


def catalogue(specs: Optional[Iterable[PatternSpec]] = None) -> str:
    specs = list(specs or all_specs())
    out: List[str] = []
    current = None
    for spec in specs:
        if spec.category != current:
            current = spec.category
            out += ["", BAR, f" {CATEGORY_TITLES[current]}", BAR,
                    f" {'#':<3}{'ID':<24}{'PATTERN':<26}{'ENTRY TF':<16}HOLD"]
        out.append(f" {spec.number:<3}{spec.id:<24}{spec.name:<26}"
                   f"{'/'.join(spec.entry_tf):<16}{spec.hold}")
    return "\n".join(out)
