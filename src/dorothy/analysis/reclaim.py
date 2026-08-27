"""슬리피지 스윕 후 되찾기 — 호가가 뚫렸다가 되돌아오는 자리.

호가창이 얇을 때 큰 주문이 들어오면 가격이 여러 호가를 뚫고 내려간다(슬리피지).
그 자리에 다시 유동성이 쌓이고 가격이 원래 자리 위로 돌아오면, 방금 그 하락이
**진짜 매도세가 아니라 유동성 부족**이었다는 뜻이 된다.

OHLC로는 이렇게 번역된다.

    슬리피지        →  긴 아래꼬리 (뚫고 내려갔다가 되돌아옴)
    유동성 유입     →  거래량 급증 (그 자리에 주문이 쌓임)
    시작가 이상 재시작 →  종가가 시가·직전 저점 위로 마감

셋을 동시에 요구한다. 꼬리만 보면 그냥 변동성이고, 거래량만 보면 방향이 없다.

⚠ 이건 봉 안의 순서를 모른다는 한계를 그대로 안는다. 저가를 언제 찍었는지,
   거래량이 그 순간에 몰렸는지는 OHLC로 알 수 없다. 여기서 재는 것은
   "그런 모양의 봉 다음에 무슨 일이 있었나"이지 인과가 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Candle, Side


@dataclass(frozen=True)
class ReclaimSpec:
    """스윕-되찾기 판정 기준."""

    wick_ratio: float = 0.50      # 아래꼬리가 봉 전체 길이의 이 비율 이상
    wick_atr: float = 0.50        # 꼬리 길이가 ATR의 이 배수 이상
    volume_mult: float = 1.5      # 거래량이 최근 평균의 이 배수 이상
    volume_window: int = 50
    require_close_above_open: bool = True   # 시가 위로 마감했는가
    lookback_low: int = 20        # 직전 몇 봉의 저점을 뚫었는지 볼지

    def __post_init__(self) -> None:
        if not 0 < self.wick_ratio < 1:
            raise ValueError("wick_ratio는 0과 1 사이여야 합니다.")
        if self.volume_window < 2:
            raise ValueError("volume_window는 2 이상이어야 합니다.")


@dataclass(frozen=True)
class Reclaim:
    index: int
    side: Side
    wick_ratio: float
    wick_atr: float
    volume_mult: float
    swept_low: float        # 뚫고 내려간 직전 저점 (숏이면 고점)
    close: float


def _wick(candle: Candle, side: Side) -> float:
    """롱 신호는 아래꼬리, 숏 신호는 위꼬리."""
    body_edge = min(candle.open, candle.close) if side is Side.LONG else max(
        candle.open, candle.close)
    return body_edge - candle.low if side is Side.LONG else candle.high - body_edge


def detect(
    candles: list[Candle],
    index: int,
    atr_value: float,
    spec: ReclaimSpec,
    *,
    side: Side = Side.LONG,
) -> Reclaim | None:
    """index 봉이 스윕-되찾기인지 판정한다. index 이후 봉은 보지 않는다."""
    if index < max(spec.volume_window, spec.lookback_low) or index >= len(candles):
        return None
    if atr_value <= 0:
        return None

    candle = candles[index]
    span = candle.high - candle.low
    if span <= 0:
        return None

    wick = _wick(candle, side)
    if wick <= 0:
        return None
    ratio = wick / span
    if ratio < spec.wick_ratio or wick < spec.wick_atr * atr_value:
        return None

    # 시가 위로 마감했는가 — "다시 시작가 이상에서 시작"
    if spec.require_close_above_open:
        reclaimed = (
            candle.close > candle.open if side is Side.LONG
            else candle.close < candle.open
        )
        if not reclaimed:
            return None

    # 직전 저점을 실제로 뚫었는가 (스윕이어야 한다)
    window = candles[index - spec.lookback_low : index]
    if not window:
        return None
    if side is Side.LONG:
        level = min(c.low for c in window)
        if candle.low >= level or candle.close <= level:
            return None      # 뚫지 않았거나, 뚫고 못 돌아왔다
    else:
        level = max(c.high for c in window)
        if candle.high <= level or candle.close >= level:
            return None

    volumes = [c.volume for c in candles[index - spec.volume_window : index]]
    average = sum(volumes) / len(volumes) if volumes else 0.0
    if average <= 0:
        return None
    mult = candle.volume / average
    if mult < spec.volume_mult:
        return None

    return Reclaim(index, side, ratio, wick / atr_value, mult, level, candle.close)
