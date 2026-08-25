"""전략 비교 — 기준선(baseline)과 함께 줄 세우기.

"어떤 분석 전략이 좋은가"에 답하는 유일한 방법은 **같은 조건에서 나란히 돌려보는 것**이다.
그리고 반드시 기준선을 함께 넣어야 한다:

- **매수 후 보유(Buy & Hold)**: 아무것도 안 하고 들고만 있었을 때.
  상승장에서는 이걸 못 이기는 전략이 대부분이다.
- **무작위 진입**: 같은 손절·목표·사이징으로 동전을 던졌을 때.
  이걸 못 이기면 그 '분석'은 기여가 없다는 뜻이다.

기준선 없는 백테스트 수익률은 해석할 수 없는 숫자다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from ..models import Candle
from ..strategy.base import get_strategy
from . import engine as backtest_engine
from .metrics import Metrics


@dataclass
class ComparisonRow:
    name: str
    label: str
    metrics: Metrics | None = None
    error: str = ""
    is_baseline: bool = False


def buy_and_hold(candles: list[Candle], config: Config) -> Metrics:
    """첫 봉에 사서 마지막 봉까지 들고 있었을 때. 레버리지 없음, 수수료 왕복 1회."""
    equity = config.initial_equity
    fee = config.exchange.taker_fee
    entry = candles[0].close
    size = equity / entry

    curve: list[tuple[int, float]] = []
    peak_equity = equity
    for c in candles:
        value = equity + (c.close - entry) * size
        curve.append((c.ts, value))
        peak_equity = max(peak_equity, value)

    final = equity + (candles[-1].close - entry) * size - (entry + candles[-1].close) * size * fee
    curve[-1] = (candles[-1].ts, final)

    peak = config.initial_equity
    max_dd = 0.0
    for _, value in curve:
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak * 100)

    profit = final - config.initial_equity
    return Metrics(
        initial_equity=config.initial_equity,
        final_equity=final,
        trades=1,
        wins=1 if profit > 0 else 0,
        losses=0 if profit > 0 else 1,
        gross_profit=max(profit, 0.0),
        gross_loss=abs(min(profit, 0.0)),
        total_fees=(entry + candles[-1].close) * size * fee,
        max_drawdown_pct=max_dd,
        max_consecutive_losses=0 if profit > 0 else 1,
    )


def compare(
    candles: list[Candle],
    config: Config,
    *,
    entries: dict[str, dict] | None = None,
    include_baselines: bool = True,
) -> list[ComparisonRow]:
    """여러 전략을 같은 데이터·같은 리스크 설정으로 돌린다.

    entries: {전략이름: 파라미터}. 생략하면 모든 등록 전략을 기본 파라미터로 돌린다.
    """
    from ..strategy.base import available

    targets = entries if entries is not None else {n: {} for n in available()}
    rows: list[ComparisonRow] = []

    if include_baselines:
        try:
            rows.append(
                ComparisonRow("buy_and_hold", "매수 후 보유", buy_and_hold(candles, config), is_baseline=True)
            )
        except Exception as exc:  # noqa: BLE001
            rows.append(ComparisonRow("buy_and_hold", "매수 후 보유", None, str(exc), True))

    for name, params in targets.items():
        cfg = _clone_with_strategy(config, name, params)
        label = "무작위 진입 (대조군)" if name == "random" else name
        try:
            strategy = get_strategy(name, **params)
            metrics = backtest_engine.run(candles, strategy, cfg)
            rows.append(ComparisonRow(name, label, metrics, is_baseline=(name == "random")))
        except Exception as exc:  # noqa: BLE001
            rows.append(ComparisonRow(name, label, None, f"{type(exc).__name__}: {exc}"))
    return rows


def _clone_with_strategy(config: Config, name: str, params: dict) -> Config:
    import copy

    cfg = copy.deepcopy(config)
    cfg.strategy.name = name
    cfg.strategy.params = dict(params)
    return cfg


def comparison_report(rows: list[ComparisonRow]) -> str:
    lines = [
        "═" * 78,
        "  전략 비교 — 같은 데이터, 같은 리스크 설정",
        "═" * 78,
        f"  {'전략':<24}{'거래':>6}{'수익률':>10}{'PF':>7}{'승률':>8}{'MDD':>8}{'기대손익':>11}",
        "─" * 78,
    ]

    ranked = [r for r in rows if r.metrics]
    ranked.sort(key=lambda r: r.metrics.net_pnl, reverse=True)

    for row in ranked:
        m = row.metrics
        pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        mark = "▷" if row.is_baseline else " "
        lines.append(
            f"{mark} {row.label:<24}{m.trades:>6,}{m.return_pct:>9.2f}%{pf:>7}"
            f"{m.win_rate:>7.1f}%{m.max_drawdown_pct:>7.1f}%{m.expectancy:>11,.2f}"
        )
    for row in rows:
        if row.metrics is None:
            lines.append(f"  {row.label:<24}{'오류':>6}  {row.error}")

    lines.append("═" * 78)
    lines.append("  ▷ = 기준선. 이걸 못 이기는 전략은 존재 이유가 없습니다.")

    random_row = next((r for r in ranked if r.name == "random"), None)
    hold_row = next((r for r in ranked if r.name == "buy_and_hold"), None)
    real = [r for r in ranked if not r.is_baseline]

    if random_row and real:
        beaten = [r for r in real if r.metrics.net_pnl > random_row.metrics.net_pnl]
        lines.append("")
        if not beaten:
            lines.append("  ⚠ 무작위 진입을 이긴 전략이 하나도 없습니다.")
            lines.append("     성과는 분석이 아니라 손절·목표·사이징 규칙에서 나온 것입니다.")
        else:
            names = ", ".join(r.label for r in beaten)
            lines.append(f"  ✓ 무작위 대비 우위: {names}")
            lines.append("     단, 표본이 작으면 우연입니다. 워크포워드로 재확인하세요:")
            lines.append("     python -m dorothy walkforward --strategy <이름>")

    if hold_row and real:
        losers = [r for r in real if r.metrics.net_pnl < hold_row.metrics.net_pnl]
        if len(losers) == len(real):
            lines.append("  ⚠ 매수 후 보유를 이긴 전략도 없습니다.")
            lines.append("     이 구간에서는 매매하지 않는 편이 나았다는 뜻입니다.")

    lines.append("  ※ 거래 30건 미만은 통계가 아닙니다. 기간을 늘려 다시 재세요.")
    return "\n".join(lines)
