"""타임프레임 변환.

상위 타임프레임으로 방향을 보고 하위에서 진입하는 방식(멀티 타임프레임)에 쓴다.

**미완성 봉을 절대 내보내지 않는다.** 이게 이 모듈의 유일하게 어려운 부분이다.
1시간봉 3개를 모아 4시간봉을 만들면 그 4시간봉은 아직 안 끝났고,
종가가 계속 바뀐다. 그걸 신호에 쓰면 같은 봉에서 신호가 켜졌다 꺼지는
리페인팅이 생긴다 — 백테스트에서만 통하는 전략이 만들어지는 전형적 경로다.
"""

from __future__ import annotations

from ..models import Candle
from .timeframes import TIMEFRAME_MS, timeframe_ms  # noqa: F401  (재수출)



def infer_interval(candles: list[Candle]) -> int:
    """캔들 간격을 추정한다. 결측이 있어도 최빈값이면 대체로 맞는다."""
    if len(candles) < 2:
        return 0
    gaps: dict[int, int] = {}
    for a, b in zip(candles, candles[1:]):
        gap = b.ts - a.ts
        if gap > 0:
            gaps[gap] = gaps.get(gap, 0) + 1
    return max(gaps, key=gaps.get) if gaps else 0


def resample(candles: list[Candle], target_ms: int, *, drop_incomplete: bool = True) -> list[Candle]:
    """상위 타임프레임 캔들로 합친다.

    drop_incomplete=True(기본)면 **마지막 미완성 봉을 버린다.**
    실시간에는 진행 중인 상위 봉의 종가를 알 수 없으므로, 그걸 쓰면 미래참조가 된다.
    """
    if not candles or target_ms <= 0:
        return []

    source_ms = infer_interval(candles)
    if source_ms and target_ms < source_ms:
        raise ValueError(
            f"상위 타임프레임이 원본보다 작습니다 (원본 {source_ms}ms → 목표 {target_ms}ms)"
        )

    # 첫 캔들이 버킷 경계에서 시작하지 않으면 첫 버킷은 앞부분이 잘려 있다.
    # 그대로 두면 첫 상위봉의 시가가 실제와 다르다. 통째로 버린다.
    first_key = candles[0].ts // target_ms * target_ms
    if candles[0].ts != first_key:
        candles = [c for c in candles if c.ts >= first_key + target_ms]
        if not candles:
            return []

    out: list[Candle] = []
    bucket_start: int | None = None
    o = h = l = c = 0.0
    v = 0.0

    for candle in candles:
        key = candle.ts // target_ms * target_ms
        if bucket_start is None:
            bucket_start, o, h, l, c, v = key, candle.open, candle.high, candle.low, candle.close, candle.volume
            continue
        if key != bucket_start:
            out.append(Candle(bucket_start, o, h, l, c, v))
            bucket_start, o, h, l, c, v = key, candle.open, candle.high, candle.low, candle.close, candle.volume
            continue
        h = max(h, candle.high)
        l = min(l, candle.low)
        c = candle.close
        v += candle.volume

    if bucket_start is None:
        return out

    # 마지막 버킷이 꽉 찼는지 확인한다.
    last_ts = candles[-1].ts
    complete = source_ms > 0 and last_ts + source_ms >= bucket_start + target_ms
    if complete or not drop_incomplete:
        out.append(Candle(bucket_start, o, h, l, c, v))
    return out
