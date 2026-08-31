"""여러 설정을 훑어 겹침을 뺀 우위를 찾는다 — 자기기만 방지 포함.

탐색은 위험하다. 많이 재면 우연히 좋아 보이는 것이 반드시 나온다.
그래서 이 모듈은 **탐색과 판정을 분리한다.**

  1. 데이터를 탐색기간/검증기간으로 나눈다. 검증기간은 건드리지 않는다.
  2. 탐색기간에서만 훑는다. 몇 번 쟀는지 센다.
  3. 본페로니로 보정한 임계값을 넘는 것만 후보로 올린다.
  4. 후보를 **처음 보는 검증기간**에 던진다.
  5. 통과 개수를 우연 기대치와 비교한다.

3번을 빼면 100개를 재서 5개쯤 나오는 것을 발견이라 부르게 된다.
4번을 빼면 탐색기간에 맞춘 것을 실력이라 부르게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .concurrency import EdgeStats, drop_concurrent, signal_outcomes
from .multiple_testing import bonferroni_threshold, expected_false_positives, verdict


def _fit(label: str, width: int) -> str:
    """길면 **가운데를 줄인다.** 뒤를 자르면 설정끼리 구분되는 부분이
    통째로 사라져서(파라미터가 대개 뒤에 온다) 표를 읽을 수 없게 된다."""
    if len(label) <= width:
        return label
    head = (width - 1) // 2
    tail = width - 1 - head
    return label[:head] + "…" + label[len(label) - tail:]


@dataclass(frozen=True)
class Candidate:
    """재볼 설정 하나."""

    strategy: str
    timeframe: str
    params: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        bits = " ".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.strategy} {self.timeframe} {bits}".strip()


@dataclass
class Trial:
    """설정 하나의 탐색기간/검증기간 성적."""

    candidate: Candidate
    in_sample: EdgeStats
    out_of_sample: EdgeStats

    @property
    def confirmed(self) -> bool:
        """검증기간에서도 돈을 벌고 우연과 구별되는가."""
        return self.out_of_sample.passes


@dataclass
class SearchReport:
    trials: list[Trial]
    alpha: float = 0.05
    min_samples: int = 30

    @property
    def tested(self) -> int:
        """서로 다른 검정의 개수.

        결과가 완전히 같은 설정은 하나로 센다. 전략이 무시하는 파라미터를
        훑으면(예: signal_outcomes는 청산 규칙을 실행하지 않으므로
        exit_channel을 바꿔도 결과가 같다) 같은 검정이 여러 번 센 것처럼
        보인다. 그대로 두면 임계값만 괜히 높아지고, 표에는 같은 줄이
        여러 번 뜬다.
        """
        return len(self._distinct())

    def _distinct(self) -> list["Trial"]:
        # 속성마다 다시 계산하면 표본 하나당 튜플을 매번 새로 만든다.
        # 설정 200개짜리 탐색에서 그것만으로 수십 초가 나간다.
        cached = getattr(self, "_distinct_cache", None)
        if cached is not None and cached[0] is self.trials:
            return cached[1]
        seen: dict[tuple, Trial] = {}
        for t in self.trials:
            key = (
                tuple(round(x, 12) for x in t.in_sample.returns),
                tuple(round(x, 12) for x in t.out_of_sample.returns),
            )
            seen.setdefault(key, t)
        out = list(seen.values())
        self._distinct_cache = (self.trials, out)
        return out

    @property
    def duplicates(self) -> int:
        """결과가 똑같아서 하나로 합쳐진 설정 수."""
        return len(self.trials) - self.tested

    def threshold_for(self, trial: Trial) -> float:
        """이 후보가 탐색기간에서 넘어야 할 |t| (몇 번 쟀는지 반영)."""
        return bonferroni_threshold(self.tested, trial.in_sample.count - 1, self.alpha)

    def _eligible(self, t: Trial) -> bool:
        return (
            t.in_sample.count >= self.min_samples
            and t.out_of_sample.count >= self.min_samples
        )

    @property
    def shortlist(self) -> list[Trial]:
        """탐색기간에서 보정 임계값을 넘은 것."""
        return [
            t
            for t in self._distinct()
            if self._eligible(t)
            and t.in_sample.net > 0
            and abs(t.in_sample.t_stat) >= self.threshold_for(t)
        ]

    @property
    def survivors(self) -> list[Trial]:
        """검증기간까지 통과한 것. 여기가 비면 찾은 게 없는 것이다."""
        return [t for t in self.shortlist if t.confirmed]

    @property
    def naive_passes(self) -> list[Trial]:
        """보정 없이 |t| >= 2만 봤다면 통과했을 것들 — 우연 기대치와 비교용."""
        return [t for t in self._distinct() if self._eligible(t) and t.in_sample.passes]

    def render(self, top: int = 12) -> str:
        ranked = sorted(
            (t for t in self._distinct() if self._eligible(t)),
            key=lambda t: abs(t.in_sample.t_stat),
            reverse=True,
        )
        thr = bonferroni_threshold(self.tested, 100, self.alpha)
        lines = [
            f"설정 {self.tested}개를 쟀습니다 (표본 {self.min_samples}건 미만은 판정 제외"
            + (f", 결과가 같은 {self.duplicates}개는 하나로 셈)" if self.duplicates else ")"),
            f"본페로니 임계값 |t| >= {thr:.2f}  (보정 안 하면 2.00)",
            "",
            f"  {'설정':<34}{'탐색기간':>20}{'검증기간':>20}",
            f"  {'':<34}{'n':>6}{'1건당':>8}{'t':>6}{'n':>7}{'1건당':>8}{'t':>6}",
            "  " + "─" * 74,
        ]
        for t in ranked[:top]:
            i, o = t.in_sample, t.out_of_sample
            mark = "✓" if t in self.survivors else ("?" if t in self.shortlist else "✗")
            lines.append(
                f"  {_fit(t.candidate.label, 33):<34}{i.count:>6}{i.net:>+7.2f}%"
                f"{i.t_stat:>6.2f}{o.count:>7}{o.net:>+7.2f}%{o.t_stat:>6.2f}  {mark}"
            )
        lines += [
            "  " + "─" * 74,
            "",
            f"  보정 없이 |t|>=2만 봤다면 통과: {len(self.naive_passes)}개",
            f"  {verdict(self.tested, len(self.naive_passes), self.alpha)}",
            "",
            f"  본페로니 통과 (탐색기간): {len(self.shortlist)}개",
            f"  검증기간까지 통과: {len(self.survivors)}개",
        ]
        if self.survivors:
            lines.append("")
            for t in self.survivors:
                lines.append(f"  ✓ {t.candidate.label}")
            lines.append(
                "     → 여기까지 왔어도 '확실하다'는 뜻은 아닙니다. "
                "실시간 페이퍼로 다시 확인하세요."
            )
        else:
            lines.append(
                "\n  ✗ 찾은 것이 없습니다. 우연히 좋아 보이는 것은 있었지만 "
                "\n     몇 번 쟀는지를 감안하거나 검증기간에 던지면 남지 않습니다."
            )
        return "\n".join(lines)


def evaluate(
    candidate: Candidate,
    candles,
    *,
    cost: float,
    split: float = 0.6,
    max_bars: int = 60,
    strategy_factory=None,
) -> Trial:
    """설정 하나를 탐색기간/검증기간으로 나눠 잰다.

    **신호는 전체 구간에서 한 번만 만들고 진입 시점으로 가른다.** 캔들을
    먼저 자르고 각각 돌리면 경계에서 워밍업이 다시 시작되어 검증기간의
    앞부분 신호가 통째로 사라진다.
    """
    from ..strategy.base import get_strategy

    factory = strategy_factory or get_strategy
    strategy = factory(candidate.strategy, **candidate.params)
    boundary = int(len(candles) * split)

    outcomes = signal_outcomes(candles, strategy, max_bars=max_bars)
    every = [o for side in outcomes.values() for o in side]
    # 탐색/검증 각각 안에서 겹침을 뺀다. 합쳐서 빼면 경계를 걸친 신호가
    # 어느 쪽 것인지 모호해진다.
    inside = drop_concurrent([o for o in every if o.exit_index < boundary])
    outside = drop_concurrent([o for o in every if o.entry_index >= boundary])
    return Trial(
        candidate,
        EdgeStats([o.ret for o in inside], cost),
        EdgeStats([o.ret for o in outside], cost),
    )
