"""메타라벨링 — 1차 전략의 신호 중 어느 것을 취할지 배운다.

1차 전략(예: 돈치안)이 **방향**을 정하고, 모델은 **취할지 말지**만 배운다.

이 분업이 중요한 이유:
- 방향 예측은 정확도 상한이 52~55%로 극도로 어렵다
- "이 신호가 통할까"는 훨씬 쉬운 이진 문제다
- 이미 있는 전략을 버리지 않고 개선한다

실제 근거도 있다. 돈치안 단독은 5년 실제 데이터에서 1,409거래 -67%였는데,
손으로 만든 조잡한 국면 필터 하나가 +8.8%로 뒤집었다. 그 필터를 사람이 아니라
모델이 데이터에서 찾게 하는 것이 메타라벨링이다.

⚠ 모델을 쓴다고 검증이 쉬워지지 않는다. 오히려 더 어렵다.
   purging·embargo 없이 학습하면 거의 확실하게 누수가 생긴다.
"""

from __future__ import annotations

import logging
import math
import statistics
import unicodedata
from dataclasses import dataclass, field

from ..config import Config
from ..models import Action, Candle, Side
from ..strategy.base import Strategy
from .features import FEATURE_NAMES, compute_at, warmup as feature_warmup
from .labeling import Sample, triple_barrier
from .validation import purged_walk_forward

log = logging.getLogger(__name__)


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    """한글은 화면에서 두 칸을 차지하는데 len()은 1로 센다. 그대로 쓰면 표가 어긋난다."""
    return " " * max(0, width - _display_width(text)) + text


def build_dataset(
    candles: list[Candle],
    strategy: Strategy,
    *,
    max_bars: int = 168,
    step: int = 1,
) -> list[Sample]:
    """1차 전략이 진입 신호를 낸 지점마다 특징과 라벨을 만든다.

    신호가 난 봉만 표본이 된다. 매 봉을 표본으로 쓰면 모델이 '언제 신호가 나는가'를
    배우게 되는데, 그건 1차 전략이 이미 하는 일이라 배울 필요가 없다.
    """
    start = max(strategy.warmup, feature_warmup())
    samples: list[Sample] = []

    for i in range(start, len(candles) - 1, step):
        signal = strategy.generate(candles[: i + 1], None)
        if signal.action is Action.HOLD or not signal.is_entry:
            continue
        if signal.stop_loss is None or signal.take_profit is None:
            continue

        features = compute_at(candles, i)
        if features is None:
            continue

        side = Side.LONG if signal.action is Action.ENTER_LONG else Side.SHORT
        outcome = triple_barrier(
            candles, i, side, signal.stop_loss, signal.take_profit, max_bars=max_bars
        )
        if outcome is None:
            continue

        samples.append(
            Sample(i, outcome.exit_index, candles[i].ts, features, outcome.label, side,
                   stop=signal.stop_loss, target=signal.take_profit)
        )

    return samples


@dataclass
class FoldResult:
    fold: int
    train_size: int
    test_size: int
    base_rate: float          # 필터 없이 취했을 때의 승률
    filtered_rate: float      # 모델이 취하라고 한 것만의 승률
    taken: int
    lift: float               # filtered - base (양수여야 의미가 있다)


@dataclass
class EdgeCheck:
    """승률이 아니라 **돈**으로 본 성적.

    승률이 올라도 수수료를 못 넘으면 아무 의미가 없다. 판단은 이 표로 한다.
    """

    label: str
    count: int
    gross_per_trade: float    # 수수료 전 1회 기대수익 (%)
    net_per_trade: float      # 수수료 후 (%)
    t_stat: float = 0.0       # 수수료 후 기대수익이 0과 다른가 (|t|>2면 우연으로 보기 어렵다)

    @property
    def profitable(self) -> bool:
        """수수료를 넘겼는가. 이것만으로는 부족하다 — survives를 쓰세요."""
        return self.net_per_trade > 0

    @property
    def survives(self) -> bool:
        """수수료를 넘겼고, 그게 우연이라고 보기 어려운가.

        평균만 보면 안 된다. 1회 수익의 표준편차가 3.5%인데 평균이 +0.05%면
        표본을 다시 뽑을 때마다 부호가 바뀐다. t를 함께 봐야 판단이 선다.
        """
        return self.net_per_trade > 0 and self.t_stat >= 2.0


