"""수수료 바닥선 — 이 타임프레임에서 애초에 이길 수 있는가.

전략을 고르기 전에 답해야 할 질문이 있다. **이 타임프레임에서 수수료를
넘는 것이 산술적으로 가능한가?**

가격 움직임은 시간의 **제곱근**에 비례해 커진다(랜덤워크). 반면 수수료는
매매할 때마다 같은 금액이 나간다. 그래서 타임프레임을 반으로 줄이면
잡을 수 있는 움직임은 √2배만 줄지만 매매 횟수는 2배가 된다.
비용 부담이 √2배로 늘어난다.

이 모듈은 그 비율을 실제 데이터로 재고, **손익분기 승률**로 환산한다.
손익비 1:1이라면:

    p·m − (1−p)·m = 비용   →   p = 0.5 + 비용 / (2m)

m이 작아질수록(짧은 타임프레임) p가 1에 가까워진다. 어느 지점부터는
100%를 넘어가는데, 그건 **어떤 전략으로도 불가능하다**는 뜻이다.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from ..config import Config
from ..data.resample import TIMEFRAME_MS, resample
from ..models import Candle


@dataclass
class Row:
    timeframe: str
    bars: int
    mean_move: float        # 봉당 평균 |수익률| (%)
    median_move: float
    cost: float             # 왕복 비용 (%)

    @property
    def eaten(self) -> float:
        """수수료가 평균 움직임의 몇 %를 먹는가."""
        return self.cost / self.mean_move * 100 if self.mean_move > 0 else float("inf")

    @property
    def breakeven_win_rate(self) -> float:
        """손익비 1:1일 때 본전이 되는 승률 (%)."""
        if self.mean_move <= 0:
            return 100.0
        return (0.5 + self.cost / (2 * self.mean_move)) * 100

    @property
    def possible(self) -> bool:
        return self.breakeven_win_rate < 100.0

    @property
    def bars_per_year(self) -> float:
        return 365.25 * 24 * 3600_000 / TIMEFRAME_MS[self.timeframe]

    @property
    def annual_cost_if_always_trading(self) -> float:
        """매 봉 진입·청산하면 1년에 자본의 몇 %가 수수료로 나가는가."""
        return self.bars_per_year * self.cost


@dataclass
class Scenario:
    """체결 방식 하나. 진입·청산을 각각 메이커로 낼지 테이커로 낼지."""

    label: str
    cost: float          # 왕복 비용 (%)
    caveat: str = ""     # 이 방식이 공짜가 아닌 이유


def scenarios(cfg: Config, *, win_rate: float = 0.45) -> list[Scenario]:
    """현실적인 체결 방식 넷.

    **메이커 전용이 공짜가 아닌 이유가 핵심이다.** 손절을 지정가로 걸 수는 없다.
    가격이 반대로 가면 지정가는 체결되지 않고 그냥 지나가며, 손실은 계속 커진다.

    그래서 진입과 익절만 지정가로 낼 수 있고 **손절은 반드시 시장가**다.
    그러면 왕복 비용이 승률에 따라 달라진다 — 이긴 매매는 익절(메이커)로,
    진 매매는 손절(테이커)로 끝나기 때문이다. win_rate는 그 혼합 비율이다.
    기본값 0.45는 이 저장소에서 실측한 전략들의 승률대(37~49%)에서 잡았다.

    손절을 아예 포기하면 왕복이 메이커 두 번으로 내려가지만, 그건 비용을
    아낀 게 아니라 **손실 한도를 포기한 것**이다. 같은 표에 두되 그렇게 적는다.
    """
    maker = cfg.exchange.maker_fee
    taker = cfg.exchange.taker_fee
    slip = cfg.exchange.slippage
    blended = maker + win_rate * maker + (1 - win_rate) * (taker + slip)
    return [
        Scenario("시장가 (테이커)", 2 * (taker + slip) * 100,
                 "가장 확실하지만 가장 비쌉니다."),
        Scenario("진입만 지정가", (maker + taker + slip) * 100,
                 "진입을 놓칠 수 있습니다 — 그것도 하필 크게 간 것들을."),
        Scenario(f"진입·익절 지정가 (승률 {win_rate:.0%})", blended * 100,
                 "손절은 시장가입니다. 승률이 낮을수록 이 값이 올라갑니다."),
        Scenario("손절 포기 (메이커 전용)", 2 * maker * 100,
                 "⚠ 비용을 아낀 게 아니라 손실 한도를 포기한 것입니다. "
                 "한 번의 큰 역행이 계좌를 끝냅니다."),
    ]


@dataclass
class CostFloor:
    rows: list[Row] = field(default_factory=list)
    cost: float = 0.0
    fee_scenarios: list[Scenario] = field(default_factory=list)

    @property
    def scaling_exponent(self) -> float:
        """움직임이 시간의 몇 승에 비례하는가. 랜덤워크면 0.5다.

        0.5보다 크면 추세성이, 작으면 평균회귀성이 있다는 뜻이다.
        """
        points = [
            (math.log(TIMEFRAME_MS[r.timeframe]), math.log(r.mean_move))
            for r in self.rows if r.mean_move > 0
        ]
        if len(points) < 2:
            return 0.0
        mean_x = statistics.fmean(x for x, _ in points)
        mean_y = statistics.fmean(y for _, y in points)
        denom = sum((x - mean_x) ** 2 for x, _ in points)
        if denom <= 0:
            return 0.0
        return sum((x - mean_x) * (y - mean_y) for x, y in points) / denom

    def report(self) -> str:
        lines = [
            "═" * 78,
            "  수수료 바닥선 — 이 타임프레임에서 애초에 이길 수 있는가",
            "═" * 78,
            f"  왕복 비용 {self.cost:.3f}%",
            "─" * 78,
            f"  {'TF':<6}{'봉수':>9}{'봉당 움직임':>14}{'수수료가 먹는 몫':>18}"
            f"{'손익분기 승률':>15}",
            "─" * 78,
        ]
        for row in self.rows:
            mark = "" if row.possible else "  ← 불가능"
            lines.append(
                f"  {row.timeframe:<6}{row.bars:>9,}{row.mean_move:>13.3f}%"
                f"{row.eaten:>17.0f}%{row.breakeven_win_rate:>14.1f}%{mark}"
            )
        lines.append("═" * 78)
        if self.fee_scenarios:
            lines += self._scenario_table()
        return "\n".join(lines + self._verdict())

    def _scenario_table(self) -> list[str]:
        """체결 방식을 바꾸면 손익분기 승률이 얼마나 내려가는가."""
        picks = [r for r in self.rows if r.timeframe in ("1h", "4h", "1d")] or self.rows[:3]
        lines = [
            "  체결 방식을 바꾸면 — 손익분기 승률",
            "  " + "─" * 74,
            "  " + f"{'방식':<24}{'왕복':>8}"
            + "".join(f"{r.timeframe:>9}" for r in picks),
            "  " + "─" * 74,
        ]
        for scenario in self.fee_scenarios:
            cells = ""
            for row in picks:
                rate = (0.5 + scenario.cost / (2 * row.mean_move)) * 100
                cells += f"{rate:>8.1f}%"
            lines.append(f"  {scenario.label:<24}{scenario.cost:>7.3f}%{cells}")
        lines.append("  " + "─" * 74)
        for scenario in self.fee_scenarios:
            if scenario.caveat:
                lines.append(f"  {scenario.label}: {scenario.caveat}")
        lines.append("═" * 78)
        return lines

    def _verdict(self) -> list[str]:
        if not self.rows:
            return ["  잴 것이 없습니다."]

        out = []
        exponent = self.scaling_exponent
        out.append(f"  움직임이 시간의 {exponent:.2f}승에 비례합니다"
                   f" (랜덤워크는 0.50).")
        if exponent > 0.55:
            out.append("  0.5보다 큽니다 — 약한 추세성이 있다는 뜻입니다.")
        elif exponent < 0.45:
            out.append("  0.5보다 작습니다 — 약한 평균회귀성이 있다는 뜻입니다.")
        else:
            out.append("  거의 정확히 랜덤워크입니다. 방향에서 우위를 찾기 어렵습니다.")

        out.append("")
        out.append("  **움직임은 시간의 제곱근으로 늘고, 수수료는 매매마다 그대로입니다.**")
        out.append("  타임프레임을 반으로 줄이면 잡을 움직임은 √2배만 줄고"
                   " 매매 횟수는 2배가 됩니다.")

        impossible = [r for r in self.rows if not r.possible]
        if impossible:
            out.append(f"  ✗ {', '.join(r.timeframe for r in impossible)}에서는"
                       " 손익분기 승률이 100%를 넘습니다. 어떤 전략으로도 불가능합니다.")

        hardest = max(self.rows, key=lambda r: r.breakeven_win_rate)
        easiest = min(self.rows, key=lambda r: r.breakeven_win_rate)
        out.append(f"  가장 불리 {hardest.timeframe}: 승률 {hardest.breakeven_win_rate:.1f}% 필요"
                   f"   /   가장 유리 {easiest.timeframe}: {easiest.breakeven_win_rate:.1f}%")
        out.append("  ※ 손익비 1:1 기준입니다. 익절을 손절보다 크게 잡으면 필요 승률은"
                   " 내려가지만, 그만큼 승률 자체도 내려갑니다.")
        return out


def analyse(
    candles: list[Candle],
    cfg: Config,
    *,
    timeframes: tuple[str, ...] = ("1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d"),
) -> CostFloor:
    """타임프레임별로 '봉당 움직임 대비 수수료'를 잰다.

    candles는 가장 짧은 타임프레임이어야 한다. 여기서 상위로만 리샘플한다.
    """
    cost = 2 * (cfg.exchange.taker_fee + cfg.exchange.slippage)
    result = CostFloor(cost=cost * 100, fee_scenarios=scenarios(cfg))

    for name in timeframes:
        if name not in TIMEFRAME_MS:
            raise ValueError(f"지원하지 않는 타임프레임: {name}")
        target = TIMEFRAME_MS[name]
        source_step = candles[1].ts - candles[0].ts if len(candles) > 1 else target
        if target < source_step:
            continue        # 원본보다 짧은 타임프레임은 만들 수 없다
        series = candles if target == source_step else resample(candles, target)
        if len(series) < 30:
            continue

        moves = [
            abs(b.close - a.close) / a.close
            for a, b in zip(series, series[1:]) if a.close > 0
        ]
        if not moves:
            continue
        result.rows.append(Row(
            name, len(series),
            statistics.fmean(moves) * 100,
            statistics.median(moves) * 100,
            cost * 100,
        ))
    return result
