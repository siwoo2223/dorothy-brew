"""매매일지 분석 — 내 실제 우위가 어디에 있는가.

백테스트는 '이 규칙이 과거에 통했나'를 묻는다.
매매일지 분석은 '내가 실제로 뭘 잘하고 뭘 못하나'를 묻는다.
후자가 먼저다. 이미 갖고 있는 우위를 자동화하는 편이,
없는 우위를 새로 만드는 것보다 훨씬 확률이 높기 때문이다.

표본이 작을 때의 원칙:
20~30건짜리 기록에서 "숏이 롱보다 낫다" 같은 결론은 대부분 우연이다.
그래서 이 모듈은 그룹 비교마다 **순열검정(permutation test)**으로
"이 차이가 순전히 운일 확률"을 함께 내놓는다.
숫자만 보고 결론을 내리지 않기 위한 장치다.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field

from .records import JournalTrade

# 이 태그가 붙은 매매는 '지키지 못한 매매'다
NEGATIVE_TAGS = {"뇌동매매", "추격매수", "손절 늦음", "과도한 레버리지", "분할 실패", "FOMO", "익절 조급"}
POSITIVE_TAGS = {"계획대로"}


@dataclass
class GroupStat:
    label: str
    trades: list[JournalTrade] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.is_win)

    @property
    def win_rate(self) -> float:
        return self.wins / self.n * 100 if self.n else 0.0

    @property
    def expectancy(self) -> float:
        """1회당 기대 손익(금액)."""
        return self.total_pnl / self.n if self.n else 0.0

    @property
    def expectancy_pct(self) -> float:
        """1회당 기대 수익률(증거금 대비 %).

        금액 기대값은 증거금이 커진 최근 매매에 끌려간다.
        규모가 변하는 계좌에서는 이쪽이 더 정직하다.
        """
        return sum(t.return_pct for t in self.trades) / self.n if self.n else 0.0

    @property
    def profit_factor(self) -> float:
        gross_win = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl <= 0))
        return gross_win / gross_loss if gross_loss else float("inf")

    @property
    def worst(self) -> float:
        return min((t.pnl for t in self.trades), default=0.0)


def permutation_p_value(
    a: list[float], b: list[float], *, iterations: int = 20_000, seed: int = 7
) -> float:
    """두 그룹의 평균 차이가 우연일 확률.

    라벨을 무작위로 섞어 다시 나눴을 때, 실제만큼 큰 차이가 얼마나 자주 나오는지 센다.
    표본이 작아 정규성을 가정할 수 없을 때 쓰는 가장 단순하고 정직한 방법이다.

    p가 크면(예: 0.3) "이 차이는 운으로도 흔히 나온다"는 뜻이다.
    """
    if not a or not b:
        return 1.0
    observed = abs(sum(a) / len(a) - sum(b) / len(b))
    pool = a + b
    cut = len(a)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(iterations):
        rng.shuffle(pool)
        diff = abs(sum(pool[:cut]) / cut - sum(pool[cut:]) / (len(pool) - cut))
        if diff >= observed:
            extreme += 1
    return extreme / iterations


@dataclass
class Analysis:
    trades: list[JournalTrade]

    # --- 전체 ---
    @property
    def overall(self) -> GroupStat:
        return GroupStat("전체", self.trades)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def max_consecutive_losses(self) -> int:
        streak = worst = 0
        for t in self.trades:
            streak = 0 if t.is_win else streak + 1
            worst = max(worst, streak)
        return worst

    @property
    def concentration(self) -> float:
        """가장 크게 번 한 건이 총이익에서 차지하는 비중(%).

        이게 높으면 '전략이 통한 것'이 아니라 '한 방이 터진 것'이다.
        그 한 건을 빼고도 수익이 남는지 반드시 확인해야 한다.
        """
        gains = sorted((t.pnl for t in self.trades if t.pnl > 0), reverse=True)
        if not gains:
            return 0.0
        return gains[0] / sum(gains) * 100

    @property
    def pnl_without_best(self) -> float:
        """최고 수익 한 건을 제외한 총손익."""
        if not self.trades:
            return 0.0
        best = max(self.trades, key=lambda t: t.pnl)
        return self.total_pnl - best.pnl

    @property
    def margin_variation(self) -> float:
        """증거금의 변동계수(표준편차÷평균).

        높을수록 매매마다 베팅 크기가 들쭉날쭉하다는 뜻이다.
        사이징이 일정하지 않으면 '어느 매매가 좋았나'를 비교할 수 없고,
        큰 베팅 한 번이 작은 승리 열 번을 지운다.
        """
        margins = [t.margin for t in self.trades if t.margin > 0]
        if len(margins) < 2:
            return 0.0
        mean = sum(margins) / len(margins)
        var = sum((m - mean) ** 2 for m in margins) / (len(margins) - 1)
        return (var**0.5) / mean if mean else 0.0

    @property
    def r_multiples(self) -> list[float]:
        """손절액이 기록된 매매의 R 배수 목록."""
        return [r for t in self.trades if (r := t.r_multiple) is not None]

    @property
    def avg_r(self) -> float:
        """1회 평균 R. 이게 양수면 계좌 크기와 무관하게 우위가 있다는 뜻이다.

        금액 기대값은 베팅 크기가 커지면 같이 커지지만 R은 그렇지 않다.
        그래서 R이 '진짜 실력'에 가장 가까운 단위다.
        """
        rs = self.r_multiples
        return sum(rs) / len(rs) if rs else 0.0

    @property
    def r_win_avg(self) -> float:
        wins = [r for r in self.r_multiples if r > 0]
        return sum(wins) / len(wins) if wins else 0.0

    @property
    def r_loss_avg(self) -> float:
        losses = [r for r in self.r_multiples if r <= 0]
        return sum(losses) / len(losses) if losses else 0.0

    @property
    def stop_overruns(self) -> list[JournalTrade]:
        """계획한 손절액보다 더 잃은 매매. 손절이 작동하지 않은 사례다."""
        return [
            t for t in self.trades
            if t.planned_risk > 0 and t.pnl < 0 and abs(t.pnl) > t.planned_risk * 1.1
        ]

    @property
    def missing_stop_ratio(self) -> float:
        """손절액이 비어 있는 비율. R 분석을 막는 유일한 원인."""
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t.planned_risk <= 0) / len(self.trades) * 100

    # --- 그룹 ---
    def by(self, key) -> list[GroupStat]:
        buckets: dict[str, list[JournalTrade]] = defaultdict(list)
        for trade in self.trades:
            value = key(trade)
            if isinstance(value, list):
                for v in value or ["(없음)"]:
                    buckets[str(v)].append(trade)
            else:
                buckets[str(value) if value not in (None, "") else "(없음)"].append(trade)
        return sorted(
            (GroupStat(label, items) for label, items in buckets.items()),
            key=lambda g: g.total_pnl,
            reverse=True,
        )

    def by_side(self) -> list[GroupStat]:
        return self.by(lambda t: t.side)

    def by_tag(self) -> list[GroupStat]:
        return self.by(lambda t: t.tags)

    def by_leverage(self) -> list[GroupStat]:
        def bucket(t: JournalTrade) -> str:
            if t.leverage <= 0:
                return "(없음)"
            if t.leverage <= 10:
                return "1~10x"
            if t.leverage <= 25:
                return "11~25x"
            if t.leverage <= 50:
                return "26~50x"
            return "50x 초과"

        return self.by(bucket)

    def by_weekday(self) -> list[GroupStat]:
        return self.by(lambda t: t.weekday)

    def by_discipline(self) -> list[GroupStat]:
        """계획을 지킨 매매 vs 지키지 못한 매매."""
        def label(t: JournalTrade) -> str:
            if any(tag in NEGATIVE_TAGS for tag in t.tags):
                return "규율 위반"
            if any(tag in POSITIVE_TAGS for tag in t.tags):
                return "계획대로"
            return "태그 없음"

        return self.by(label)

    # --- 위험 신호 ---
    def rewarded_bad_habits(self) -> list[GroupStat]:
        """부정적 태그인데 돈을 번 경우.

        **가장 위험한 패턴이다.** 나쁜 습관이 보상받으면 그 습관은 강화되고,
        결국 표본이 쌓였을 때 한 번에 청산된다.
        지금 수익이 나고 있다는 사실이 그 습관이 옳다는 증거가 아니다.
        """
        return [
            g for g in self.by_tag()
            if g.label in NEGATIVE_TAGS and g.total_pnl > 0
        ]

    def compare(self, groups: list[GroupStat], *, metric: str = "return_pct") -> tuple[str, float] | None:
        """상위 두 그룹의 차이가 우연일 확률을 계산한다."""
        usable = [g for g in groups if g.n >= 3]
        if len(usable) < 2:
            return None
        top, second = usable[0], usable[1]

        def values(g: GroupStat) -> list[float]:
            return [t.return_pct if metric == "return_pct" else t.pnl for t in g.trades]

        p = permutation_p_value(values(top), values(second))
        return f"{top.label} vs {second.label}", p


# ==========================================================================
# 리포트
# ==========================================================================
def _group_table(title: str, groups: list[GroupStat], *, min_n: int = 3) -> list[str]:
    lines = [f"  {title}", "  " + "─" * 68,
             f"  {'구분':<14}{'건수':>5}{'총손익':>11}{'1회기대':>10}{'승률':>8}{'최악':>10}"]
    for g in groups:
        flag = "" if g.n >= min_n else "  ⚠표본부족"
        lines.append(
            f"  {g.label:<14}{g.n:>5}{g.total_pnl:>11,.2f}{g.expectancy_pct:>9.1f}%"
            f"{g.win_rate:>7.1f}%{g.worst:>10,.2f}{flag}"
        )
    return lines + [""]


def report(analysis: Analysis) -> str:
    a = analysis
    if not a.trades:
        return "매매 기록이 없습니다."

    o = a.overall
    first = a.trades[0].traded_on
    last = a.trades[-1].traded_on
    period = f"{first} ~ {last}" if first and last else "기간 미상"

    lines = [
        "═" * 72,
        "  매매일지 분석",
        "═" * 72,
        f"  기간 {period} · {o.n}건",
        "─" * 72,
        f"  총손익        {a.total_pnl:>12,.2f}",
        f"  승률          {o.win_rate:>11.1f}%   ({o.wins}승 {o.n - o.wins}패)",
        f"  손익비(PF)    {o.profit_factor:>12.2f}",
        f"  1회 기대손익  {o.expectancy:>12,.2f}",
        f"  1회 기대수익률{o.expectancy_pct:>11.1f}%   (증거금 대비)",
        f"  최대 손실 1건 {o.worst:>12,.2f}",
        f"  최대 연속손실 {a.max_consecutive_losses:>12}",
        "═" * 72,
        "",
    ]

    lines += _group_table("방향별", a.by_side())
    lines += _group_table("레버리지별", a.by_leverage())
    lines += _group_table("규율별", a.by_discipline())
    lines += _group_table("실수 태그별", a.by_tag())

    # --- R 분석 (손절액이 기록된 경우에만) ---
    if a.r_multiples:
        overruns = a.stop_overruns
        lines += [
            f"  R 분석 ({len(a.r_multiples)}/{o.n}건에 손절액 기록됨)",
            "  " + "─" * 68,
            f"  1회 평균 R      {a.avg_r:>8.2f}R   ← 양수면 계좌 크기와 무관한 우위",
            f"  이겼을 때 평균  {a.r_win_avg:>8.2f}R",
            f"  졌을 때 평균    {a.r_loss_avg:>8.2f}R",
            f"  손절 초과       {len(overruns):>8}건   (계획보다 더 잃은 매매)",
            "",
        ]
        if overruns:
            for t in overruns[:5]:
                lines.append(
                    f"    · 회차 {t.index}: 계획 {t.planned_risk:,.2f} → 실제 {abs(t.pnl):,.2f}"
                    f" ({abs(t.pnl) / t.planned_risk:.1f}배)"
                )
            lines.append("")

    # --- 통계적 유의성 ---
    lines += ["  통계 검정 (순열검정 · 이 차이가 우연일 확률)", "  " + "─" * 68]
    for title, groups in (("방향", a.by_side()), ("레버리지", a.by_leverage()), ("규율", a.by_discipline())):
        result = a.compare(groups)
        if result is None:
            lines.append(f"  {title:<10} 비교 불가 (표본 3건 이상인 그룹이 둘 미만)")
            continue
        label, p = result
        verdict = "유의미함" if p < 0.05 else "우연으로도 흔함" if p > 0.2 else "판단 보류"
        lines.append(f"  {title:<10} {label:<24} p={p:.3f}  → {verdict}")
    lines += [
        "",
        "  ※ p가 0.05보다 작아야 '우연이 아니다'라고 말할 수 있습니다.",
        "     기록이 20~30건이면 대부분 p가 크게 나옵니다. 그게 정상이고,",
        "     '아직 결론을 낼 수 없다'는 뜻이지 '차이가 없다'는 뜻은 아닙니다.",
        "",
    ]

    # --- 위험 신호 ---
    lines += ["═" * 72, "  위험 신호", "═" * 72]
    warnings: list[str] = []

    rewarded = a.rewarded_bad_habits()
    if rewarded:
        names = ", ".join(f"{g.label}({g.total_pnl:+,.0f})" for g in rewarded)
        warnings += [
            f"  ⚠ 나쁜 습관이 보상받고 있습니다: {names}",
            "     지금 수익이 난다는 사실은 그 습관이 옳다는 증거가 아닙니다.",
            "     보상받은 습관은 강화되고, 표본이 쌓이면 한 번에 청산됩니다.",
            "     이게 이 리포트에서 가장 위험한 항목입니다.",
            "",
        ]

    if a.concentration > 30:
        warnings += [
            f"  ⚠ 총이익의 {a.concentration:.0f}%가 단 한 건에서 나왔습니다.",
            f"     그 한 건을 빼면 총손익은 {a.pnl_without_best:+,.2f}입니다.",
            "     전략이 통한 것인지 한 방이 터진 것인지 구분해야 합니다.",
            "",
        ]

    if a.missing_stop_ratio > 20:
        warnings += [
            f"  ⚠ 손절액이 {a.missing_stop_ratio:.0f}%의 기록에서 비어 있습니다.",
            "     R(손절액 대비 배수) 분석이 불가능합니다. R은 계좌 크기와 무관하게",
            "     매매를 비교할 수 있는 유일한 단위라, 이게 없으면 성과 비교가",
            "     증거금 크기에 끌려갑니다. **가장 먼저 채워야 할 칸입니다.**",
            "",
        ]

    if a.stop_overruns:
        worst_over = max(a.stop_overruns, key=lambda t: abs(t.pnl) / t.planned_risk)
        ratio = abs(worst_over.pnl) / worst_over.planned_risk
        warnings += [
            f"  ⚠ 계획한 손절액을 넘긴 매매가 {len(a.stop_overruns)}건입니다"
            f" (최악 {ratio:.1f}배).",
            "     손절 규칙이 있어도 지켜지지 않으면 없는 것과 같습니다.",
            "     거래소에 스탑 주문을 미리 걸어두면 의지력에 기대지 않아도 됩니다.",
            "",
        ]

    if a.margin_variation > 0.5:
        warnings += [
            f"  ⚠ 베팅 크기가 들쭉날쭉합니다 (변동계수 {a.margin_variation:.2f}).",
            "     큰 베팅 한 번이 작은 승리 여러 번을 지웁니다.",
            "     증거금을 자본의 일정 비율로 고정하면 이 문제가 사라집니다.",
            "",
        ]

    worst_ratio = abs(o.worst) / max(
        (t.margin for t in a.trades if t.pnl == o.worst), default=1
    ) * 100
    if worst_ratio > 50:
        warnings += [
            f"  ⚠ 최악의 한 건에서 증거금의 {worst_ratio:.0f}%를 잃었습니다.",
            "     손절이 작동하지 않았다는 뜻입니다. 고배율에서는 가격이",
            "     1%만 움직여도 증거금이 사라집니다.",
            "",
        ]

    if o.n < 30:
        warnings += [
            f"  ⚠ 기록이 {o.n}건뿐입니다. 어떤 결론도 잠정적입니다.",
            "     최소 50건, 가능하면 100건은 쌓아야 패턴을 신뢰할 수 있습니다.",
            "",
        ]

    lines += warnings or ["  특별한 위험 신호가 없습니다.", ""]

    # --- 다음 행동 ---
    lines += ["═" * 72, "  자동화 후보", "═" * 72]
    best_side = a.by_side()[0] if a.by_side() else None
    if best_side and best_side.n >= 5 and best_side.total_pnl > 0:
        lines += [
            f"  · '{best_side.label}' 방향이 {best_side.n}건에서 {best_side.total_pnl:+,.2f}입니다.",
            f"    (1회 기대 {best_side.expectancy_pct:+.1f}%, 승률 {best_side.win_rate:.0f}%)",
            "    다만 위 검정 결과를 먼저 보세요. p가 크면 아직 우연입니다.",
            "",
        ]
    lines += [
        "  자동화 전에 채워야 할 것:",
        "  1. 손절액을 매 기록에 남기세요 → R 분석이 열립니다",
        "  2. 진입 근거를 짧게라도 남기세요 → 어떤 셋업이 통하는지 분류됩니다",
        "  3. 증거금을 자본의 고정 비율로 → 매매 간 비교가 가능해집니다",
        "",
        "  이 셋이 갖춰지고 50건이 쌓이면, 그때 '내 우위'를 코드로 옮길 수 있습니다.",
        "  지금은 데이터가 그 질문에 답할 만큼 쌓이지 않았습니다.",
    ]
    return "\n".join(lines)
