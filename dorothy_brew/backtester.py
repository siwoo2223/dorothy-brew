"""Event-driven backtester.

Walks the series forward one bar at a time and only ever shows the scanner the
bars that had already closed, so a signal can never see its own outcome. Trades
are managed exactly the way :func:`dorothy_brew.engine.build_plan` proposes
them: risk-based size, staged take-profits, stop to breakeven after the first
target.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .config import ScanConfig
from .core import Series
from .engine import scan
from .registry import REGISTRY
from .signals import LONG, SHORT, Signal

ENTRY_NEXT_OPEN = "next_open"
ENTRY_SIGNAL_CLOSE = "signal_close"


@dataclass
class BacktestConfig:
    initial_equity: float = 10_000.0
    risk_pct: float = 1.0              # % of equity risked per trade
    compound: bool = True              # size off current equity, not the initial
    warmup: int = 150                  # bars before the first scan
    window: int = 320                  # bars handed to the scanner each step
    step: int = 1                      # scan every N bars
    max_open: int = 3                  # concurrent positions
    one_per_pattern: bool = True       # no second trade while one is open
    cooldown_bars: int = 10            # per pattern+direction, after an entry
    allow_shorts: bool = True
    entry_mode: str = ENTRY_NEXT_OPEN  # fill at the next open, or at the signal close
    fee_bps: float = 6.0               # per side, 6 bps ~ Bitget futures taker
    slippage_bps: float = 2.0          # applied to market entries and stop exits
    max_bars_in_trade: int = 150       # time stop
    breakeven_after_first_tp: bool = True
    scale_out: Tuple[float, ...] = (0.5, 0.3, 0.2)
    stop_first: bool = True            # if a bar spans stop and target, assume the stop
    max_notional_mult: float = 5.0     # leverage cap: notional <= equity * this
    min_score: float = 0.0             # ignore signals ranking below this
    min_stop_distance_bps: float = 10.0  # skip setups whose stop is unrealistically tight
    max_entry_drift_r: float = 0.5     # skip the fill if price ran this far (in planned R)


@dataclass
class Fill:
    bar: int
    ts: int
    price: float
    qty: float
    kind: str                          # entry | tp1.. | stop | breakeven | time | eod
    pnl: float = 0.0
    fee: float = 0.0

    def to_dict(self) -> Dict:
        return {"bar": self.bar, "ts": self.ts, "price": round(self.price, 10),
                "qty": round(self.qty, 10), "kind": self.kind,
                "pnl": round(self.pnl, 6), "fee": round(self.fee, 6)}


@dataclass
class Trade:
    pattern_id: str
    direction: str
    status: str
    confidence: float
    score: float
    entry_bar: int
    entry_ts: int
    entry_price: float
    qty: float
    stop: float
    initial_stop: float
    targets: List[float]
    risk_amount: float
    fills: List[Fill] = field(default_factory=list)
    open_qty: float = 0.0
    pnl: float = 0.0
    fees: float = 0.0
    exit_bar: Optional[int] = None
    exit_ts: Optional[int] = None
    reason: str = ""
    hit_targets: int = 0

    @property
    def closed(self) -> bool:
        return self.open_qty <= 1e-12

    @property
    def bars_held(self) -> int:
        return (self.exit_bar if self.exit_bar is not None else self.entry_bar) - self.entry_bar

    @property
    def r_multiple(self) -> float:
        return self.pnl / self.risk_amount if self.risk_amount else 0.0

    def to_dict(self) -> Dict:
        return {"pattern_id": self.pattern_id, "direction": self.direction,
                "status": self.status, "confidence": self.confidence,
                "score": self.score, "entry_bar": self.entry_bar, "entry_ts": self.entry_ts,
                "entry_price": round(self.entry_price, 10), "qty": round(self.qty, 10),
                "stop": round(self.initial_stop, 10), "targets": self.targets,
                "exit_bar": self.exit_bar, "exit_ts": self.exit_ts,
                "bars_held": self.bars_held, "reason": self.reason,
                "hit_targets": self.hit_targets, "pnl": round(self.pnl, 6),
                "fees": round(self.fees, 6), "r_multiple": round(self.r_multiple, 4),
                "fills": [f.to_dict() for f in self.fills]}


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    bars: int
    config: BacktestConfig
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[Tuple[int, float]] = field(default_factory=list)
    signals_seen: int = 0
    signals_taken: int = 0

    # -- statistics ---------------------------------------------------------
    def stats(self) -> Dict[str, float]:
        closed = [t for t in self.trades if t.closed]
        n = len(closed)
        if not n:
            return {"trades": 0, "win_rate": 0.0, "expectancy_r": 0.0,
                    "profit_factor": 0.0, "total_return_pct": 0.0,
                    "max_drawdown_pct": self.max_drawdown_pct(), "avg_bars_held": 0.0,
                    "signals_seen": self.signals_seen, "signals_taken": self.signals_taken}
        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]
        gross_win = sum(t.pnl for t in wins)
        gross_loss = -sum(t.pnl for t in losses)
        equity = self.equity_curve[-1][1] if self.equity_curve else self.config.initial_equity
        rs = [t.r_multiple for t in closed]
        return {
            "trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / n, 4),
            "avg_win_r": round(sum(t.r_multiple for t in wins) / len(wins), 3) if wins else 0.0,
            "avg_loss_r": round(sum(t.r_multiple for t in losses) / len(losses), 3) if losses else 0.0,
            "expectancy_r": round(sum(rs) / n, 4),
            "total_r": round(sum(rs), 3),
            "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else float("inf"),
            "total_return_pct": round((equity / self.config.initial_equity - 1) * 100, 3),
            "max_drawdown_pct": self.max_drawdown_pct(),
            "avg_bars_held": round(sum(t.bars_held for t in closed) / n, 1),
            "best_r": round(max(rs), 3),
            "worst_r": round(min(rs), 3),
            "fees_paid": round(sum(t.fees for t in closed), 4),
            "signals_seen": self.signals_seen,
            "signals_taken": self.signals_taken,
        }

    def max_drawdown_pct(self) -> float:
        peak, worst = self.config.initial_equity, 0.0
        for _, eq in self.equity_curve:
            peak = max(peak, eq)
            if peak > 0:
                worst = min(worst, eq / peak - 1)
        return round(worst * 100, 3)

    def by_pattern(self) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        for trade in self.trades:
            if not trade.closed:
                continue
            row = out.setdefault(trade.pattern_id,
                                 {"trades": 0, "wins": 0, "total_r": 0.0, "pnl": 0.0})
            row["trades"] += 1
            row["wins"] += 1 if trade.pnl > 0 else 0
            row["total_r"] += trade.r_multiple
            row["pnl"] += trade.pnl
        for pid, row in out.items():
            row["win_rate"] = round(row["wins"] / row["trades"], 3)
            row["expectancy_r"] = round(row["total_r"] / row["trades"], 3)
            row["total_r"] = round(row["total_r"], 3)
            row["pnl"] = round(row["pnl"], 4)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["total_r"]))

    def by_direction(self) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        for trade in self.trades:
            if not trade.closed:
                continue
            row = out.setdefault(trade.direction, {"trades": 0, "wins": 0, "total_r": 0.0})
            row["trades"] += 1
            row["wins"] += 1 if trade.pnl > 0 else 0
            row["total_r"] += trade.r_multiple
        for row in out.values():
            row["win_rate"] = round(row["wins"] / row["trades"], 3)
            row["total_r"] = round(row["total_r"], 3)
        return out

    def to_dict(self) -> Dict:
        return {"symbol": self.symbol, "timeframe": self.timeframe, "bars": self.bars,
                "stats": self.stats(), "by_pattern": self.by_pattern(),
                "by_direction": self.by_direction(),
                "equity_curve": [[ts, round(eq, 4)] for ts, eq in self.equity_curve],
                "trades": [t.to_dict() for t in self.trades]}


# ---------------------------------------------------------------------------
# execution helpers
# ---------------------------------------------------------------------------

def _slip(price: float, direction: str, bps: float, adverse: bool = True) -> float:
    """Move a fill price against us by ``bps`` basis points."""
    factor = bps / 10_000.0
    worse_up = (direction == LONG) == adverse
    return price * (1 + factor) if worse_up else price * (1 - factor)


def _weights(targets: Sequence[float], scale_out: Sequence[float]) -> List[float]:
    """Normalised exit weights, one per target, always summing to 1."""
    if not targets:
        return []
    w = list(scale_out[:len(targets)])
    while len(w) < len(targets):
        w.append(0.0)
    total = sum(w)
    if total <= 0:
        return [1.0 / len(targets)] * len(targets)
    remainder = 1.0 - total
    w[-1] += remainder                      # the last target carries the rest
    return w


class _Position:
    """A live trade plus the bookkeeping the loop needs."""

    def __init__(self, trade: Trade, weights: List[float]):
        self.trade = trade
        self.weights = weights
        self.remaining = list(trade.targets)
        self.remaining_weights = list(weights)


def _fillable(sig: Signal, price: float, bt: BacktestConfig) -> bool:
    """Would a desk actually take this fill?

    Two rules keep the simulation honest: a stop that sits a few basis points
    away is an artefact (it would be pure fee drag at absurd leverage), and a
    fill that has already run a large part of the planned risk toward the
    target is a chase, not the trade the pattern proposed.
    """
    if (sig.direction == LONG and sig.stop >= price) or \
       (sig.direction == SHORT and sig.stop <= price):
        return False                                   # stop already on the wrong side
    if abs(price - sig.stop) / price * 10_000 < bt.min_stop_distance_bps:
        return False
    planned_risk = abs(sig.entry - sig.stop)
    if planned_risk > 0 and bt.max_entry_drift_r > 0:
        drift = (price - sig.entry) if sig.direction == LONG else (sig.entry - price)
        if drift / planned_risk > bt.max_entry_drift_r:
            return False
    return not any((t <= price if sig.direction == LONG else t >= price)
                   for t in sig.targets[:1])           # first target already passed
def _open_trade(sig: Signal, bar_index: int, ts: int, price: float,
                equity: float, bt: BacktestConfig) -> Optional[Trade]:
    risk_per_unit = abs(price - sig.stop)
    if risk_per_unit <= 0:
        return None
    base = equity if bt.compound else bt.initial_equity
    risk_amount = base * bt.risk_pct / 100.0
    qty = risk_amount / risk_per_unit
    cap = (base * bt.max_notional_mult) / price if price > 0 else qty
    if qty > cap:                                    # leverage cap shrinks the risk too
        qty = cap
        risk_amount = qty * risk_per_unit
    if qty <= 0:
        return None
    fee = qty * price * bt.fee_bps / 10_000.0
    trade = Trade(pattern_id=sig.pattern_id, direction=sig.direction, status=sig.status,
                  confidence=sig.confidence, score=sig.score(), entry_bar=bar_index,
                  entry_ts=ts, entry_price=price, qty=qty, stop=sig.stop,
                  initial_stop=sig.stop, targets=list(sig.targets),
                  risk_amount=risk_amount, open_qty=qty, fees=fee, pnl=-fee)
    trade.fills.append(Fill(bar_index, ts, price, qty, "entry", -fee, fee))
    return trade


def _close_part(pos: _Position, qty: float, price: float, bar: int, ts: int,
                kind: str, bt: BacktestConfig) -> float:
    trade = pos.trade
    qty = min(qty, trade.open_qty)
    if qty <= 0:
        return 0.0
    sign = 1.0 if trade.direction == LONG else -1.0
    gross = (price - trade.entry_price) * qty * sign
    fee = qty * price * bt.fee_bps / 10_000.0
    net = gross - fee
    trade.open_qty -= qty
    trade.pnl += net
    trade.fees += fee
    trade.fills.append(Fill(bar, ts, price, qty, kind, net, fee))
    if trade.closed:
        trade.exit_bar, trade.exit_ts = bar, ts
        trade.reason = kind
    return net


def _stop_kind(trade: Trade) -> str:
    return "breakeven" if abs(trade.stop - trade.entry_price) < 1e-12 else "stop"


def _stop_price(pos: _Position, candle, bt: BacktestConfig) -> float:
    """Where a stop actually fills — at the open when the bar gapped through it."""
    trade = pos.trade
    long = trade.direction == LONG
    gapped = candle.open <= trade.stop if long else candle.open >= trade.stop
    if gapped:
        return candle.open
    return _slip(trade.stop, trade.direction, bt.slippage_bps, adverse=True)


def _manage(pos: _Position, bar_index: int, series: Series, bt: BacktestConfig) -> float:
    """Walk one bar of an open position. Returns the realised PnL of the bar."""
    trade = pos.trade
    candle = series[bar_index]
    long = trade.direction == LONG
    realised = 0.0

    stop_hit = candle.low <= trade.stop if long else candle.high >= trade.stop
    target_hits = [t for t in pos.remaining
                   if (candle.high >= t if long else candle.low <= t)]

    if stop_hit and (bt.stop_first or not target_hits):
        return realised + _close_part(pos, trade.open_qty, _stop_price(pos, candle, bt),
                                      bar_index, candle.ts, _stop_kind(trade), bt)

    for target in target_hits:
        idx = pos.remaining.index(target)
        weight = pos.remaining_weights.pop(idx)
        pos.remaining.pop(idx)
        qty = trade.qty * weight if pos.remaining else trade.open_qty
        trade.hit_targets += 1
        realised += _close_part(pos, qty, target, bar_index, candle.ts,
                                f"tp{trade.hit_targets}", bt)
        if trade.closed:
            return realised
        if bt.breakeven_after_first_tp and trade.hit_targets == 1:
            trade.stop = trade.entry_price

    if stop_hit and not trade.closed:            # stop taken after the target fills
        realised += _close_part(pos, trade.open_qty, _stop_price(pos, candle, bt),
                                bar_index, candle.ts, _stop_kind(trade), bt)
        return realised

    if not trade.closed and bar_index - trade.entry_bar >= bt.max_bars_in_trade:
        realised += _close_part(pos, trade.open_qty, candle.close, bar_index,
                                candle.ts, "time", bt)
    return realised


def _unrealised(positions: List[_Position], price: float) -> float:
    total = 0.0
    for pos in positions:
        trade = pos.trade
        sign = 1.0 if trade.direction == LONG else -1.0
        total += (price - trade.entry_price) * trade.open_qty * sign
    return total


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------

def backtest(series: Series, scan_cfg: Optional[ScanConfig] = None,
             bt: Optional[BacktestConfig] = None, *,
             patterns: Optional[Sequence[str]] = None,
             categories: Optional[Sequence[str]] = None,
             progress: Optional[callable] = None) -> BacktestResult:
    scan_cfg = scan_cfg or ScanConfig()
    bt = bt or BacktestConfig()
    result = BacktestResult(series.symbol, series.timeframe, len(series), bt)
    if len(series) <= bt.warmup + 2:
        return result

    equity = bt.initial_equity
    positions: List[_Position] = []
    pending: List[Signal] = []
    cooldown: Dict[Tuple[str, str], int] = {}

    for i in range(bt.warmup, len(series)):
        candle = series[i]

        # 1. fill what the previous bar queued, at this bar's open
        for sig in pending:
            price = _slip(candle.open, sig.direction, bt.slippage_bps, adverse=True)
            if not _fillable(sig, price, bt):
                continue
            trade = _open_trade(sig, i, candle.ts, price, equity, bt)
            if trade is None:
                continue
            equity += trade.pnl                      # entry fee
            positions.append(_Position(trade, _weights(trade.targets, bt.scale_out)))
            result.trades.append(trade)
            result.signals_taken += 1
            cooldown[(sig.pattern_id, sig.direction)] = i + bt.cooldown_bars
        pending = []

        # 2. manage open positions on this bar
        for pos in list(positions):
            equity += _manage(pos, i, series, bt)
            if pos.trade.closed:
                positions.remove(pos)

        result.equity_curve.append((candle.ts, equity + _unrealised(positions, candle.close)))

        # 3. scan the closed bar and queue entries for the next open
        if (i - bt.warmup) % bt.step or i >= len(series) - 1:
            continue
        window = series[max(0, i - bt.window + 1): i + 1]
        window.symbol, window.timeframe = series.symbol, series.timeframe
        report = scan(window, scan_cfg, patterns=patterns, categories=categories)
        result.signals_seen += len(report.actionable())

        open_ids = {p.trade.pattern_id for p in positions}
        for sig in report.actionable():
            if len(positions) + len(pending) >= bt.max_open:
                break
            if sig.score() < bt.min_score:
                continue
            if sig.direction == SHORT and not bt.allow_shorts:
                continue
            if bt.one_per_pattern and sig.pattern_id in open_ids:
                continue
            if cooldown.get((sig.pattern_id, sig.direction), -1) > i:
                continue
            if any(p.pattern_id == sig.pattern_id and p.direction == sig.direction
                   for p in pending):
                continue
            if bt.entry_mode == ENTRY_SIGNAL_CLOSE:
                price = _slip(candle.close, sig.direction, bt.slippage_bps, adverse=True)
                if not _fillable(sig, price, bt):
                    continue
                trade = _open_trade(sig, i, candle.ts, price, equity, bt)
                if trade is None:
                    continue
                equity += trade.pnl
                positions.append(_Position(trade, _weights(trade.targets, bt.scale_out)))
                result.trades.append(trade)
                result.signals_taken += 1
                cooldown[(sig.pattern_id, sig.direction)] = i + bt.cooldown_bars
            else:
                pending.append(sig)
        if progress:
            progress(i, len(series), equity)

    # 4. mark anything still open out at the last close
    last = series[-1]
    for pos in positions:
        equity += _close_part(pos, pos.trade.open_qty, last.close, len(series) - 1,
                              last.ts, "eod", bt)
    if result.equity_curve:
        result.equity_curve[-1] = (last.ts, equity)
    return result
