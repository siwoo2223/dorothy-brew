"""몬테카를로 시뮬레이션 — 백테스트 한 줄 대신 '분포'를 본다.

백테스트가 내놓는 "+24.3%"는 **매매가 그 순서로 일어났을 때의 결과 하나**다.
같은 매매들이 다른 순서로 왔다면 결과는 달라진다. 특히 최대낙폭은
순서에 극단적으로 민감하다 — 연패가 초반에 몰리면 계좌가 먼저 죽는다.

여기서는 실제 매매의 수익률을 **복원추출로 재배열**해 수천 가지 경로를 만든다.
그러면 "얼마 버나" 대신 답할 수 있는 것들이 생긴다:

- 중앙값은 얼마인가 (한 번의 백테스트 수치보다 훨씬 믿을 만하다)
- 나쁜 쪽 5%는 얼마인가 (이게 실제로 각오해야 할 숫자다)
- 손실로 끝날 확률은
- 계좌가 반토막 날 확률은

**이 도구는 미래를 예측하지 않는다.** "과거 매매의 성질이 유지된다면"이라는
가정 위에서 변동폭을 보여줄 뿐이다. 그 가정 자체가 틀릴 수 있다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..models import Trade


def trade_returns(trades: list[Trade], initial_equity: float) -> list[float]:
    """각 매매의 '그 시점 자본 대비' 수익률.

    포지션 크기를 자본의 일정 비율로 잡으므로, 금액이 아니라 비율이
    매매의 성질을 나타내는 단위다. 그래야 재배열해도 의미가 유지된다.
    """
    equity = initial_equity
    out: list[float] = []
    for trade in trades:
        if equity <= 0:
            break
        out.append(trade.net_pnl / equity)
        equity += trade.net_pnl
    return out


@dataclass
class MonteCarloResult:
    initial_equity: float
    runs: int
    trades_per_run: int
    finals: list[float]
    drawdowns: list[float]
    ruin_count: int

    def percentile(self, values: list[float], p: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(int(p / 100 * len(ordered)), len(ordered) - 1)
        return ordered[index]

    @property
    def median_final(self) -> float:
        return self.percentile(self.finals, 50)

    @property
    def loss_probability(self) -> float:
        if not self.finals:
            return 0.0
        return sum(1 for f in self.finals if f < self.initial_equity) / len(self.finals) * 100

    @property
    def ruin_probability(self) -> float:
        return self.ruin_count / self.runs * 100 if self.runs else 0.0

    def drawdown_probability(self, threshold: float) -> float:
        if not self.drawdowns:
            return 0.0
        return sum(1 for d in self.drawdowns if d >= threshold) / len(self.drawdowns) * 100

    def report(self) -> str:
        def pct(value: float) -> str:
            return f"{(value / self.initial_equity - 1) * 100:+.1f}%"

        lines = [
            "═" * 68,
            "  몬테카를로 — 같은 매매를 다른 순서로 수천 번",
            "═" * 68,
            f"  시작 자본 ${self.initial_equity:,.0f} · 경로 {self.runs:,}개 · "
            f"경로당 {self.trades_per_run}매매",
            "─" * 68,
            "  최종 자본 분포",
            f"    상위  5%   ${self.percentile(self.finals, 95):>10,.2f}   ({pct(self.percentile(self.finals, 95))})",
            f"    상위 25%   ${self.percentile(self.finals, 75):>10,.2f}   ({pct(self.percentile(self.finals, 75))})",
            f"    중앙값     ${self.percentile(self.finals, 50):>10,.2f}   ({pct(self.percentile(self.finals, 50))})",
            f"    하위 25%   ${self.percentile(self.finals, 25):>10,.2f}   ({pct(self.percentile(self.finals, 25))})",
            f"    하위  5%   ${self.percentile(self.finals, 5):>10,.2f}   ({pct(self.percentile(self.finals, 5))})   ← 각오할 숫자",
            "─" * 68,
            "  최대낙폭(MDD) 분포",
            f"    중앙값     {self.percentile(self.drawdowns, 50):>9.1f}%",
            f"    하위  5%   {self.percentile(self.drawdowns, 95):>9.1f}%   ← 최악 경로",
            "─" * 68,
            "  확률",
            f"    손실로 끝남          {self.loss_probability:>6.1f}%",
            f"    낙폭 20% 초과        {self.drawdown_probability(20):>6.1f}%",
            f"    낙폭 50% 초과        {self.drawdown_probability(50):>6.1f}%",
            f"    사실상 파산(-90%)    {self.ruin_probability:>6.1f}%",
            "═" * 68,
        ]

        if self.loss_probability > 40:
            lines.append("  ⚠ 손실 확률이 40%를 넘습니다. 동전 던지기에 가깝습니다.")
        if self.drawdown_probability(20) > 30:
            lines.append("  ⚠ 낙폭 20% 초과 확률이 높습니다. 실제로 그 구간을 견디실 수 있나요?")
        if self.trades_per_run < 30:
            lines.append(f"  ⚠ 원본 매매가 {self.trades_per_run}건뿐입니다. 분포도 그만큼 불안정합니다.")
        lines += [
            "  ※ '과거 매매의 성질이 유지된다면'이라는 가정 위의 숫자입니다.",
            "     그 가정이 틀리면 이 분포도 틀립니다. 예측이 아닙니다.",
            "",
            "  ⚠ **학습 구간 매매를 넣으면 그 구간의 낙관까지 그대로 물려받습니다.**",
            "     백테스트가 과최적화됐다면 이 분포도 똑같이 과최적화된 것입니다.",
            "     반드시 워크포워드를 먼저 보세요:",
            "       python -m dorothy walkforward --csv <데이터> --strategy <전략>",
            "     효율이 0.5 미만이면 위 분포는 실전 추정치가 아닙니다.",
        ]
        return "\n".join(lines)


def run(
    trades: list[Trade],
    initial_equity: float,
    *,
    runs: int = 5000,
    seed: int = 42,
    trades_per_run: int | None = None,
    ruin_threshold: float = 0.1,
) -> MonteCarloResult:
    """매매 수익률을 복원추출로 재배열해 경로를 만든다."""
    returns = trade_returns(trades, initial_equity)
    if len(returns) < 5:
        raise ValueError(
            f"매매가 {len(returns)}건뿐입니다. 최소 5건, 실질적으로는 30건 이상 필요합니다."
        )

    count = trades_per_run or len(returns)
    rng = random.Random(seed)
    finals: list[float] = []
    drawdowns: list[float] = []
    ruin = 0

    for _ in range(runs):
        equity = peak = initial_equity
        worst = 0.0
        busted = False
        for _ in range(count):
            equity *= 1 + rng.choice(returns)
            if equity <= initial_equity * ruin_threshold:
                busted = True
                equity = max(equity, 0.0)
                break
            peak = max(peak, equity)
            worst = max(worst, (peak - equity) / peak * 100)
        finals.append(equity)
        drawdowns.append(100.0 if busted else worst)
        ruin += busted

    return MonteCarloResult(initial_equity, runs, count, finals, drawdowns, ruin)
