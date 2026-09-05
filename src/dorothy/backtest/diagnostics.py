"""전략 진단 도구 — 왜 진입하지 않는가, 어떤 요소가 실제로 기여하는가.

합류(confluence) 전략에는 고유한 실패 방식이 있다. 필터를 겹겹이 쌓다 보면
어느 순간 **거래가 0건**이 되는데, 백테스트 결과만 보면 원인을 알 수 없다.
"수익률 0%"는 전략이 신중한 것인지, 조건 하나가 영원히 거짓인지 구분해주지 않는다.

여기 두 도구가 그걸 구분해준다:

- `funnel`   : 어느 단계에서 몇 번 걸러졌는지 (깔때기 분석)
- `ablate`   : 요소를 하나씩 꺼보며 성과 기여도 측정 (제거 실험)

ablate가 특히 중요하다. 요소 넷을 쌓아 수익이 났을 때, 그게 넷 다 필요해서인지
사실은 하나만 일하고 셋은 파라미터만 늘린 것인지 알아야 한다.
후자라면 그 셋은 과최적화 재료일 뿐이다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..config import Config
from ..models import Action, Candle
from ..strategy.base import Strategy, get_strategy, known_params
from ..strategy.base import _REGISTRY
from . import engine as backtest_engine
from .metrics import Metrics


@dataclass
class Funnel:
    """진입 조건 깔때기. 상단이 넓고 하단이 좁아야 정상이다."""

    total_bars: int
    entries: int
    rejections: Counter = field(default_factory=Counter)

    @property
    def entry_rate(self) -> float:
        return self.entries / self.total_bars * 100 if self.total_bars else 0.0

    def report(self) -> str:
        lines = [
            "─" * 62,
            "  진입 조건 깔때기 — 어디서 걸러지는가",
            "─" * 62,
            f"  평가한 봉        {self.total_bars:>10,}",
            f"  진입 신호        {self.entries:>10,}  ({self.entry_rate:.2f}%)",
            "─" * 62,
        ]
        for reason, count in self.rejections.most_common(12):
            pct = count / self.total_bars * 100 if self.total_bars else 0
            bar = "█" * int(pct / 2.5)
            lines.append(f"  {count:>7,} ({pct:>5.1f}%) {bar:<20} {reason}")
        lines.append("─" * 62)

        if self.entries == 0:
            lines.append("  ⚠ 진입이 0건입니다. 위 1순위 항목이 사실상 항상 참인 조건은 아닌지")
            lines.append("     확인하세요. 조건이 신중한 것과 절대 성립하지 않는 것은 다릅니다.")
        elif self.entry_rate > 5:
            lines.append("  ⚠ 진입률이 높습니다. 필터가 거의 작동하지 않을 수 있습니다.")
        return "\n".join(lines)


def funnel(candles: list[Candle], strategy: Strategy, *, step: int = 1) -> Funnel:
    """포지션 없는 상태로 매 봉을 평가해 거절 사유를 집계한다."""
    result = Funnel(total_bars=0, entries=0)
    for i in range(strategy.warmup, len(candles), step):
        signal = strategy.generate(candles[: i + 1], None)
        result.total_bars += 1
        if signal.action is Action.HOLD:
            # 괄호 안 수치는 제외하고 사유만 묶는다
            key = signal.reason.split("(")[0].strip() or "(사유 없음)"
            result.rejections[key] += 1
        else:
            result.entries += 1
    return result


# --------------------------------------------------------------------------
# 제거 실험 (ablation)
# --------------------------------------------------------------------------
@dataclass
class AblationRow:
    label: str
    params: dict
    metrics: Metrics | None
    error: str = ""


# 요소별 '끄는 방법'. 각 항목은 그 요소를 무력화하는 파라미터다.
ABLATIONS: dict[str, dict] = {
    "전체 (기준선)": {},
    "− 각도(변위) 필터": {"min_displacement_angle": -90.0, "min_r_squared": 0.0},
    "− 피보나치 OTE": {"entry_tolerance": 999.0},      # 구간이 전 범위가 되어 사실상 무조건 통과
    "− FVG": {"use_fvg": False},
    "− 엘리엇 필터": {"elliott_enabled": False},
    "− 합류 점수제": {"min_score": -999.0},
    "− 등가레벨 허용오차": {"pool_tolerance": 0.0},
    "− 손익비 하한": {"min_rr": 0.0},
}


def ablate(
    candles: list[Candle],
    config: Config,
    *,
    base_params: dict | None = None,
    selected: list[str] | None = None,
) -> list[AblationRow]:
    """요소를 하나씩 꺼가며 백테스트를 반복한다.

    기준선보다 성과가 **떨어지지 않는** 항목은 그 요소가 기여하지 않는다는 뜻이다.
    (요소를 껐는데 결과가 같거나 좋아졌다 = 그 요소는 없어도 된다)
    """
    base = dict(base_params or config.strategy.params)
    names = selected or list(ABLATIONS)
    rows: list[AblationRow] = []

    get_strategy(config.strategy.name)   # 전략 존재 확인 + 레지스트리 적재
    supported = known_params(_REGISTRY[config.strategy.name])

    for name in names:
        overrides = ABLATIONS.get(name)
        if overrides is None:
            rows.append(AblationRow(name, {}, None, f"알 수 없는 항목: {name}"))
            continue
        missing = set(overrides) - supported
        if missing:
            # 이 전략에 없는 요소는 끌 수도 없다. 조용히 건너뛰지 말고 명시한다.
            rows.append(
                AblationRow(name, {}, None, f"이 전략에 해당 없음 ({', '.join(sorted(missing))})")
            )
            continue
        params = {**base, **overrides}
        try:
            strategy = get_strategy(config.strategy.name, **params)
            metrics = backtest_engine.run(candles, strategy, config)
            rows.append(AblationRow(name, params, metrics))
        except Exception as exc:  # noqa: BLE001
            rows.append(AblationRow(name, params, None, f"{type(exc).__name__}: {exc}"))
    return rows


def ablation_report(rows: list[AblationRow]) -> str:
    header = (
        f"  {'구성':<22}{'거래':>7}{'순손익':>12}{'PF':>8}{'MDD':>8}{'기대손익':>11}"
    )
    lines = ["═" * 70, "  제거 실험 — 각 요소가 실제로 기여하는가", "═" * 70, header, "─" * 70]

    baseline = next((r for r in rows if r.label.startswith("전체") and r.metrics), None)
    for row in rows:
        if row.metrics is None:
            lines.append(f"  {row.label:<22}{'오류':>7}  {row.error}")
            continue
        m = row.metrics
        pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        lines.append(
            f"  {row.label:<22}{m.trades:>7,}{m.net_pnl:>12,.2f}{pf:>8}"
            f"{m.max_drawdown_pct:>7.1f}%{m.expectancy:>11,.2f}"
        )

    lines.append("═" * 70)
    if baseline and baseline.metrics:
        useless = [
            r.label for r in rows
            if r.metrics and not r.label.startswith("전체")
            and r.metrics.net_pnl >= baseline.metrics.net_pnl
        ]
        if useless:
            lines.append("  ⚠ 꺼도 성과가 나빠지지 않은 요소:")
            for label in useless:
                lines.append(f"     · {label.lstrip('− ')}")
            lines.append("     → 이 요소들은 기여가 없습니다. 빼는 편이 파라미터가 줄어")
            lines.append("        과최적화 위험도 함께 줄어듭니다.")
        else:
            lines.append("  ✓ 모든 요소가 기준선 대비 기여하고 있습니다.")
    lines.append("  ※ 표본이 작으면 이 비교도 우연입니다. 거래 30건 이상에서만 신뢰하세요.")
    return "\n".join(lines)
