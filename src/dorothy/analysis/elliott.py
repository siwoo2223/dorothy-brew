"""엘리엇 파동 카운팅 — 그리고 그 한계의 계량화.

⚠ **이 모듈만 성격이 다르다. 반드시 읽고 쓸 것.**

각도·피보나치·ICT 유동성은 정의가 명확해서 코드가 곧 정의다.
엘리엇은 아니다. 같은 차트를 놓고 숙련자 열 명이 열 가지로 센다.
그리고 결정적으로, **새 캔들이 오면 과거 카운트가 바뀐다(리페인팅)**.

"3파였는데 알고 보니 1파였다"가 사후에 성립하는 것은 서사로는 괜찮지만
자동매매에서는 치명적이다. 백테스트는 최종 확정된 카운트로 계산되어
"3파 시작에서 정확히 진입"한 것처럼 보이지만, 실시간에는 그 시점에
그게 3파인지 알 수 없었기 때문이다. 이게 엘리엇 봇이 백테스트에서만
돈을 버는 이유다.

여기서 택한 타협:
1. **규칙(rule)만 하드 필터로 쓴다.** 엘리엇의 3대 불가침 규칙은 객관적이다.
2. **지침(guideline)은 confidence 점수로만 쓴다.** 맞으면 가점, 아니어도 진입 가능.
3. **카운트는 확정된 스윙으로만 만든다.** 미확정 스윙은 존재하지 않는 것으로 본다.
4. **리페인팅을 측정해서 보여준다** (`measure_repainting`). 숨기지 않는다.

결론적으로 이 모듈은 "지금 3파다"를 단언하지 않는다.
"5파 말미로 보이니 신규 진입은 피하자" 정도의 **소프트 필터**로 쓰는 것이 안전하다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Candle
from .liquidity import Bias
from .slope import Slope, leg_angle
from .swings import Swing, SwingKind

# 엘리엇 3대 규칙 (위반하면 그 카운트는 무효)
RULE_WAVE2_LIMIT = "2파는 1파를 100% 이상 되돌릴 수 없다"
RULE_WAVE3_NOT_SHORTEST = "3파는 1·3·5파 중 가장 짧을 수 없다"
RULE_WAVE4_NO_OVERLAP = "4파는 1파의 가격 영역을 침범할 수 없다"


@dataclass(frozen=True)
class Wave:
    number: int
    start: Swing
    end: Swing
    angle: Slope | None = None

    @property
    def length(self) -> float:
        return abs(self.end.price - self.start.price)

    @property
    def is_up(self) -> bool:
        return self.end.price > self.start.price

    @property
    def bars(self) -> int:
        return self.end.index - self.start.index


@dataclass(frozen=True)
class WaveCount:
    waves: list[Wave]
    direction: Bias
    valid: bool
    violations: list[str] = field(default_factory=list)
    confidence: float = 0.0        # 0~1. 지침 충족도이지 '맞을 확률'이 아니다
    notes: list[str] = field(default_factory=list)

    @property
    def current_wave(self) -> int:
        """지금 진행 중이라고 보는 파동 번호. 0이면 판정 불가."""
        return self.waves[-1].number if self.waves else 0

    @property
    def is_terminal(self) -> bool:
        """5파 진행 중 = 추세 막바지. 신규 진입을 피하고 싶은 구간."""
        return self.current_wave >= 5

    @property
    def is_impulsive_leg(self) -> bool:
        """3파 = 보통 가장 강하고 길다. 추세 추종 진입에 가장 좋은 구간."""
        return self.current_wave == 3

    def describe(self) -> str:
        if not self.waves:
            return "카운트 불가"
        head = f"{self.direction.value} {self.current_wave}파"
        if not self.valid:
            return f"{head} (규칙 위반: {'; '.join(self.violations)})"
        return f"{head} (확신도 {self.confidence:.0%})"


def _check_rules(points: list[Swing], up: bool) -> list[str]:
    """확정된 파동 구간에 대해 3대 규칙을 검사한다.

    points는 파동 경계점들. p0=시작, p1=1파 끝, p2=2파 끝, ...
    """
    violations: list[str] = []
    sign = 1 if up else -1

    # 규칙 1: 2파가 1파를 전량 되돌리면 안 된다
    if len(points) >= 3 and (points[2].price - points[0].price) * sign <= 0:
        violations.append(RULE_WAVE2_LIMIT)

    # 규칙 3: 4파가 1파 영역을 침범하면 안 된다
    if len(points) >= 5 and (points[4].price - points[1].price) * sign <= 0:
        violations.append(RULE_WAVE4_NO_OVERLAP)

    # 규칙 2: 3파가 가장 짧으면 안 된다 (1·3·5파가 모두 있을 때만 판정 가능)
    if len(points) >= 6:
        w1 = abs(points[1].price - points[0].price)
        w3 = abs(points[3].price - points[2].price)
        w5 = abs(points[5].price - points[4].price)
        if w3 < w1 and w3 < w5:
            violations.append(RULE_WAVE3_NOT_SHORTEST)

    return violations


def _score_guidelines(points: list[Swing], up: bool) -> tuple[float, list[str]]:
    """지침 충족도. 규칙과 달리 위반해도 무효가 아니다."""
    score, notes = 0.0, []
    checks = 0

    if len(points) >= 3:
        checks += 1
        w1 = abs(points[1].price - points[0].price)
        retrace = abs(points[2].price - points[1].price) / w1 if w1 else 0
        if 0.382 <= retrace <= 0.786:
            score += 1
            notes.append(f"2파 되돌림 {retrace:.0%} (정상 범위)")
        else:
            notes.append(f"2파 되돌림 {retrace:.0%} (이례적)")

    if len(points) >= 4:
        checks += 1
        w1 = abs(points[1].price - points[0].price)
        w3 = abs(points[3].price - points[2].price)
        ratio = w3 / w1 if w1 else 0
        if ratio >= 1.618:
            score += 1
            notes.append(f"3파가 1파의 {ratio:.2f}배 (연장 3파)")
        elif ratio >= 1.0:
            score += 0.5
            notes.append(f"3파가 1파의 {ratio:.2f}배")
        else:
            notes.append(f"3파가 1파보다 짧음 ({ratio:.2f}배)")

    if len(points) >= 5:
        checks += 1
        w3 = abs(points[3].price - points[2].price)
        retrace4 = abs(points[4].price - points[3].price) / w3 if w3 else 0
        if 0.236 <= retrace4 <= 0.5:
            score += 1
            notes.append(f"4파 되돌림 {retrace4:.0%} (정상 범위)")
        else:
            notes.append(f"4파 되돌림 {retrace4:.0%} (이례적)")

    return (score / checks if checks else 0.0), notes


def analyze(
    candles: list[Candle],
    swings: list[Swing],
    *,
    upto: int | None = None,
    max_points: int = 6,
    atr_period: int = 14,
) -> WaveCount:
    """확정된 스윙만으로 가장 그럴듯한 임펄스 카운트를 찾는다.

    가능한 시작점을 모두 시도해 규칙을 통과하는 것 중 확신도가 가장 높은 카운트를 택한다.
    통과하는 카운트가 없으면 valid=False를 그대로 돌려준다 — 억지로 만들지 않는다.
    """
    limit = len(candles) - 1 if upto is None else upto
    usable = [s for s in swings if s.confirmed_index <= limit]
    if len(usable) < 3:
        return WaveCount([], Bias.NEUTRAL, False, ["스윙 부족"])

    best: WaveCount | None = None

    # 최근 스윙들 중 여러 시작점을 시도한다 (엘리엇 카운트가 유일하지 않다는 사실의 반영)
    window = usable[-(max_points + 4) :]
    for start_i in range(len(window) - 2):
        points = window[start_i : start_i + max_points]
        if len(points) < 3:
            continue
        # 임펄스는 고저가 교대해야 한다
        if any(points[i].kind is points[i + 1].kind for i in range(len(points) - 1)):
            continue

        up = points[0].kind is SwingKind.LOW
        violations = _check_rules(points, up)
        confidence, notes = _score_guidelines(points, up)

        waves = [
            Wave(
                number=i,
                start=points[i - 1],
                end=points[i],
                angle=leg_angle(
                    candles, points[i - 1].index, points[i].index, atr_period=atr_period
                ),
            )
            for i in range(1, len(points))
        ]
        count = WaveCount(
            waves=waves,
            direction=Bias.BULLISH if up else Bias.BEARISH,
            valid=not violations,
            violations=violations,
            confidence=confidence,
            notes=notes,
        )

        # 유효한 카운트를 우선하고, 그 안에서 파동 수가 많고 확신도가 높은 것을 택한다
        key = (count.valid, len(count.waves), count.confidence)
        best_key = (best.valid, len(best.waves), best.confidence) if best else (False, 0, -1.0)
        if key > best_key:
            best = count

    return best or WaveCount([], Bias.NEUTRAL, False, ["유효한 카운트 없음"])


# --------------------------------------------------------------------------
# 리페인팅 계량화 — 이 모듈을 믿어도 되는지 직접 재보는 도구
# --------------------------------------------------------------------------
@dataclass
class RepaintReport:
    bars_checked: int
    count_changes: int
    wave_number_changes: int
    direction_flips: int
    valid_ratio: float

    @property
    def change_rate(self) -> float:
        return self.count_changes / self.bars_checked * 100 if self.bars_checked else 0.0

    @property
    def flip_rate(self) -> float:
        return self.direction_flips / self.bars_checked * 100 if self.bars_checked else 0.0

    def report(self) -> str:
        return "\n".join(
            [
                "─" * 52,
                "  엘리엇 카운트 안정성 측정",
                "─" * 52,
                f"  검사 봉 수            {self.bars_checked:>10,}",
                f"  카운트가 바뀐 횟수    {self.count_changes:>10,}  ({self.change_rate:.1f}%)",
                f"  파동 번호 변경        {self.wave_number_changes:>10,}",
                f"  방향 자체가 뒤집힘    {self.direction_flips:>10,}  ({self.flip_rate:.1f}%)",
                f"  규칙 통과 비율        {self.valid_ratio:>9.1f}%",
                "─" * 52,
                "  ※ 변경률이 높을수록 '지금 몇 파인가'에 기대어 매매하기 어렵다는 뜻이다.",
                "     방향 반전율이 높으면 하드 필터로 쓰면 안 된다.",
            ]
        )


def measure_repainting(
    candles: list[Candle], swings: list[Swing], *, start: int = 100, step: int = 1
) -> RepaintReport:
    """봉을 하나씩 넘기며 카운트가 얼마나 자주 바뀌는지 실측한다.

    엘리엇을 전략에 넣기 전에 반드시 이 숫자를 보고 결정할 것.
    """
    prev: WaveCount | None = None
    checked = changes = number_changes = flips = valid_count = 0

    for i in range(start, len(candles), step):
        current = analyze(candles, swings, upto=i)
        checked += 1
        if current.valid:
            valid_count += 1
        if prev is not None:
            if current.current_wave != prev.current_wave:
                number_changes += 1
            if current.direction is not prev.direction:
                flips += 1
            same = (
                current.current_wave == prev.current_wave
                and current.direction is prev.direction
                and len(current.waves) == len(prev.waves)
            )
            if not same:
                changes += 1
        prev = current

    return RepaintReport(
        bars_checked=checked,
        count_changes=changes,
        wave_number_changes=number_changes,
        direction_flips=flips,
        valid_ratio=valid_count / checked * 100 if checked else 0.0,
    )
