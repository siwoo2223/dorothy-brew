"""스윙 고점/저점 검출.

피보나치, 엘리엇 파동, ICT 시장구조 — 전부 여기서 출발한다.
스윙 검출이 틀리면 그 위에 쌓은 모든 것이 같이 틀린다.

가장 중요한 것은 **인과성**이다.
차트를 눈으로 보면 고점은 즉시 보이지만, 실시간에서는 그 봉이 고점인지
몇 봉이 더 지나봐야 안다. 이걸 무시하고 "지금 캔들이 고점"이라고 쓰면
백테스트에서만 돈을 버는 전략이 만들어진다.

그래서 모든 Swing은 `confirmed_index`를 들고 다니고,
`as_of` 시점에 확정되지 않은 스윙은 아예 반환하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..models import Candle
from ..data.indicators import atr as atr_indicator


class SwingKind(str, Enum):
    HIGH = "high"
    LOW = "low"

    @property
    def opposite(self) -> "SwingKind":
        return SwingKind.LOW if self is SwingKind.HIGH else SwingKind.HIGH


@dataclass(frozen=True)
class Swing:
    index: int              # 스윙이 실제로 발생한 캔들 인덱스
    ts: int
    price: float
    kind: SwingKind
    confirmed_index: int    # 이 캔들에 와서야 스윙임을 알 수 있다

    @property
    def lag(self) -> int:
        """확정까지 걸린 봉 수. 실시간 전략의 반응 지연이다."""
        return self.confirmed_index - self.index


def find_swings(
    candles: list[Candle],
    *,
    left: int = 2,
    right: int = 2,
    atr_period: int = 14,
    min_atr_mult: float = 0.5,
    as_of: int | None = None,
) -> list[Swing]:
    """프랙탈 기반 스윙 검출 + ATR 진폭 필터 + 고저 교대 정리.

    left/right: 좌우 몇 봉보다 높아야(낮아야) 스윙으로 인정할지.
        right가 클수록 노이즈가 줄지만 확정이 늦어진다. 이 트레이드오프는 피할 수 없다.
    min_atr_mult: 직전 스윙 대비 최소 진폭 (ATR 배수). 잔파동을 걸러낸다.
    as_of: 이 인덱스 시점에서 알 수 있는 스윙만 반환한다 (기본값: 마지막 캔들).
    """
    n = len(candles)
    if n < left + right + 1:
        return []

    limit = n - 1 if as_of is None else min(as_of, n - 1)
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    atr_line = atr_indicator(highs, lows, [c.close for c in candles], atr_period)

    # --- 1단계: 프랙탈 후보 수집 ---
    candidates: list[Swing] = []
    for i in range(left, n - right):
        confirmed = i + right
        if confirmed > limit:
            break   # 아직 확정되지 않은 스윙은 존재하지 않는 것으로 취급한다

        window = slice(i - left, i + right + 1)
        if highs[i] == max(highs[window]) and highs[i] > max(
            highs[i - left : i], default=float("-inf")
        ):
            candidates.append(Swing(i, candles[i].ts, highs[i], SwingKind.HIGH, confirmed))
        if lows[i] == min(lows[window]) and lows[i] < min(
            lows[i - left : i], default=float("inf")
        ):
            candidates.append(Swing(i, candles[i].ts, lows[i], SwingKind.LOW, confirmed))

    if not candidates:
        return []

    # 확정 순서대로 처리해야 인과성이 유지된다 (발생 순서가 아니라)
    candidates.sort(key=lambda s: (s.confirmed_index, s.index))

    # --- 2단계: 고↔저 교대 정리 + 진폭 필터 ---
    result: list[Swing] = []
    for cand in candidates:
        threshold = _amplitude_threshold(atr_line, cand.index, min_atr_mult)

        if not result:
            result.append(cand)
            continue

        last = result[-1]
        if cand.kind is last.kind:
            # 같은 종류가 연달아 나오면 더 극단적인 쪽만 남긴다
            more_extreme = (
                cand.price > last.price if cand.kind is SwingKind.HIGH else cand.price < last.price
            )
            if more_extreme:
                result[-1] = cand
            continue

        # 종류가 바뀌었다 = 새 스윙. 진폭이 충분한지 확인
        if abs(cand.price - last.price) < threshold:
            continue
        result.append(cand)

    return result


def _amplitude_threshold(atr_line: list[float | None], index: int, mult: float) -> float:
    value = atr_line[index] if index < len(atr_line) else None
    if value is None:
        # ATR이 아직 없는 초반 구간에서는 진폭 필터를 걸지 않는다
        return 0.0
    return value * mult


def last_leg(swings: list[Swing]) -> tuple[Swing, Swing] | None:
    """가장 최근에 완성된 스윙 구간(저→고 또는 고→저)."""
    if len(swings) < 2:
        return None
    return swings[-2], swings[-1]


def recent(swings: list[Swing], kind: SwingKind, count: int = 1) -> list[Swing]:
    """특정 종류의 최근 스윙들 (최신순)."""
    return [s for s in reversed(swings) if s.kind is kind][:count]