@dataclass
class MetaResult:
    folds: list[FoldResult] = field(default_factory=list)
    feature_importance: list[tuple[str, float]] = field(default_factory=list)
    threshold: float = 0.5
    oos_predictions: dict[int, float] = field(default_factory=dict)   # 표본 index → 확률
    edges: list[EdgeCheck] = field(default_factory=list)
    round_trip_cost: float = 0.0

    @property
    def mean_lift(self) -> float:
        return sum(f.lift for f in self.folds) / len(self.folds) if self.folds else 0.0

    @property
    def mean_base(self) -> float:
        return sum(f.base_rate for f in self.folds) / len(self.folds) if self.folds else 0.0

    @property
    def mean_filtered(self) -> float:
        return sum(f.filtered_rate for f in self.folds) / len(self.folds) if self.folds else 0.0

    @property
    def total_taken(self) -> int:
        return sum(f.taken for f in self.folds)

    def report(self) -> str:
        lines = [
            "═" * 70,
            "  메타라벨링 — 모델이 신호를 걸러낼 수 있는가",
            "═" * 70,
            f"  {'구간':>4}{'학습':>7}{'검증':>7}{'기본승률':>10}{'필터후':>10}{'취함':>7}{'개선':>9}",
            "─" * 70,
        ]
        for f in self.folds:
            lines.append(
                f"  {f.fold:>4}{f.train_size:>7}{f.test_size:>7}"
                f"{f.base_rate:>9.1f}%{f.filtered_rate:>9.1f}%{f.taken:>7}{f.lift:>+8.1f}%p"
            )
        lines += [
            "─" * 70,
            f"  평균 기본 승률   {self.mean_base:>6.1f}%",
            f"  평균 필터 후     {self.mean_filtered:>6.1f}%",
            f"  평균 개선        {self.mean_lift:>+6.1f}%p   ← 이게 양수여야 의미가 있다",
            f"  모델이 취한 신호  {self.total_taken:>6}",
            "═" * 70,
        ]

        if self.feature_importance:
            lines += ["  특징 중요도 (상위 6)", "  " + "─" * 66]
            for name, score in self.feature_importance[:6]:
                bar = "█" * int(score * 60)
                lines.append(f"  {name:<18}{score:>6.3f}  {bar}")
            lines.append("═" * 70)

        if self.edges:
            lines += [
                "  실제 손익 — 승률이 아니라 돈으로 본다",
                f"  왕복 비용 {self.round_trip_cost * 100:.3f}%  ← 1회 기대수익이 이걸 넘어야 번다",
                "  " + "─" * 66,
                "  " + _pad("구분", 4) + " " * 8 + _pad("건수", 7)
                + _pad("1회(수수료 전)", 16) + _pad("1회(수수료 후)", 16) + _pad("t", 8),
            ]
            for edge in self.edges:
                if edge.survives:
                    mark = "✓"
                elif edge.profitable:
                    mark = "?"      # 수수료는 넘었지만 우연과 구별이 안 된다
                else:
                    mark = "✗"
                label = edge.label + " " * max(0, 12 - _display_width(edge.label))
                lines.append(
                    f"  {label}{edge.count:>7}"
                    f"{edge.gross_per_trade:>+15.3f}%{edge.net_per_trade:>+15.3f}%"
                    f"{edge.t_stat:>8.2f} {mark}"
                )
            lines.append("  ✓ 통과   ? 수수료는 넘었으나 우연과 구별 불가(|t|<2)   ✗ 손실")
            lines.append("═" * 70)

        lines += self._verdict()
        lines.append("  ※ purging·embargo를 적용한 검증 구간 성적입니다. 학습 구간이 아닙니다.")
        return "\n".join(lines)

    def _verdict(self) -> list[str]:
        """승률과 손익을 함께 보고 결론을 낸다. 손익이 승률보다 우선한다."""
        if not self.edges:
            if self.mean_lift <= 0:
                return ["  ✗ 모델이 신호를 개선하지 못했습니다. 특징에 정보가 없다는 뜻입니다.",
                        "  ※ 손익 판정을 하려면 train()에 candles를 넘기세요."]
            return ["  ⚠ 승률만으로는 판단할 수 없습니다. train()에 candles를 넘겨 손익을 보세요."]

        base = self.edges[0]
        candidates = self.edges[1:]
        passing = [e for e in candidates if e.survives]
        best = max(passing or candidates, key=lambda e: e.net_per_trade, default=None)
        if best is None:
            return ["  ✗ 모델이 취한 신호가 없습니다."]

        out = []
        if best.survives:
            out.append(f"  ✓ 필터 후 1회 기대수익 +{best.net_per_trade:.3f}%, t={best.t_stat:.2f}"
                       f" ({best.label}, {best.count}건). 우연으로 보기 어렵습니다.")
            out += self._monotonicity_warning()
            out.append("  ※ 그래도 --seed를 바꿔가며 재현되는지, 백테스트·몬테카를로가 같은 말을"
                       " 하는지 확인하세요.")
            return out

        if best.profitable:
            out.append(f"  ? 수수료는 넘었지만 우연과 구별되지 않습니다"
                       f" ({best.label}: {best.net_per_trade:+.3f}%/회, t={best.t_stat:.2f}).")
            out.append(f"  1회 수익의 흔들림에 비해 평균이 너무 작습니다. {best.count}건으로는"
                       " 부호조차 확신할 수 없습니다.")
            out.append("  → 여기서 실전에 넣지 마세요. 할 일은 둘입니다:")
            out.append("     1) --seed를 여러 개 돌려 평균이 유지되는지 본다"
                       " (한 번의 +값은 표본 추출 운입니다)")
            out.append(f"     2) 비용을 깎는다. 메이커로 넣어 왕복 {self.round_trip_cost * 100:.2f}%를"
                       " 줄이면 같은 우위가 살아납니다")
            return out + self._monotonicity_warning()

        out.append(f"  ✗ 어떤 임계값에서도 비용을 넘지 못했습니다"
                   f" (최선 {best.label}: {best.net_per_trade:+.3f}%/회).")
        cost_pct = self.round_trip_cost * 100
        if base.gross_per_trade <= 0:
            out.append(f"  원인: 1차 전략은 수수료를 빼기 전에도 지고 있습니다"
                       f" ({base.gross_per_trade:+.3f}%/회).")
            out.append("  필터로 고칠 수 있는 문제가 아닙니다. 방향 자체가 틀렸다는 뜻입니다.")
            out.append("  → 반대로 뒤집어도 수수료 때문에 못 법니다. 다른 신호를 찾으세요.")
        elif base.gross_per_trade < cost_pct:
            need = cost_pct / base.gross_per_trade
            out.append(f"  원인: 1차 전략의 수수료 전 우위가 {base.gross_per_trade:+.3f}%/회뿐입니다.")
            out.append(f"  비용을 넘으려면 모델이 우위를 {need:.1f}배로 키워야 하는데,"
                       " 그건 필터가 할 수 있는 일이 아닙니다.")
            out.append("  → 모델을 손보지 말고 **1차 전략의 수수료 전 우위**부터 만드세요.")
        return out + self._monotonicity_warning()

    def _monotonicity_warning(self) -> list[str]:
        """임계값을 올릴수록 나빠지면, 합격이든 불합격이든 반드시 말해야 한다.

        합격 판정 뒤에 숨기면 도구가 스스로를 속인다.
        모델의 확신이 높을수록 성적이 나쁘다는 건 확신이 실제 우위와 무관하다는 뜻이다.
        """
        tail = self.edges[1:]
        if len(tail) < 3 or tail[-1].gross_per_trade >= tail[0].gross_per_trade:
            return []
        return [
            "  ⚠ 경고: 임계값을 올릴수록 성적이 나빠집니다"
            f" ({tail[0].label} {tail[0].gross_per_trade:+.3f}%"
            f" → {tail[-1].label} {tail[-1].gross_per_trade:+.3f}%, 수수료 전).",
            "     모델이 확신할수록 결과가 나쁘다는 건, 그 확신이 실제 우위와 무관하다는 신호입니다.",
            "     낮은 임계값의 좋은 성적은 실력이 아니라 표본 추출 운일 수 있습니다.",
            "     판단 전에 --seed를 바꿔가며 결과가 유지되는지 반드시 확인하세요.",
        ]


