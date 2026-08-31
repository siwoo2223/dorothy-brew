"""펀딩비 수취 (델타 중립 캐리) — 가격을 맞히지 않고 버는 구조.

**구조**
  현물 BTC를 사고, 같은 수량의 무기한선물을 숏 친다.
  가격이 오르면 현물이 벌고 선물이 잃는다. 내리면 반대다. **합은 0이다.**
  남는 것은 펀딩비뿐이다 — 펀딩이 양수면 롱이 숏에게 낸다.

  총가치 = Q·P(현물) + M(증거금) + (P_진입 - P)·Q(선물 미실현)
         = Q·P_진입 + M          ← P가 사라진다. 이게 델타 중립이다.

**이 모듈이 정직하게 말해야 하는 것 셋**

1. 자본이 두 군데로 쪼개진다. 자본 C를 레버리지 L로 굴리면
   숏 명목가는 C·L/(L+1)뿐이다. 펀딩 수익률은 명목가 기준이므로
   자본 기준 수익률은 그만큼 깎인다. L=1이면 절반이다.

2. **숏 다리가 청산될 수 있다.** 델타 중립이라 총자산은 안전하지만,
   선물 계좌만 따로 보면 가격이 오를 때 증거금이 녹는다. L=3이면
   대략 33% 상승에서 청산이다. BTC는 그 정도 자주 오른다.
   현물을 팔아 증거금을 채우는 것(top-up)이 실무인데, 채우려면
   현물을 줄여야 하고 그러면 헤지가 깨진다. 여기서는 그걸 모두 센다.

3. 펀딩은 음수가 될 수 있다. 그때는 **내가 낸다.**
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CarryConfig:
    leverage: float = 1.0            # 숏 다리 레버리지. 높을수록 명목가↑ 청산위험↑
    spot_fee: float = 0.001          # 현물 테이커 (비트겟 기본 0.1%)
    perp_fee: float = 0.0006         # 선물 테이커
    maintenance_margin: float = 0.005  # 유지증거금률
    topup_trigger: float = 0.5       # 증거금이 초기의 이 비율 아래로 가면 대응
    topup_to: float = 1.0            # 초기 증거금의 이 배수까지 채운다
    allow_topup: bool = True
    # 증거금이 모자랄 때 무엇을 하는가. **이 선택이 생사를 가른다.**
    #
    #   deleverage  양 다리를 같이 줄인다 (숏 일부 환매 + 현물 일부 매도).
    #               중립이 유지되고 **원리상 청산되지 않는다.** 대신
    #               포지션이 작아져 펀딩 수익이 줄고 수수료를 낸다.
    #   sell_spot   현물만 팔아 증거금에 넣고 숏은 그대로 둔다.
    #               헤지가 줄어 순숏이 되고, 상승이 이어지면 더 팔아야 하고,
    #               그러다 현물이 바닥나면 청산된다. 실제로 돌려보면
    #               8.6년 BTC에서 1배로도 -100%가 난다. 하지 마세요.
    topup_mode: str = "deleverage"

    def validate(self) -> list[str]:
        errors = []
        if self.leverage < 1:
            errors.append("레버리지는 1 이상이어야 합니다.")
        if not 0 < self.topup_trigger < self.topup_to:
            errors.append("topup_trigger는 0보다 크고 topup_to보다 작아야 합니다.")
        if self.maintenance_margin <= 0:
            errors.append("유지증거금률은 0보다 커야 합니다.")
        if self.topup_mode not in ("deleverage", "sell_spot"):
            errors.append(f"topup_mode는 deleverage 또는 sell_spot이어야 합니다: {self.topup_mode}")
        return errors


@dataclass
class CarryResult:
    initial_equity: float
    final_equity: float
    funding_collected: float = 0.0
    funding_paid: float = 0.0
    fees: float = 0.0
    topups: int = 0
    topup_amount: float = 0.0
    liquidated: bool = False
    fully_closed: bool = False
    closed_fraction: float = 0.0   # 원래 대비 줄어든 비율 (누적합이 아니다)
    liquidated_at: int | None = None
    days: float = 0.0
    periods: int = 0
    negative_periods: int = 0
    min_margin_ratio: float = 1.0
    # 증거금 보충으로 현물을 팔면 순포지션이 숏으로 기운다. 그 정도(%).
    hedge_drift: float = 0.0
    equity_curve: list[tuple[int, float]] = field(default_factory=list)

    @property
    def net_pnl(self) -> float:
        return self.final_equity - self.initial_equity

    @property
    def return_pct(self) -> float:
        return self.net_pnl / self.initial_equity * 100 if self.initial_equity else 0.0

    @property
    def annualized_pct(self) -> float:
        if self.days <= 0 or self.initial_equity <= 0 or self.final_equity <= 0:
            return 0.0
        return ((self.final_equity / self.initial_equity) ** (365.25 / self.days) - 1) * 100

    @property
    def negative_share(self) -> float:
        return self.negative_periods / self.periods * 100 if self.periods else 0.0

    def render(self) -> str:
        lines = [
            "펀딩비 수취 (현물 매수 + 선물 숏)",
            "",
            f"  기간            {self.days:.0f}일 ({self.periods}회 정산)",
            f"  펀딩 받은 것    {self.funding_collected:+,.2f}",
            f"  펀딩 낸 것      {-self.funding_paid:+,.2f}   "
            f"(음수 구간 {self.negative_share:.1f}%)",
            f"  수수료          {-self.fees:+,.2f}",
            "  " + "─" * 46,
            f"  최종            {self.final_equity:,.2f}  "
            f"({self.return_pct:+.2f}%, 연 {self.annualized_pct:+.2f}%)",
            "",
            f"  증거금 최저     초기 대비 {self.min_margin_ratio * 100:.0f}%",
            f"  포지션 축소     {self.topups}회 "
            f"(원래의 {(1 - self.closed_fraction) * 100:.0f}%만 남음)"
            if self.closed_fraction
            else f"  증거금 보충     {self.topups}회 ({self.topup_amount:,.2f})",
            f"  헤지 어긋남     {self.hedge_drift:.1f}%  "
            f"(현물을 팔아 증거금을 대면 그만큼 순숏이 됩니다)",
        ]
        if self.fully_closed:
            lines += [
                "",
                "  ※ 포지션이 전부 정리되어 현금이 됐습니다. 그 뒤로는 아무것도",
                "     벌지 않으므로, 위 '연 수익률'은 정리된 시점까지만의 값입니다.",
            ]
        if self.liquidated:
            lines += [
                "",
                "  ✗ **숏 다리가 청산됐습니다.** 그 시점에 헤지가 사라졌고,",
                "     이후는 현물만 들고 있는 것과 같아 델타 중립이 아닙니다.",
            ]
        return "\n".join(lines)


def simulate(
    prices: list[tuple[int, float]],
    funding: list[tuple[int, float]],
    *,
    equity: float,
    cfg: CarryConfig | None = None,
) -> CarryResult:
    """가격과 펀딩률을 받아 델타 중립 캐리를 돌린다.

    prices  (ts, price) — 정산 시각의 가격을 찾는 데 쓴다
    funding (ts, rate)  — 정산 시각과 그 구간 펀딩률 (0.0001 = 0.01%)

    **펀딩은 명목가에 붙지 자본에 붙지 않는다.** 이걸 자본에 붙이면
    수익률이 레버리지 배만큼 부풀려진다.
    """
    cfg = cfg or CarryConfig()
    errors = cfg.validate()
    if errors:
        raise ValueError("; ".join(errors))
    if not prices:
        raise ValueError("가격 데이터가 없습니다.")
    if equity <= 0:
        raise ValueError("자본은 0보다 커야 합니다.")

    by_ts = sorted(prices)
    times = [t for t, _ in by_ts]

    def price_at(ts: int) -> float | None:
        # 그 시각까지 알려진 마지막 가격. 미래를 보지 않는다.
        import bisect

        i = bisect.bisect_right(times, ts) - 1
        return by_ts[i][1] if i >= 0 else None

    # 자본 배분: 현물 S = 숏 명목가 N, 증거금 M = N/L, C = N(1 + 1/L)
    notional = equity * cfg.leverage / (cfg.leverage + 1.0)
    margin0 = notional / cfg.leverage

    # 진입 시점은 '가격을 알 수 있는 첫 정산 시각'이다. 펀딩 파일이 가격보다
    # 앞서 시작하는 일은 흔하고, 그때 첫 줄을 그냥 쓰면 터진다.
    entry_ts = entry = None
    for ts, _ in sorted(funding):
        candidate = price_at(ts)
        if candidate is not None and candidate > 0:
            entry_ts, entry = ts, candidate
            break
    if entry is None:
        raise ValueError("펀딩 정산 시각에 대응하는 가격이 없습니다.")

    # 현물: 수수료를 뺀 만큼만 산다. 선물: 진입 수수료를 증거금에서 뺀다.
    # **선물 수수료를 합계에만 적고 증거금에서 안 빼면 자본이 그만큼 뻥튀기된다.**
    spot_fee_paid = notional * cfg.spot_fee
    perp_fee_paid = notional * cfg.perp_fee
    spot_qty = (notional - spot_fee_paid) / entry
    perp_qty = spot_qty                     # 진입 시점에는 델타 중립
    qty0 = spot_qty                         # 축소 정도를 재는 기준
    margin = margin0 - perp_fee_paid
    fees = spot_fee_paid + perp_fee_paid
    result = CarryResult(initial_equity=equity, final_equity=equity, fees=fees)

    last_ts = entry_ts
    for ts, rate in sorted(funding):
        if ts < entry_ts:
            continue
        price = price_at(ts)
        if price is None:
            continue
        result.periods += 1

        # 펀딩: 숏이므로 rate가 양수면 받는다. 명목가는 **선물 수량** 기준.
        cash = rate * perp_qty * price
        if cash >= 0:
            result.funding_collected += cash
        else:
            result.funding_paid += -cash
            result.negative_periods += 1
        margin += cash

        unrealized = (entry - price) * perp_qty      # 숏: 오르면 마이너스
        available = margin + unrealized
        maintenance = cfg.maintenance_margin * perp_qty * price
        result.min_margin_ratio = min(
            result.min_margin_ratio, available / margin0 if margin0 else 0.0
        )

        if available <= maintenance and not cfg.allow_topup:
            result.liquidated = True
            result.liquidated_at = ts
            # 숏과 증거금이 날아간다. 현물은 남는다 — 이제 델타 중립이 아니다.
            result.final_equity = spot_qty * price * (1 - cfg.spot_fee)
            result.days = (ts - entry_ts) / 86_400_000
            result.fees = fees
            result.hedge_drift = (perp_qty - spot_qty) / perp_qty * 100 if perp_qty else 0.0
            result.equity_curve.append((ts, result.final_equity))
            return result

        if cfg.allow_topup and available < cfg.topup_trigger * margin0:
            need = cfg.topup_to * margin0 - available
            if cfg.topup_mode == "deleverage":
                # 양 다리를 같이 줄인다. 비율 (1-k)만큼 닫으면
                # 증거금이 (1-k)·perp_qty·entry 만큼 늘어난다
                # (숏 환매 손실을 실현하고 현물 매도 대금이 들어온다).
                unit = perp_qty * entry
                shrink = min(1.0, need / unit) if unit > 0 else 1.0
                closed = perp_qty * shrink
                fee = closed * price * (cfg.spot_fee + cfg.perp_fee)
                margin += closed * entry - fee
                spot_qty -= closed
                perp_qty -= closed
                fees += fee
                result.topups += 1
                result.topup_amount += closed * entry
            else:
                sell_qty = min(spot_qty, need / (price * (1 - cfg.spot_fee)))
                if sell_qty > 0:
                    proceeds = sell_qty * price * (1 - cfg.spot_fee)
                    fees += sell_qty * price * cfg.spot_fee
                    spot_qty -= sell_qty
                    margin += proceeds
                    result.topups += 1
                    result.topup_amount += proceeds
            result.closed_fraction = 1.0 - perp_qty / qty0 if qty0 else 0.0
            if perp_qty <= 0:
                # 전부 정리됐다. 남은 것은 현금뿐이라 더 잴 것이 없다.
                result.final_equity = margin + spot_qty * price
                result.days = (ts - entry_ts) / 86_400_000
                result.fees = fees
                result.fully_closed = True
                return result
            unrealized = (entry - price) * perp_qty
            if margin + unrealized <= cfg.maintenance_margin * perp_qty * price:
                result.liquidated = True
                result.liquidated_at = ts
                result.final_equity = max(0.0, spot_qty * price)
                result.days = (ts - entry_ts) / 86_400_000
                result.fees = fees
                result.hedge_drift = (
                    (perp_qty - spot_qty) / perp_qty * 100 if perp_qty else 0.0
                )
                return result

        result.equity_curve.append(
            (ts, spot_qty * price + margin + (entry - price) * perp_qty)
        )
        last_ts = ts

    price = price_at(last_ts) or entry
    gross = spot_qty * price + margin + (entry - price) * perp_qty
    exit_fee = spot_qty * price * cfg.spot_fee + perp_qty * price * cfg.perp_fee
    result.fees = fees + exit_fee
    result.final_equity = gross - exit_fee
    result.days = (last_ts - entry_ts) / 86_400_000
    result.hedge_drift = (perp_qty - spot_qty) / perp_qty * 100 if perp_qty else 0.0
    return result


def yield_on_capital(rate: float, leverage: float, *, periods_per_day: int = 3) -> float:
    """펀딩률이 계속 rate일 때 자본 기준 연 수익률 (%).

    **명목가가 아니라 자본 기준이다.** 이 구분을 안 하면 "펀딩 연 11%"를
    그대로 계좌 수익률로 착각한다. 자본은 현물과 증거금으로 쪼개지므로
    실제로는 L/(L+1)배만 일한다.
    """
    return rate * periods_per_day * 365.25 * (leverage / (leverage + 1.0)) * 100


def breakeven_rate(
    cfg: CarryConfig, holding_days: float, *, periods_per_day: int = 3
) -> float:
    """진입·청산 수수료를 덮으려면 평균 펀딩률이 얼마여야 하는가.

    수수료는 명목가 기준이고 펀딩도 명목가 기준이라 레버리지가 약분된다 —
    보유 기간만이 변수다. 짧게 들고 나면 수수료를 못 덮는다.
    """
    round_trip = 2 * (cfg.spot_fee + cfg.perp_fee)
    periods = max(holding_days * periods_per_day, 1e-9)
    return round_trip / periods
