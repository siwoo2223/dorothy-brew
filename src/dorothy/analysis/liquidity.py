"""ICT 유동성 개념 구현.

네 가지 요소 중 **가장 코드로 옮기기 좋은 부분**이다.
"세력이 어쩐다"는 서사를 걷어내면 남는 것은 관찰 가능한 가격 패턴이고,
그건 전부 정의가 명확하다:

- 유동성 풀 (Liquidity Pool): 손절이 몰려 있을 법한 자리
  = 등가 고점/저점(EQH/EQL), 전일 고저(PDH/PDL), 직전 스윙 고저
- 스윕 (Sweep/Raid): 그 자리를 꼬리로 뚫고 되돌아온 캔들
- FVG (Fair Value Gap): 급격한 변위가 남긴 3봉 갭
- 시장구조 전환 (BOS/CHoCH): 직전 스윙을 종가로 돌파했는가

여기서 나오는 값은 전부 '지금까지의 캔들'만으로 계산된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..models import Candle
from .fibonacci import Zone
from .swings import Swing, SwingKind


class PoolKind(str, Enum):
    BUY_SIDE = "buy_side"     # 고점 위 = 매수 손절/역지정이 쌓인 곳
    SELL_SIDE = "sell_side"   # 저점 아래 = 매도 손절이 쌓인 곳


class StructureEvent(str, Enum):
    BOS = "bos"       # Break of Structure — 추세 지속
    CHOCH = "choch"   # Change of Character — 추세 전환 후보
    NONE = "none"


class Bias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"

    @property
    def opposite(self) -> "Bias":
        if self is Bias.BULLISH:
            return Bias.BEARISH
        if self is Bias.BEARISH:
            return Bias.BULLISH
        return Bias.NEUTRAL


@dataclass(frozen=True)
class Pool:
    """유동성 풀. touches가 많을수록 '누구나 보는 자리' = 유동성이 두텁다."""

    price: float
    kind: PoolKind
    touches: int
    first_index: int
    last_index: int
    label: str = ""

    @property
    def is_equal_level(self) -> bool:
        """등가 고점/저점(EQH/EQL). 두 번 이상 같은 자리를 때렸다."""
        return self.touches >= 2


@dataclass(frozen=True)
class Sweep:
    """유동성 스윕. 풀을 뚫었다가 되돌아온 사건."""

    index: int
    ts: int
    pool: Pool
    extreme: float        # 꼬리가 닿은 최극단 (손절을 여기 밖에 둬야 한다)
    close: float
    penetration: float    # 얼마나 깊이 뚫었나 (ATR 배수)

    @property
    def direction(self) -> Bias:
        """매도측 스윕 = 아래 손절을 털었다 = 상방 편향."""
        return Bias.BULLISH if self.pool.kind is PoolKind.SELL_SIDE else Bias.BEARISH


@dataclass(frozen=True)
class FVG:
    """Fair Value Gap — 3봉 구간에 생긴 미체결 갭.

    가격이 이 구간을 다시 채우러 오는 경향이 있어 되돌림 진입 자리로 쓴다.
    """

    index: int            # 갭 한가운데 봉
    confirmed_index: int  # 세 번째 봉에서 확정된다
    zone: Zone
    direction: Bias
    atr_size: float       # 갭 크기 (ATR 배수). 클수록 강한 변위

    def is_filled(self, candles: list[Candle], upto: int) -> bool:
        """확정 이후 가격이 갭을 다 메웠는가."""
        for c in candles[self.confirmed_index + 1 : upto + 1]:
            if self.direction is Bias.BULLISH and c.low <= self.zone.low:
                return True
            if self.direction is Bias.BEARISH and c.high >= self.zone.high:
                return True
        return False


@dataclass(frozen=True)
class Structure:
    """시장구조 판정 결과."""

    bias: Bias
    event: StructureEvent
    event_index: int
    broken_level: float
    reference_swing: Swing | None = None


# --------------------------------------------------------------------------
# 유동성 풀
# --------------------------------------------------------------------------
def find_pools(
    swings: list[Swing],
    *,
    atr: float,
    tolerance_mult: float = 0.15,
    min_touches: int = 1,
) -> list[Pool]:
    """스윙 고점/저점을 묶어 유동성 풀을 만든다.

    같은 레벨(ATR 허용오차 안)에 여러 스윙이 모이면 등가 고점/저점(EQH/EQL)이고,
    그만큼 손절이 두텁게 쌓여 있다고 본다.
    """
    pools: list[Pool] = []
    for kind, pool_kind in ((SwingKind.HIGH, PoolKind.BUY_SIDE), (SwingKind.LOW, PoolKind.SELL_SIDE)):
        group = [s for s in swings if s.kind is kind]
        used: set[int] = set()
        for i, swing in enumerate(group):
            if i in used:
                continue
            cluster = [swing]
            used.add(i)
            for j in range(i + 1, len(group)):
                if j in used:
                    continue
                if abs(group[j].price - swing.price) <= atr * tolerance_mult:
                    cluster.append(group[j])
                    used.add(j)
            if len(cluster) < min_touches:
                continue
            # 풀 가격은 가장 극단값으로 잡는다. 스윕 판정은 이 선을 넘어야 성립한다.
            price = max(s.price for s in cluster) if kind is SwingKind.HIGH else min(
                s.price for s in cluster
            )
            pools.append(
                Pool(
                    price=price,
                    kind=pool_kind,
                    touches=len(cluster),
                    first_index=min(s.index for s in cluster),
                    last_index=max(s.index for s in cluster),
                    label="EQH" if (len(cluster) >= 2 and kind is SwingKind.HIGH)
                    else "EQL" if len(cluster) >= 2
                    else ("고점" if kind is SwingKind.HIGH else "저점"),
                )
            )
    pools.sort(key=lambda p: p.last_index)
    return pools


def session_pools(candles: list[Candle], *, upto: int, lookback_bars: int) -> list[Pool]:
    """직전 구간의 고가/저가 (전일 고저 PDH/PDL 대용).

    타임프레임에 맞춰 lookback_bars를 정한다.
    예: 15분봉에서 하루 = 96봉.
    """
    end = upto - lookback_bars
    start = end - lookback_bars
    if start < 0:
        return []
    window = candles[start:end]
    if not window:
        return []
    hi = max(c.high for c in window)
    lo = min(c.low for c in window)
    return [
        Pool(hi, PoolKind.BUY_SIDE, 1, start, end - 1, "직전구간 고가"),
        Pool(lo, PoolKind.SELL_SIDE, 1, start, end - 1, "직전구간 저가"),
    ]


# --------------------------------------------------------------------------
# 스윕
# --------------------------------------------------------------------------
def detect_sweep(
    candles: list[Candle],
    pools: list[Pool],
    *,
    index: int,
    atr: float,
    min_penetration: float = 0.05,
    max_lookback: int = 3,
) -> Sweep | None:
    """`index` 캔들이 유동성 풀을 스윕했는지 판정한다.

    성립 조건:
      1. 꼬리가 풀 가격을 명확히 뚫었다 (ATR × min_penetration 이상)
      2. 종가는 풀 안쪽으로 되돌아왔다  ← 이게 핵심. 뚫고 못 돌아오면 그냥 돌파다
      3. 풀이 이 캔들보다 과거에 형성되었다

    max_lookback: 스윕 캔들 여러 개에 걸쳐 일어난 경우도 잡기 위해
    최근 몇 봉의 극단값까지 함께 본다.
    """
    if index >= len(candles) or atr <= 0:
        return None

    candle = candles[index]
    start = max(0, index - max_lookback + 1)
    window = candles[start : index + 1]
    window_high = max(c.high for c in window)
    window_low = min(c.low for c in window)

    best: Sweep | None = None
    for pool in pools:
        if pool.last_index >= index:
            continue   # 아직 형성되지 않은 풀은 스윕 대상이 아니다

        if pool.kind is PoolKind.SELL_SIDE:
            depth = pool.price - window_low
            if depth < atr * min_penetration:
                continue
            if candle.close <= pool.price:
                continue   # 되돌아오지 못했다 = 스윕이 아니라 하방 돌파
            extreme = window_low
        else:
            depth = window_high - pool.price
            if depth < atr * min_penetration:
                continue
            if candle.close >= pool.price:
                continue
            extreme = window_high

        sweep = Sweep(index, candle.ts, pool, extreme, candle.close, depth / atr)
        # 더 두터운 풀(터치 수 많음)을, 같으면 더 깊이 뚫은 쪽을 택한다
        if best is None or (sweep.pool.touches, sweep.penetration) > (
            best.pool.touches, best.penetration
        ):
            best = sweep
    return best


# --------------------------------------------------------------------------
# FVG
# --------------------------------------------------------------------------
def find_fvgs(
    candles: list[Candle], *, atr: float, min_size_mult: float = 0.1, upto: int | None = None
) -> list[FVG]:
    """3봉 패턴으로 갭을 찾는다.

    상승 FVG: candles[i-1].high < candles[i+1].low  →  그 사이가 빈 구간
    하락 FVG: candles[i-1].low  > candles[i+1].high
    """
    end = len(candles) - 1 if upto is None else min(upto, len(candles) - 1)
    out: list[FVG] = []
    if atr <= 0:
        return out

    for i in range(1, end):
        prev, nxt = candles[i - 1], candles[i + 1]
        if nxt.low > prev.high:
            size = nxt.low - prev.high
            if size >= atr * min_size_mult:
                out.append(
                    FVG(i, i + 1, Zone(prev.high, nxt.low, "상승 FVG"), Bias.BULLISH, size / atr)
                )
        elif prev.low > nxt.high:
            size = prev.low - nxt.high
            if size >= atr * min_size_mult:
                out.append(
                    FVG(i, i + 1, Zone(nxt.high, prev.low, "하락 FVG"), Bias.BEARISH, size / atr)
                )
    return out


# --------------------------------------------------------------------------
# 시장구조
# --------------------------------------------------------------------------
def market_structure(candles: list[Candle], swings: list[Swing], *, upto: int) -> Structure:
    """스윙 고저의 갱신 패턴으로 추세와 구조 전환을 판정한다.

    상승추세 = 고점·저점 모두 높아짐(HH/HL)
    BOS   = 추세 방향으로 직전 스윙을 종가 돌파 (지속)
    CHoCH = 추세 반대로 직전 스윙을 종가 돌파 (전환 후보)  ← ICT 진입의 방아쇠
    """
    highs = [s for s in swings if s.kind is SwingKind.HIGH and s.confirmed_index <= upto]
    lows = [s for s in swings if s.kind is SwingKind.LOW and s.confirmed_index <= upto]
    if len(highs) < 2 or len(lows) < 2:
        return Structure(Bias.NEUTRAL, StructureEvent.NONE, upto, 0.0)

    higher_high = highs[-1].price > highs[-2].price
    higher_low = lows[-1].price > lows[-2].price
    if higher_high and higher_low:
        bias = Bias.BULLISH
    elif not higher_high and not higher_low:
        bias = Bias.BEARISH
    else:
        bias = Bias.NEUTRAL

    last_high, last_low = highs[-1], lows[-1]
    close = candles[upto].close

    # 종가 기준으로 판정한다. 꼬리 돌파는 스윕이지 구조 전환이 아니다.
    if close > last_high.price and last_high.confirmed_index <= upto:
        event = StructureEvent.BOS if bias is Bias.BULLISH else StructureEvent.CHOCH
        return Structure(Bias.BULLISH, event, upto, last_high.price, last_high)
    if close < last_low.price and last_low.confirmed_index <= upto:
        event = StructureEvent.BOS if bias is Bias.BEARISH else StructureEvent.CHOCH
        return Structure(Bias.BEARISH, event, upto, last_low.price, last_low)

    return Structure(bias, StructureEvent.NONE, upto, 0.0)