def _gross_returns(candles: list[Candle], samples: list[Sample]) -> list[float]:
    """표본별 실제 가격 수익률(수수료 전). 진입 종가 → 라벨 확정 봉 종가."""
    out = []
    for sample in samples:
        entry = candles[sample.index].close
        exit_ = candles[sample.exit_index].close
        change = (exit_ - entry) / entry if entry else 0.0
        out.append(change * sample.side.sign)
    return out


def _measure_edges(
    returns: list[float],
    oos: dict[int, float],
    cost: float,
    thresholds: tuple[float, ...],
) -> list[EdgeCheck]:
    """임계값별 1회 기대수익을 수수료 전/후로 계산한다."""
    indices = sorted(oos)
    if not indices:
        return []

    def check(label: str, selected: list[int]) -> EdgeCheck | None:
        if not selected:
            return None
        values = [returns[i] for i in selected]
        gross = statistics.fmean(values)
        net = gross - cost
        t_stat = 0.0
        if len(values) > 2:
            spread = statistics.stdev(values)
            if spread > 0:
                t_stat = net / (spread / math.sqrt(len(values)))
        return EdgeCheck(label, len(selected), gross * 100, net * 100, t_stat)

    edges = [check("필터 없음", indices)]
    for threshold in thresholds:
        edges.append(check(f"임계 {threshold:.2f}", [i for i in indices if oos[i] >= threshold]))
    return [e for e in edges if e is not None]


