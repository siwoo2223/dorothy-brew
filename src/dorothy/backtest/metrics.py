"""백테스트 성과 지표.

수익률만 보면 안 된다. MDD(최대낙폭)와 거래 횟수를 같이 봐야
"운 좋은 한 방"과 "재현 가능한 우위"를 구분할 수 있다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..models import Trade


@dataclass
class Metrics:
    initial_equity: float
    final_equity: float
    trades: int
    wins: int
    losses: int
    gross_profit: float
    gross_loss: float
    total_fees: float
    max_drawdown_pct: float
    max_consecutive_losses: int

    @property
    def net_pnl(self) -> float:
        return self.final_equity - self.initial_equity

    @property
    def return_pct(self) -> float:
        return self.net_pnl / self.initial_equity * 100 if self.initial_equity else 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades * 100 if self.trades else 0.0

    @property
    def profit_factor(self) -> float:
        """총이익 ÷ 총손실. 1.0 미만이면 손해 보는 전략."""
        return self.gross_profit / self.gross_loss if self.gross_loss else math.inf

    @property
    def expectancy(self) -> float:
        """1회 매매당 기대 손익."""
        return self.net_pnl / self.trades if self.trades else 0.0

    def report(self) -> str:
        lines = [
            "═" * 46,
            "  백테스트 결과",
            "═" * 46,
            f"  시작 자본      {self.initial_equity:>14,.2f}",
            f"  종료 자본      {self.final_equity:>14,.2f}",
            f"  순손익         {self.net_pnl:>14,.2f}  ({self.return_pct:+.2f}%)",
            f"  지불 수수료    {self.total_fees:>14,.2f}",
            "─" * 46,
            f"  거래 횟수      {self.trades:>14,}",
            f"  승/패          {self.wins:>7,} / {self.losses:<6,}  ({self.win_rate:.1f}%)",
            f"  손익비(PF)     {self.profit_factor:>14.2f}",
            f"  1회 기대손익   {self.expectancy:>14,.2f}",
            f"  최대낙폭(MDD)  {self.max_drawdown_pct:>13.2f}%",
            f"  최대연속손실   {self.max_consecutive_losses:>14,}",
            "═" * 46,
        ]
        if self.trades == 0:
            lines.append("  ⚠ 거래가 한 건도 없습니다. 파라미터나 기간을 확인하세요.")
        elif self.trades < 30:
            lines.append("  ⚠ 표본이 30건 미만입니다. 통계적으로 신뢰하기 어렵습니다.")
        if self.profit_factor < 1.2 and self.trades:
            lines.append("  ⚠ PF 1.2 미만 — 수수료·슬리피지 변동만으로 손실 전환될 수 있습니다.")
        if self.max_drawdown_pct > 30:
            lines.append("  ⚠ MDD 30% 초과 — 실전에서 이 낙폭을 견딜 수 있을지 자문해보세요.")
        return "\n".join(lines)


def compute(
    trades: list[Trade], equity_curve: list[tuple[int, float]], initial_equity: float
) -> Metrics:
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]

    peak = initial_equity
    max_dd = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100)

    streak = worst_streak = 0
    for t in trades:
        streak = streak + 1 if t.net_pnl <= 0 else 0
        worst_streak = max(worst_streak, streak)

    return Metrics(
        initial_equity=initial_equity,
        final_equity=equity_curve[-1][1] if equity_curve else initial_equity,
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        gross_profit=sum(t.net_pnl for t in wins),
        gross_loss=abs(sum(t.net_pnl for t in losses)),
        total_fees=sum(t.fee + t.funding for t in trades),
        max_drawdown_pct=max_dd,
        max_consecutive_losses=worst_streak,
    )
