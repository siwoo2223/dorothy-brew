"""삼중 배리어 라벨링.

"N봉 뒤에 올랐나?"로 라벨을 만들면 실제 매매와 어긋난다. 실전에서는 목표가·손절가·
시간 중 **먼저 닿는 것**이 결과를 정한다. 삼중 배리어는 그 규칙을 그대로 라벨로 쓴다.

메타라벨링에서는 라벨이 방향이 아니라 **"이 신호를 잡았어야 했나"**다.
1차 전략이 방향을 정하고, 모델은 그 신호를 취할지 말지만 배운다.
방향 예측(정확도 상한 52~55%)보다 훨씬 쉬운 문제이고, 이미 있는 전략을
버리지 않고 개선한다는 점에서 실용적이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Candle, Side


@dataclass(frozen=True)
class BarrierOutcome:
    label: int          # 1 = 목표 도달(잡았어야 함), 0 = 손절 또는 미달
    exit_index: int     # 결과가 확정된 봉
    reason: str


def triple_barrier(
    candles: list[Candle],
    entry_index: int,
    side: Side,
    stop: float,
    target: float,
    *,
    max_bars: int = 168,
    include_entry_bar: bool = False,
) -> BarrierOutcome | None:
    """진입 이후 목표·손절·시간 중 먼저 닿는 것으로 라벨을 정한다.

    한 봉 안에서 목표와 손절이 모두 닿으면 **손절을 택한다**(보수적 가정).
    백테스트 엔진의 스탑 판정과 같은 규칙이라 라벨과 실제 성과가 어긋나지 않는다.

    include_entry_bar는 지정가 체결에 쓴다. 봉 중간에 체결됐다면 그 봉의 남은
    구간에서도 손절이 나갈 수 있다. 다음 봉부터 보면 그만큼 유리하게 계산된다.
    """
    if entry_index >= len(candles) - 1:
        return None

    last = min(entry_index + max_bars, len(candles) - 1)
    first = entry_index if include_entry_bar else entry_index + 1
    for i in range(first, last + 1):
        candle = candles[i]
        if side is Side.LONG:
            hit_stop = candle.low <= stop
            hit_target = candle.high >= target
        else:
            hit_stop = candle.high >= stop
            hit_target = candle.low <= target

        if hit_stop:
            return BarrierOutcome(0, i, "stop")
        if hit_target:
            return BarrierOutcome(1, i, "target")

    # 시간 초과 — 방향이 맞았는지로 판정한다
    entry_price = candles[entry_index].close
    final = candles[last].close
    gain = (final - entry_price) * side.sign
    return BarrierOutcome(1 if gain > 0 else 0, last, "timeout")


@dataclass
class Sample:
    """학습 표본 하나. span은 누수 방지(purging)에 쓴다."""

    index: int          # 신호 발생 봉
    exit_index: int     # 라벨이 확정된 봉
    ts: int
    features: list[float]
    label: int
    side: Side
    # 손절·익절 절대가. 체결가를 바꿔가며 다시 태우려면 이게 있어야 한다
    # (지정가 진입은 체결가가 신호봉 종가와 다르다).
    stop: float = 0.0
    target: float = 0.0

    @property
    def span(self) -> tuple[int, int]:
        return self.index, self.exit_index
