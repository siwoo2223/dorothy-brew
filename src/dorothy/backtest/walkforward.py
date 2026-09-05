"""워크포워드 검증 — 백테스트를 믿어도 되는지 판정한다.

**단일 백테스트 수익률은 거의 항상 거짓말이다.** 파라미터를 그 구간에 맞게
고른 순간, 그 구간에서 잘 나오는 건 당연하다. 시험 문제를 보고 답을 외운 뒤
같은 시험을 다시 치는 것과 같다.

워크포워드는 그걸 분리한다:

    [── 학습 구간 ──][─ 검증 ─]
              [── 학습 구간 ──][─ 검증 ─]
                        [── 학습 구간 ──][─ 검증 ─]

학습 구간에서만 파라미터를 고르고, **한 번도 보지 않은** 다음 구간에서 성과를 잰다.
이걸 여러 번 반복해 검증 구간 성과만 모은 것이 '실전에 가장 가까운 추정치'다.

핵심 지표는 **효율(efficiency) = 검증 성과 ÷ 학습 성과**다.
  - 1.0 근처  → 학습 구간 성과가 재현된다. 신뢰할 만하다
  - 0.5 미만  → 절반이 사라졌다. 과최적화 의심
  - 0 이하    → 학습에선 벌고 검증에선 잃는다. 전형적인 과최적화
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field

from ..config import Config
from ..models import Candle
from ..strategy.base import get_strategy
from . import engine as backtest_engine
from .metrics import Metrics

log = logging.getLogger(__name__)

# 전략별 탐색 격자. 일부러 작게 잡았다 —
# 격자가 크면 학습 구간에 우연히 맞는 조합이 반드시 나오고, 그게 곧 과최적화다.
DEFAULT_GRIDS: dict[str, dict[str, list]] = {
    "donchian": {"channel": [10, 20, 40], "atr_stop_mult": [1.5, 2.5]},
    "ema_cross": {"fast": [10, 20], "slow": [50, 100], "atr_stop_mult": [1.5, 2.5]},
    "random": {"entry_probability": [0.01, 0.02]},
}


@dataclass
class Fold:
    index: int
    train_range: tuple[int, int]
    test_range: tuple[int, int]
    best_params: dict
    in_sample: Metrics | None
    out_sample: Metrics | None
    error: str = ""


@dataclass
class WalkForwardResult:
    strategy: str
    folds: list[Fold] = field(default_factory=list)
    grid_size: int = 0

    @property
    def valid_folds(self) -> list[Fold]:
        return [f for f in self.folds if f.in_sample and f.out_sample]

    @property
    def avg_in_sample(self) -> float:
        folds = self.valid_folds
        return sum(f.in_sample.return_pct for f in folds) / len(folds) if folds else 0.0

    @property
    def avg_out_sample(self) -> float:
        folds = self.valid_folds
        return sum(f.out_sample.return_pct for f in folds) / len(folds) if folds else 0.0

    @property
    def efficiency(self) -> float:
        """검증 성과 ÷ 학습 성과. 1에 가까울수록 재현성이 높다."""
        if self.avg_in_sample <= 0:
            return 0.0
        return self.avg_out_sample / self.avg_in_sample

    @property
    def profitable_folds(self) -> int:
        return sum(1 for f in self.valid_folds if f.out_sample.return_pct > 0)

    @property
    def total_oos_trades(self) -> int:
        return sum(f.out_sample.trades for f in self.valid_folds)

    @property
    def params_are_stable(self) -> bool:
        """구간마다 최적 파라미터가 요동치면 그 '최적'은 노이즈를 맞춘 것이다."""
        folds = self.valid_folds
        if len(folds) < 2:
            return True
        distinct = {tuple(sorted(f.best_params.items())) for f in folds}
        return len(distinct) <= max(1, len(folds) // 2)

    def report(self) -> str:
        lines = [
            "═" * 76,
            f"  워크포워드 검증 — {self.strategy}",
            "═" * 76,
            f"  탐색 조합 {self.grid_size}개 · 구간 {len(self.folds)}개",
            "─" * 76,
            f"  {'구간':<6}{'학습 수익':>12}{'검증 수익':>12}{'검증 거래':>10}  최적 파라미터",
            "─" * 76,
        ]
        for fold in self.folds:
            if fold.error:
                lines.append(f"  #{fold.index:<5}{'오류':>12}  {fold.error}")
                continue
            params = ", ".join(f"{k}={v}" for k, v in sorted(fold.best_params.items()))
            lines.append(
                f"  #{fold.index:<5}{fold.in_sample.return_pct:>11.2f}%"
                f"{fold.out_sample.return_pct:>11.2f}%{fold.out_sample.trades:>10,}  {params}"
            )

        folds = self.valid_folds
        lines += [
            "─" * 76,
            f"  평균 학습 수익   {self.avg_in_sample:>8.2f}%",
            f"  평균 검증 수익   {self.avg_out_sample:>8.2f}%   ← 실전에 가장 가까운 추정치",
            f"  효율             {self.efficiency:>8.2f}    (검증 ÷ 학습)",
            f"  검증 흑자 구간   {self.profitable_folds}/{len(folds)}",
            f"  검증 총 거래     {self.total_oos_trades:>8,}",
            "═" * 76,
        ]

        if not folds:
            lines.append("  ⚠ 유효한 구간이 없습니다. 데이터를 늘리거나 구간 수를 줄이세요.")
            return "\n".join(lines)

        if self.avg_out_sample <= 0 < self.avg_in_sample:
            lines.append("  ✗ 학습에선 벌고 검증에선 잃습니다. **전형적인 과최적화입니다.**")
            lines.append("     파라미터를 더 조정하지 마세요. 그럴수록 나빠집니다.")
        elif self.efficiency < 0.5:
            lines.append("  ⚠ 학습 성과의 절반 이상이 검증에서 사라졌습니다. 과최적화 의심.")
            lines.append("     파라미터를 줄이거나 격자를 더 좁히세요.")
        elif self.avg_out_sample > 0:
            lines.append("  ✓ 검증 구간에서도 수익이 유지됩니다.")

        if self.profitable_folds <= len(folds) // 2:
            lines.append(f"  ⚠ 흑자 구간이 절반 이하({self.profitable_folds}/{len(folds)})입니다.")
            lines.append("     특정 장세에서만 통하는 전략일 수 있습니다.")

        if not self.params_are_stable:
            lines.append("  ⚠ 구간마다 최적 파라미터가 다릅니다.")
            lines.append("     그 '최적값'은 시장 구조가 아니라 노이즈를 맞춘 것입니다.")

        if self.total_oos_trades < 30:
            lines.append(f"  ⚠ 검증 거래가 {self.total_oos_trades}건뿐입니다. 통계로 볼 수 없습니다.")
        return "\n".join(lines)


def _grid_combinations(grid: dict[str, list]) -> list[dict]:
    if not grid:
        return [{}]
    keys = sorted(grid)
    return [dict(zip(keys, values)) for values in itertools.product(*(grid[k] for k in keys))]


def _score(metrics: Metrics, min_trades: int) -> float:
    """학습 구간에서 파라미터를 고르는 기준.

    순손익만 보면 거래 2건으로 운 좋게 번 조합이 뽑힌다. 최소 거래 수를 요구한다.
    """
    if metrics.trades < min_trades:
        return float("-inf")
    return metrics.net_pnl


def run(
    candles: list[Candle],
    config: Config,
    *,
    strategy_name: str | None = None,
    grid: dict[str, list] | None = None,
    folds: int = 4,
    train_ratio: float = 0.7,
    min_train_trades: int = 5,
) -> WalkForwardResult:
    name = strategy_name or config.strategy.name
    search = grid if grid is not None else DEFAULT_GRIDS.get(name, {})
    combos = _grid_combinations(search)
    result = WalkForwardResult(strategy=name, grid_size=len(combos))

    if not 0.3 <= train_ratio <= 0.9:
        raise ValueError("train_ratio는 0.3~0.9 사이여야 합니다.")
    if folds < 1:
        raise ValueError("folds는 1 이상이어야 합니다.")

    segment = len(candles) // folds
    if segment < 200:
        raise ValueError(
            f"구간당 캔들이 {segment}개뿐입니다. 데이터를 늘리거나 folds를 줄이세요."
        )

    base_params = dict(config.strategy.params)
    train_len = int(segment * train_ratio)

    for k in range(folds):
        start = k * segment
        train_end = start + train_len
        test_end = start + segment
        train = candles[start:train_end]
        test = candles[train_end:test_end]

        best_params, best_metrics, best_score = None, None, float("-inf")
        for combo in combos:
            params = {**base_params, **combo}
            try:
                metrics = backtest_engine.run(
                    train, get_strategy(name, **params), _cfg_for(config, name, params)
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("구간 %d 조합 %s 실패: %s", k, combo, exc)
                continue
            score = _score(metrics, min_train_trades)
            if score > best_score:
                best_params, best_metrics, best_score = params, metrics, score

        if best_params is None:
            result.folds.append(
                Fold(k, (start, train_end), (train_end, test_end), {}, None, None,
                     f"학습 구간에서 거래 {min_train_trades}건 이상인 조합이 없습니다")
            )
            continue

        try:
            oos = backtest_engine.run(
                test, get_strategy(name, **best_params), _cfg_for(config, name, best_params)
            )
        except Exception as exc:  # noqa: BLE001
            result.folds.append(
                Fold(k, (start, train_end), (train_end, test_end), best_params,
                     best_metrics, None, f"검증 실패: {exc}")
            )
            continue

        # 격자에서 실제로 탐색한 값만 보고한다 (고정 파라미터까지 보고하면 표가 지저분해진다)
        reported = {k_: v for k_, v in best_params.items() if k_ in search}
        result.folds.append(
            Fold(k, (start, train_end), (train_end, test_end), reported, best_metrics, oos)
        )

    return result


def _cfg_for(config: Config, name: str, params: dict) -> Config:
    import copy

    cfg = copy.deepcopy(config)
    cfg.mode = "backtest"
    cfg.strategy.name = name
    cfg.strategy.params = dict(params)
    return cfg
