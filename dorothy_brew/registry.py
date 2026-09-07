"""Pattern catalogue.

Every entry mirrors one row of the source playbooks: analysis timeframe,
entry timeframe, hold duration and the key-focus checklist. Detectors register
themselves against a spec so the engine only ever deals with metadata + a
callable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional

CLASSIC = "classic"
SMC = "smart_money"
HARMONIC = "harmonic"

CATEGORY_TITLES = {
    CLASSIC: "Classic Chart Patterns",
    SMC: "Smart Money Concepts",
    HARMONIC: "Harmonic & Fibonacci",
}


@dataclass
class PatternSpec:
    id: str
    number: int
    name: str
    name_ko: str
    kind: str                       # human label, e.g. "Bullish Continuation"
    category: str
    analysis_tf: List[str]          # higher timeframes used to qualify the setup
    entry_tf: List[str]             # timeframes used to time the trigger
    hold: str                       # expected holding period
    key_focus: List[str]            # the checklist printed on the playbook card
    detector: Optional[Callable] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d.pop("detector", None)
        d["category_title"] = CATEGORY_TITLES[self.category]
        return d


REGISTRY: Dict[str, PatternSpec] = {}


def register(spec: PatternSpec) -> Callable:
    """Decorator: attach a detector function to a spec and publish it."""
    def wrap(fn: Callable) -> Callable:
        spec.detector = fn
        REGISTRY[spec.id] = spec
        fn.spec = spec
        return fn
    return wrap


def get(pattern_id: str) -> PatternSpec:
    return REGISTRY[pattern_id]


def all_specs(category: Optional[str] = None) -> List[PatternSpec]:
    specs = [s for s in REGISTRY.values() if category is None or s.category == category]
    return sorted(specs, key=lambda s: (s.category, s.number))


# ---------------------------------------------------------------------------
# 1. Classic chart patterns — "Trade the Structure, Not the Noise"
# ---------------------------------------------------------------------------

BULL_FLAG = PatternSpec(
    "bull_flag", 1, "Bull Flag", "불 플래그", "Bullish Continuation", CLASSIC,
    ["4H", "1D"], ["15m", "1H"], "Hours - Days",
    ["Strong pole momentum", "Falling volume in flag", "Breakout with volume",
     "Measured move target"])

HEAD_SHOULDERS = PatternSpec(
    "head_shoulders", 2, "Head & Shoulders", "헤드앤숄더", "Bearish Reversal", CLASSIC,
    ["1D", "1W"], ["1H", "4H"], "Days - Weeks",
    ["High-volume head", "Decreasing right shoulder", "Neckline retest entry",
     "Structural reversal"])

ASCENDING_TRIANGLE = PatternSpec(
    "ascending_triangle", 3, "Ascending Triangle", "상승 삼각형", "Bullish Continuation", CLASSIC,
    ["4H", "1D"], ["15m", "1H"], "Hours - Days",
    ["Continuous higher lows", "Horizontal liquidity test", "Clean close above resistance",
     "Expansion phase"])

RECTANGLE_BREAKOUT = PatternSpec(
    "rectangle_breakout", 4, "Rectangle Breakout", "박스권 돌파", "Range Expansion", CLASSIC,
    ["1H", "4H"], ["5m", "15m"], "Min - Hours",
    ["Range boundary rejection", "Volatility contraction", "Full candle close outside",
     "Target opposite width"])

CUP_HANDLE = PatternSpec(
    "cup_handle", 5, "Cup and Handle", "컵앤핸들", "Bullish Continuation", CLASSIC,
    ["1D", "1W"], ["4H", "1D"], "Weeks - Months",
    ["Smooth rounded bottom", "Shallow handle pullback", "Volume dry-up in handle",
     "Macro breakout signal"])

ENDING_WEDGE = PatternSpec(
    "ending_wedge", 6, "Ending Wedge", "엔딩 웨지", "Bearish/Bullish Reversal", CLASSIC,
    ["4H", "1D"], ["15m", "1H"], "Hours - Days",
    ["Bearish/Bullish divergence", "Trendline breakdown", "Sharp counter-strike",
     "Reversal target base"])

DOUBLE_BOTTOM = PatternSpec(
    "double_bottom", 7, "Double Bottom", "쌍바닥", "Bullish Reversal", CLASSIC,
    ["1H", "4H"], ["15m", "1H"], "Hours - Days",
    ["Second leg rejection", "Bullish divergence", "Neckline confirmation",
     "Height projection TP"])

DIAMOND_TOP = PatternSpec(
    "diamond_top", 8, "Diamond Top", "다이아몬드 탑", "Bearish Reversal", CLASSIC,
    ["4H", "1D"], ["1H", "4H"], "Days - Weeks",
    ["High volatility expansion", "Loss of buyer control", "Lower boundary breach",
     "High-conviction short"])

TRIPLE_TOUCH_CHANNEL = PatternSpec(
    "triple_touch_channel", 9, "Triple Touch Channel", "트리플 터치 채널", "Bullish Continuation", CLASSIC,
    ["1D", "1W"], ["1H", "4H"], "Days - Weeks",
    ["Mid-line acceptance", "Boundary confluence", "Bullish engulfing trigger",
     "Target upper channel"])

BROADENING_WEDGE = PatternSpec(
    "broadening_wedge", 10, "Broadening Wedge", "확장형 웨지", "Volatility Expansion", CLASSIC,
    ["1H", "4H"], ["5m", "15m"], "Min - Hours",
    ["Extreme emotion trap", "Liquidity sweep both sides", "Structural reclamation",
     "Counter-momentum move"])

# ---------------------------------------------------------------------------
# 2. Smart money concepts — "liquidity, structure and order flow"
# ---------------------------------------------------------------------------

LIQUIDITY_SWEEP = PatternSpec(
    "liquidity_sweep", 1, "Liquidity Sweep", "유동성 스윕", "Liquidity Grab", SMC,
    ["1H", "4H"], ["1m", "5m", "15m"], "Seconds - Minutes",
    ["Sweep high/low points", "Quick reclaim (entry)", "Stop below/above wick",
     "Liquidity zones"])

CHOCH = PatternSpec(
    "choch", 2, "Change of Character", "캐릭터 전환(CHoCH)", "Structure Shift", SMC,
    ["4H", "1D"], ["15m", "30m", "1H"], "Hours - Days",
    ["Key pivot break", "FVG/OB retest", "Trend direction", "Hold to liquidity zone"])

ORDER_BLOCK = PatternSpec(
    "order_block", 3, "Order Block", "오더블록", "Institutional Order Block", SMC,
    ["1D"], ["1H", "2H", "4H"], "Days - Weeks",
    ["Last opposite candle", "First touch = rebound", "Close below = stop",
     "Follow higher time trend"])

FAIR_VALUE_GAP = PatternSpec(
    "fair_value_gap", 4, "Fair Value Gap", "FVG (공정가치 갭)", "Imbalance", SMC,
    ["4H"], ["5m", "15m", "30m", "1H"], "Minutes - Hours",
    ["3-candle gap", "50% fill entry (CE)", "Target previous high/low",
     "Follow trend direction"])

SUPPLY_DEMAND_FLIP = PatternSpec(
    "supply_demand_flip", 5, "Supply/Demand Flip", "수급 전환(존 플립)", "Zone Flip", SMC,
    ["4H"], ["15m", "1H"], "Hours - Days",
    ["Zone flip confirmation", "Retest entry", "Follow trend", "Invalidation stop"])

BOS = PatternSpec(
    "bos", 6, "Break of Structure", "구조 돌파(BOS)", "Trend Continuation", SMC,
    ["4H", "1D"], ["15m", "1H"], "Hours - Days",
    ["Strong candle close", "Volume confirmation", "Trend continuation", "Pullback entry"])

PREMIUM_DISCOUNT = PatternSpec(
    "premium_discount", 7, "Premium & Discount", "프리미엄/디스카운트", "Fibonacci Zone", SMC,
    ["4H", "1D"], ["15m", "1H"], "Hours - Days",
    ["Trade discount zone", "Trade premium zone", "Midline (0.5)", "Follow trend"])

EQUAL_HIGHS_LOWS = PatternSpec(
    "equal_highs_lows", 8, "Equal Highs/Lows", "동일 고점/저점", "Liquidity Pool", SMC,
    ["4H"], ["15m", "30m", "1H"], "Minutes - Hours",
    ["Liquidity pool", "False breakout", "Reversal entry", "Trend direction"])

INSTITUTIONAL_ENGULFING = PatternSpec(
    "institutional_engulfing", 9, "Institutional Engulfing", "기관 장악형 캔들", "Engulfing Candle", SMC,
    ["4H"], ["15m", "30m", "1H"], "Minutes - Hours",
    ["Strong body", "Rejection wick", "Key level", "Follow trend"])

LIQUIDITY_COMPRESSION = PatternSpec(
    "liquidity_compression", 10, "Liquidity Compression", "유동성 압축(스퀴즈)", "Squeeze", SMC,
    ["4H", "1D"], ["15m", "30m", "1H"], "Hours - Days",
    ["Tight range", "Volume build-up", "Breakout direction", "Fade / reversal"])

# ---------------------------------------------------------------------------
# 3. Harmonic & Fibonacci — "Precision Ratios. High-Probability Setups."
# ---------------------------------------------------------------------------

GOLDEN_RETRACEMENT = PatternSpec(
    "golden_retracement", 1, "Golden Retracement", "황금 되돌림 (61.8%)", "61.8% Pullback", HARMONIC,
    ["4H", "1D"], ["15m", "1H"], "Hours - Days",
    ["Fib 0.618 / 0.65 zone", "Confluence with S/R", "Candlestick rejection",
     "Target 1.618 extension"])

BAT = PatternSpec(
    "bat", 2, "Bat Pattern", "배트 패턴", "Harmonic Reversal", HARMONIC,
    ["1D", "1W"], ["1H", "4H"], "Days - Weeks",
    ["Precise 0.886 XA completion", "Tight PRZ alignment", "Low risk high R:R",
     "Target 0.382 CD wave"])

BUTTERFLY = PatternSpec(
    "butterfly", 3, "Butterfly Pattern", "버터플라이 패턴", "Harmonic Reversal", HARMONIC,
    ["4H", "1D"], ["15m", "1H"], "Hours - Days",
    ["D point extension 1.272-1.414", "Deep XA extension", "Structural reversal zone",
     "Target B point level"])

CRAB = PatternSpec(
    "crab", 4, "Crab Pattern", "크랩 패턴", "Extreme Reversal", HARMONIC,
    ["1D", "1W"], ["1H", "4H"], "Days - Weeks",
    ["D point 1.618 extension", "High-volume exhaustion", "Tight stop above D",
     "Aggressive mean-reversion"])

ABCD = PatternSpec(
    "abcd", 5, "AB=CD Harmonic", "AB=CD 하모닉", "Symmetrical Move", HARMONIC,
    ["1H", "4H"], ["15m", "1H"], "Hours - Days",
    ["1:1 time & price confluence", "BC 0.618/0.786 retracement", "Symmetric leg completion",
     "Clean structural entry"])

FIB_EXTENSION = PatternSpec(
    "fib_extension", 6, "Fibonacci Extension", "피보나치 확장 (1.618)", "1.618 Exit", HARMONIC,
    ["4H", "1D"], ["15m", "1H"], "Hours - Days",
    ["Trend expansion target", "Over-bought boundary", "Exhaustion candle test",
     "Profit lock-in zone"])

DEEP_CRAB = PatternSpec(
    "deep_crab", 7, "Deep Crab 886", "딥 크랩 886", "Gartley Reinforcement", HARMONIC,
    ["4H", "1D"], ["15m", "1H"], "Hours - Days",
    ["Gartley 0.786 PRZ", "Market structure confluence", "Clear stop beyond X",
     "High-probability bounce"])

FIB_CLUSTER = PatternSpec(
    "fib_cluster", 8, "Fib Cluster Zone", "피보나치 클러스터", "Confluence Zone", HARMONIC,
    ["1D", "1W"], ["1H", "4H"], "Days - Weeks",
    ["Multi-wave confluence", "High-conviction node", "Institutional barrier",
     "Position sizing boost"])

SHARK = PatternSpec(
    "shark", 9, "Shark Pattern", "샤크 패턴", "Sharp Reversal", HARMONIC,
    ["1H", "4H"], ["5m", "15m"], "Min - Hours",
    ["Failed breakout trigger", "Rapid PRZ response", "Strong 50% Fib target",
     "High velocity scalp"])

FIB_TREND_FAN = PatternSpec(
    "fib_trend_fan", 10, "Fib Trend Fan", "피보나치 팬", "Dynamic Support", HARMONIC,
    ["1D", "1W"], ["4H", "1D"], "Weeks - Months",
    ["Geometric trendline", "Dynamic angle support", "Trend acceleration",
     "Trailing stop anchor"])


CORE_PRINCIPLES = {
    CLASSIC: ("Patterns reflect psychology: geometrical structures are the visual "
              "footprint of institutional accumulation and distribution."),
    SMC: ("Smart money doesn't predict the market - it uses liquidity, structure "
          "and order flow to find high-probability entries."),
    HARMONIC: ("Mathematics drives markets: price waves oscillate in precise "
               "mathematical ratios governed by human emotion."),
}

EXECUTION_RULES = {
    CLASSIC: "Wait for confirmation: never pre-run a pattern; execute only upon structural breakout or retest.",
    SMC: "Follow the process: mark liquidity and structure first, execute only on the reaction.",
    HARMONIC: "Respect Potential Reversal Zones: only trade harmonics when price reaches the exact PRZ boundary.",
}
