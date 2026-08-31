"""겹치는 신호를 독립 표본으로 세지 않는다.

**이 모듈이 막는 사고:**
돌파 전략의 신호는 서로 겹친다. 40봉 고가를 뚫으면 그 다음 몇 봉도
대개 같이 뚫려 있다. 신호마다 '앞으로 60봉'의 결과를 붙여 t값을 내면
**같은 가격 움직임을 여러 번 세게 된다.**

표본 수는 늘어나지만 정보량은 그대로다. t는 √n에 비례하므로 딱 그만큼
부풀려진다. 이 저장소에서 실제로 일어난 일이다:

    291건 겹침 포함  →  1건당 +1.198%  t = 2.67  ✓ 통과
    115건 겹침 제거  →  1건당 +0.734%  t = 0.99  ✗

같은 전략, 같은 데이터, 같은 수수료다. 세는 방법만 달랐다.
그 t=2.87 하나로 "유일하게 검증을 통과한 전략"이라고 적어뒀었다.

봇은 한 번에 포지션 하나만 든다(max_open_positions=1). 그러면 보유 중에
들어온 신호는 **애초에 잡을 수 없다.** 잡을 수 없는 것을 실적에 넣으면
안 된다. drop_concurrent()가 그것을 뺀다.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome:
    """신호 하나와 그 결말.

    entry_index/exit_index는 캔들 색인이다. 두 신호의 구간이 겹치면
    같은 움직임을 두 번 센 것이다.
    """

    entry_index: int
    exit_index: int
    ret: float

    def __post_init__(self) -> None:
        if self.exit_index < self.entry_index:
            raise ValueError(
                f"청산({self.exit_index})이 진입({self.entry_index})보다 앞섭니다."
            )


@dataclass
class EdgeStats:
    """수수료를 뺀 1건당 기대수익과 그 유의성."""

    returns: list[float]
    cost: float

    @property
    def count(self) -> int:
        return len(self.returns)

    @property
    def net(self) -> float:
        """1건당 순수익 (%)."""
        return (statistics.fmean(self.returns) - self.cost) * 100 if self.returns else 0.0

    @property
    def t_stat(self) -> float:
        if len(self.returns) < 3:
            return 0.0
        spread = statistics.stdev(self.returns)
        if spread <= 0:
            return 0.0
        return (statistics.fmean(self.returns) - self.cost) / (
            spread / math.sqrt(len(self.returns))
        )

    @property
    def passes(self) -> bool:
        """돈을 벌면서 우연과도 구별되는가. 둘 다여야 한다."""
        return self.net > 0 and abs(self.t_stat) >= 2.0


def drop_concurrent(outcomes: list[Outcome]) -> list[Outcome]:
    """포지션을 하나만 들 수 있을 때 실제로 잡히는 신호만 남긴다.

    진입 순서대로 훑으면서, 이미 들고 있는 동안 들어온 신호는 버린다.
    실전에서 리스크 매니저가 하는 일과 같다 — 동시 보유 한도에 걸려
    거부되는 신호들이다.
    """
    kept: list[Outcome] = []
    busy_until = -1
    for o in sorted(outcomes, key=lambda x: (x.entry_index, x.exit_index)):
        if o.entry_index <= busy_until:
            continue
        kept.append(o)
        busy_until = o.exit_index
    return kept


@dataclass
class OverlapReport:
    """겹침을 세느냐 마느냐로 결론이 뒤집히는지 보여준다."""

    raw: EdgeStats
    independent: EdgeStats

    @property
    def dropped(self) -> int:
        return self.raw.count - self.independent.count

    @property
    def dropped_pct(self) -> float:
        return self.dropped / self.raw.count * 100 if self.raw.count else 0.0

    @property
    def inflation(self) -> float:
        """겹침을 세면 t가 몇 배로 부풀려지는가 (√n 비율)."""
        if self.independent.count <= 0 or self.raw.count <= 0:
            return 1.0
        return math.sqrt(self.raw.count / self.independent.count)

    @property
    def verdict(self) -> str:
        if self.independent.count < 3:
            return "✗ 겹침을 빼면 표본이 3건 미만입니다 — 판단 불가"
        if self.independent.passes:
            return "✓ 겹침을 빼도 통과합니다"
        if self.raw.passes:
            return (
                f"✗ **겹침 때문에 통과한 것입니다.** 겹침 포함 t={self.raw.t_stat:.2f} → "
                f"제거 t={self.independent.t_stat:.2f}. 잡을 수 없는 신호를 세지 마세요"
            )
        return "✗ 어느 쪽으로 세도 통과하지 못합니다"

    def render(self) -> str:
        lines = [
            "겹치는 신호를 빼면",
            "",
            f"  {'':<16}{'표본':>7}{'1건당':>11}{'t':>8}   판정",
            "  " + "─" * 48,
        ]
        for label, s in (("겹침 포함", self.raw), ("겹침 제거", self.independent)):
            mark = "✓" if s.passes else "✗"
            lines.append(
                f"  {label:<16}{s.count:>7}{s.net:>+10.3f}%{s.t_stat:>8.2f}   {mark}"
            )
        lines += [
            "  " + "─" * 48,
            f"  보유 중이라 잡을 수 없는 신호 {self.dropped}건 ({self.dropped_pct:.0f}%)",
            f"  겹쳐 세면 t가 약 {self.inflation:.2f}배 부풀려집니다",
            "",
            f"  {self.verdict}",
        ]
        return "\n".join(lines)


def analyze(outcomes: list[Outcome], cost: float) -> OverlapReport:
    """겹침 포함/제거 두 가지로 재서 나란히 보여준다.

    cost는 왕복 비용(수수료+슬리피지)을 소수로. 0.0022면 0.22%.
    """
    kept = drop_concurrent(outcomes)
    return OverlapReport(
        raw=EdgeStats([o.ret for o in outcomes], cost),
        independent=EdgeStats([o.ret for o in kept], cost),
    )


def signal_outcomes(
    candles,
    strategy,
    *,
    max_bars: int = 168,
    step: int = 1,
    side_filter=None,
):
    """전략의 진입 신호마다 삼중 장벽 결과를 붙여 Outcome으로 만든다.

    **진입/청산 색인을 같이 들고 나온다.** 수익률만 모으면 어느 신호가
    어느 신호와 겹치는지 알 수 없고, 그러면 겹침을 뺄 방법이 없다.
    이 저장소가 t값을 부풀린 경로가 정확히 그것이었다.
    """
    from ..ml.labeling import triple_barrier
    from ..models import Action, Side

    out: dict[object, list[Outcome]] = {Side.LONG: [], Side.SHORT: []}
    for i in range(strategy.warmup, len(candles) - 1, step):
        signal = strategy.generate(candles[: i + 1], None)
        if signal.action is Action.HOLD or not signal.is_entry:
            continue
        if signal.stop_loss is None or signal.take_profit is None:
            continue
        side = Side.LONG if signal.action is Action.ENTER_LONG else Side.SHORT
        if side_filter is not None and side is not side_filter:
            continue
        barrier = triple_barrier(
            candles, i, side, signal.stop_loss, signal.take_profit, max_bars=max_bars
        )
        if barrier is None:
            continue
        entry = candles[i].close
        exit_price = candles[barrier.exit_index].close
        out[side].append(
            Outcome(i, barrier.exit_index, (exit_price - entry) / entry * side.sign)
        )
    return out
