"""그리드 매매 — 선이 아니라 면으로 진입한다.

방향을 맞히지 않는다. 현재가 아래에 매수 지정가를 여러 개 깔고, 채워지면
한 칸 위에 매도 지정가를 건다. 가격이 오르내리기만 하면 그 왕복을 먹는다.

**전부 지정가라 메이커 수수료만 낸다**(왕복 0.04%). 이 저장소에서 잰
1시간봉 손익분기 승률이 시장가로는 76.5%인데, 그리드는 애초에 승률 게임이
아니다. 격자 한 칸이 수수료보다 크기만 하면 왕복마다 이긴다.

**대신 추세에 진다.** 가격이 한 방향으로 계속 가면 매수 격자가 전부 채워지고
반등이 안 와서 평가손이 쌓인다. 그래서 하루 마감(강제 청산)이 필요하고,
그 청산은 시장가라 테이커 수수료를 낸다. 이 전략의 손익은 결국
**"진동으로 번 것 − 추세로 잃은 것"**이다.

⚠ 봉 안의 순서를 알 수 없다는 문제가 있다.
   OHLC만으로는 한 봉 안에서 저가를 먼저 찍었는지 고가를 먼저 찍었는지
   알 수 없다. 그리드는 그 순서가 손익을 결정하기 때문에 이게 치명적이다.
   여기서는 **보수적으로** 잡는다: 같은 봉 안에서 산 물량은 그 봉에서
   팔 수 없다. 낙관적으로 잡으면 없는 왕복이 생겨 수익이 부풀려진다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..models import Candle


@dataclass(frozen=True)
class GridSpec:
    """격자 설정."""

    levels: int = 5                 # 한쪽 격자 개수
    step_atr: float = 0.25          # 격자 간격 (ATR 배수)
    size_per_level: float = 0.20    # 레벨당 명목가 (자본 대비)
    close_daily: bool = True        # 하루 끝에 전량 청산
    max_hold_bars: int = 24         # 이 봉 수를 넘긴 물량은 강제 청산

    def __post_init__(self) -> None:
        if self.levels < 1:
            raise ValueError("levels는 1 이상이어야 합니다.")
        if self.step_atr <= 0:
            raise ValueError("step_atr는 0보다 커야 합니다.")
        if self.size_per_level <= 0:
            raise ValueError("size_per_level은 0보다 커야 합니다.")


@dataclass
class Lot:
    """채워진 매수 격자 하나.

    size는 **진입 시점 자본 대비 명목가**가 아니라 절대 명목가다.
    자본이 줄면 새 격자도 작아져야 한다 — 초기 자본에 고정하면
    자본이 반토막 난 뒤에도 같은 크기로 사서 순식간에 파산한다.
    """

    price: float
    size: float          # 절대 명목가 (진입 시점 자본 × size_per_level)
    opened_at: int
    target: float


@dataclass
class GridResult:
    equity: list[float] = field(default_factory=list)
    round_trips: int = 0            # 지정가로 사서 지정가로 판 횟수
    forced_closes: int = 0          # 시간 초과·하루 마감으로 시장가 청산한 횟수
    maker_fees: float = 0.0         # 자본 대비 %
    taker_fees: float = 0.0
    daily: list[float] = field(default_factory=list)   # 하루 수익률 (%)
    max_inventory: int = 0
    ruined: bool = False

    @property
    def return_pct(self) -> float:
        if len(self.equity) < 2 or self.equity[0] <= 0:
            return 0.0
        return (self.equity[-1] / self.equity[0] - 1) * 100

    @property
    def max_drawdown_pct(self) -> float:
        peak, worst = -math.inf, 0.0
        for value in self.equity:
            peak = max(peak, value)
            if peak > 0:
                worst = max(worst, (peak - value) / peak)
        return worst * 100

    @property
    def win_days(self) -> int:
        return sum(1 for d in self.daily if d > 0)

    @property
    def mean_daily(self) -> float:
        return sum(self.daily) / len(self.daily) if self.daily else 0.0

    def days_over(self, target_pct: float) -> int:
        """하루 수익이 target_pct% 이상이었던 날 수."""
        return sum(1 for d in self.daily if d >= target_pct)

    def report(self, spec: GridSpec, cost: dict[str, float]) -> str:
        lines = [
            "═" * 76,
            "  그리드 매매 — 선이 아니라 면으로 진입",
            "═" * 76,
            f"  격자 {spec.levels}단  간격 {spec.step_atr:.2f} ATR"
            f"  레벨당 {spec.size_per_level:.0%}"
            f"  보유 한도 {spec.max_hold_bars}봉"
            + ("  하루 마감 청산" if spec.close_daily else ""),
            f"  메이커 {cost['maker']*100:.3f}%   테이커+슬리피지 "
            f"{(cost['taker'] + cost['slippage'])*100:.3f}%",
            "─" * 76,
            f"  수익률          {self.return_pct:>+10.2f}%",
            f"  최대 낙폭       {self.max_drawdown_pct:>10.2f}%",
            f"  지정가 왕복     {self.round_trips:>10,}회   "
            f"(메이커 수수료 {self.maker_fees:.1f}%)",
            f"  강제 청산       {self.forced_closes:>10,}회   "
            f"(테이커 수수료 {self.taker_fees:.1f}%)",
            f"  최대 보유 격자  {self.max_inventory:>10}단",
            "─" * 76,
        ]
        if self.daily:
            ordered = sorted(self.daily)
            n = len(ordered)
            lines += [
                f"  거래일 {n:,}일   흑자 {self.win_days:,}일 "
                f"({self.win_days / n * 100:.1f}%)",
                f"  하루 수익  평균 {self.mean_daily:+.3f}%"
                f"   중앙값 {ordered[n // 2]:+.3f}%"
                f"   최악 {ordered[0]:+.2f}%   최선 {ordered[-1]:+.2f}%",
                f"  하루 3% 이상  {self.days_over(3.0):,}일 "
                f"({self.days_over(3.0) / n * 100:.1f}%)",
                f"  하루 1% 이상  {self.days_over(1.0):,}일 "
                f"({self.days_over(1.0) / n * 100:.1f}%)",
            ]
        lines.append("═" * 76)
        return "\n".join(lines + self._verdict())

    def _verdict(self) -> list[str]:
        out = []
        if not self.daily:
            return ["  거래일이 없습니다."]

        if self.ruined:
            out.append("  ✗ 파산했습니다 (자본 0).")
        elif self.return_pct <= 0:
            out.append(f"  ✗ 누적이 {self.return_pct:+.2f}%입니다.")
        if self.forced_closes > self.round_trips:
            out.append(f"  ⚠ 강제 청산({self.forced_closes:,})이 지정가 왕복"
                       f"({self.round_trips:,})보다 많습니다.")
            out.append("     격자가 채워진 뒤 반등이 안 온다는 뜻입니다."
                       " 추세에 계속 밟히고 있습니다.")
        if self.taker_fees > self.maker_fees:
            out.append(f"  ⚠ 테이커 수수료({self.taker_fees:.1f}%)가 메이커"
                       f"({self.maker_fees:.1f}%)보다 큽니다.")
            out.append("     지정가만 쓰려고 만든 전략인데 강제 청산이 그걸 무너뜨립니다.")

        target_days = self.days_over(3.0)
        share = target_days / len(self.daily) * 100
        out.append(f"  하루 3% 목표를 넘은 날이 {share:.1f}%입니다"
                   f" (평균은 {self.mean_daily:+.3f}%).")
        return out


def simulate(
    candles: list[Candle],
    spec: GridSpec,
    *,
    maker_fee: float = 0.0002,
    taker_fee: float = 0.0006,
    slippage: float = 0.0005,
    atr_period: int = 14,
    atr_window: int = 200,
) -> GridResult:
    """격자를 깔고 채워지는 대로 왕복시킨다.

    격자 기준선은 **직전 봉 종가**다. 현재 봉을 보고 격자를 놓으면 미래참조다.
    간격은 직전 봉까지의 ATR로 정한다.

    한 봉 안의 순서를 모르므로 보수적으로 잡는다.
      1. 먼저 **이전 봉에서** 산 물량의 매도 지정가를 확인한다
      2. 그다음 이번 봉의 매수 격자를 채운다
      3. 이번 봉에서 산 것은 이번 봉에서 못 판다
    낙관적으로 잡으면 없는 왕복이 생겨 수익이 부풀려진다.
    """
    from ..data.indicators import atr

    result = GridResult(equity=[1.0])
    if len(candles) < atr_period + 2:
        return result

    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]

    inventory: list[Lot] = []
    equity = 1.0
    day_start_equity = 1.0
    current_day = candles[atr_period + 1].ts // 86_400_000

    def close_lot(lot: Lot, price: float, fee_rate: float) -> float:
        """청산 손익(절대액). 수수료는 청산 쪽만 — 진입 수수료는 진입 때 뺐다."""
        gross = (price - lot.price) / lot.price * lot.size
        return gross - lot.size * fee_rate

    for i in range(atr_period + 1, len(candles)):
        candle = candles[i]
        day = candle.ts // 86_400_000
        window = slice(max(0, i - atr_window), i)          # 직전 봉까지만
        atr_line = atr(highs[window], lows[window], closes[window], atr_period)
        step_price = (atr_line[-1] or 0.0) * spec.step_atr if atr_line else 0.0

        # --- 1. 기존 물량의 매도 지정가 (메이커) ---
        remaining: list[Lot] = []
        for lot in inventory:
            if candle.high >= lot.target:
                equity += close_lot(lot, lot.target, maker_fee)
                result.maker_fees += lot.size * maker_fee * 100
                result.round_trips += 1
            else:
                remaining.append(lot)
        inventory = remaining

        # --- 2. 보유 한도 초과분 강제 청산 (시장가) ---
        keep: list[Lot] = []
        for lot in inventory:
            if i - lot.opened_at >= spec.max_hold_bars:
                equity += close_lot(lot, candle.close, taker_fee + slippage)
                result.taker_fees += lot.size * (taker_fee + slippage) * 100
                result.forced_closes += 1
            else:
                keep.append(lot)
        inventory = keep

        # --- 3. 이번 봉 매수 격자 (메이커). 이번 봉에서 산 건 이번 봉에 못 판다 ---
        if step_price > 0:
            base = candles[i - 1].close
            for level in range(1, spec.levels + 1):
                # 격자를 동시에 levels개보다 많이 들면 안 된다. 기준선이 계속
                # 내려가면 새 가격대에 격자가 또 생겨 무한히 쌓인다 —
                # 그러면 노출이 설정한 한도를 넘어 리스크 계산이 무의미해진다.
                if len(inventory) >= spec.levels:
                    break
                price = base - step_price * level
                if price <= 0 or candle.low > price:
                    continue
                if any(abs(lot.price - price) < step_price * 0.5 for lot in inventory):
                    continue        # 이미 그 칸을 들고 있다
                notional = max(0.0, equity) * spec.size_per_level
                if notional <= 0:
                    break
                inventory.append(Lot(price, notional, i, price + step_price))
                result.maker_fees += notional * maker_fee * 100
                equity -= notional * maker_fee

        result.max_inventory = max(result.max_inventory, len(inventory))

        # --- 4. 하루 마감 청산 (시장가) ---
        # 마지막 봉을 '하루 마감'으로 처리하면 안 된다. 데이터를 자를 때마다
        # 그 지점 자본이 달라져서, 뒤 봉이 앞 구간 결과를 바꾸는 것처럼 보인다.
        # 미실현 평가액은 아래에서 어차피 반영되므로 잘라낼 이유도 없다.
        next_day = i + 1 < len(candles) and candles[i + 1].ts // 86_400_000 != day
        if spec.close_daily and next_day and inventory:
            for lot in inventory:
                equity += close_lot(lot, candle.close, taker_fee + slippage)
                result.taker_fees += lot.size * (taker_fee + slippage) * 100
                result.forced_closes += 1
            inventory = []

        # 파산하면 거기서 끝이다. 마이너스로 두면 그 뒤 반등에 계좌가 되살아난다.
        if equity <= 0:
            equity = 0.0
            inventory = []
            result.ruined = True

        unrealised = sum(
            (candle.close - lot.price) / lot.price * lot.size for lot in inventory
        )
        result.equity.append(max(0.0, equity + unrealised))

        if day != current_day:
            if day_start_equity > 0:
                result.daily.append((equity / day_start_equity - 1) * 100)
            day_start_equity = equity
            current_day = day

    return result