def train(
    samples: list[Sample],
    *,
    candles: list[Candle] | None = None,
    config: Config | None = None,
    folds: int = 4,
    embargo_bars: int = 24,
    threshold: float = 0.55,
    seed: int = 42,
    thresholds: tuple[float, ...] = (0.50, 0.55, 0.60, 0.65, 0.70),
) -> MetaResult:
    """purged walk-forward로 학습하고 검증 구간 성적만 보고한다.

    candles를 넘기면 승률뿐 아니라 **수수료를 뺀 1회 기대수익**까지 계산한다.
    승률 개선은 수수료 앞에서 자주 무의미해지므로, 판단은 그쪽으로 해야 한다.
    """
    try:
        import numpy as np
        from sklearn.ensemble import GradientBoostingClassifier
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "메타라벨링에는 numpy와 scikit-learn이 필요합니다: pip install -r requirements-ml.txt"
        ) from exc

    if len(samples) < 60:
        raise ValueError(f"표본이 {len(samples)}개뿐입니다. 최소 60개는 필요합니다.")

    X = np.array([s.features for s in samples], dtype=float)
    y = np.array([s.label for s in samples], dtype=int)
    spans = [s.span for s in samples]

    result = MetaResult(threshold=threshold)
    importances = np.zeros(X.shape[1])

    for k, split in enumerate(purged_walk_forward(spans, folds=folds, embargo_bars=embargo_bars)):
        train_idx, test_idx = split.train, split.test
        if len(set(y[train_idx])) < 2:
            log.warning("구간 %d: 학습 라벨이 한 종류뿐이라 건너뜁니다", k)
            continue

        model = GradientBoostingClassifier(
            n_estimators=120, max_depth=3, learning_rate=0.05,
            subsample=0.8, random_state=seed,
        )
        model.fit(X[train_idx], y[train_idx])
        proba = model.predict_proba(X[test_idx])[:, 1]

        for pos, sample_i in enumerate(test_idx):
            result.oos_predictions[sample_i] = float(proba[pos])

        base_rate = float(y[test_idx].mean()) * 100
        taken = proba >= threshold
        filtered_rate = float(y[test_idx][taken].mean()) * 100 if taken.any() else 0.0

        result.folds.append(
            FoldResult(k, len(train_idx), len(test_idx), base_rate,
                       filtered_rate, int(taken.sum()), filtered_rate - base_rate)
        )
        importances += model.feature_importances_

    if result.folds:
        importances /= len(result.folds)
        result.feature_importance = sorted(
            zip(FEATURE_NAMES, importances.tolist()), key=lambda x: -x[1]
        )

    if candles is not None:
        cfg = config or Config()
        result.round_trip_cost = 2 * (cfg.exchange.taker_fee + cfg.exchange.slippage)
        result.edges = _measure_edges(
            _gross_returns(candles, samples), result.oos_predictions,
            result.round_trip_cost, thresholds,
        )
    return result
