"""지정가(메이커) 진입 시뮬레이션.

시장가 대신 지정가로 넣으면 수수료가 내려간다(테이커 0.06% → 메이커 0.02%).
하지만 **공짜가 아니다.** 지정가는 가격이 되돌아와야 체결되는데, 돌파 전략에서
되돌아오지 않는 캔들은 대개 **그대로 크게 간 캔들**이다.

즉 지정가는 비용을 깎는 대신 **좋은 매매를 골라서 놓칠 수 있다.**
비용 절감분이 그 손실보다 큰지는 재봐야 아는 문제고, 이 모듈이 그걸 잰다.

체결 규칙 (보수적으로 잡았다):
- 롱 지정가 L: 이후 봉의 저가가 L 이하로 내려오면 L에 체결.
  갭으로 L보다 훨씬 아래에서 열려도 L로 계산한다(유리한 쪽을 취하지 않는다).
- 숏 지정가 L: 고가가 L 이상이면 L에 체결.
- 대기 봉 수를 넘기면 주문을 취소한다. 시장가로 쫓아가지 않는다.
  쫓아가면 메이커 수수료를 받으려던 이유가 사라진다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Candle, Side


@dataclass(frozen=True)
class FillOutcome:
    """지정가 주문의 결말."""

    filled: bool
    index: int | None       # 체결된 봉
    price: float | None     # 체결가 (지정가와 같다)
    waited: int             # 몇 봉 기다렸나
    reason: str             # "filled" | "timeout" | "no_bars"


def limit_price(
    candle: Candle, side: Side, atr_value: float, offset_atr: float
) -> float:
    """신호봉 종가에서 유리한 쪽으로 offset_atr만큼 물러난 지정가.

    offset_atr=0이면 종가에 그대로 건다. 이것도 체결이 보장되지 않는다 —
    다음 봉이 한 번도 이 가격에 닿지 않을 수 있다.
    """
    step = atr_value * offset_atr
    return candle.close - step if side is Side.LONG else candle.close + step


def simulate_limit_fill(
    candles: list[Candle],
    signal_index: int,
    side: Side,
    price: float,
    *,
    timeout_bars: int = 3,
) -> FillOutcome:
    """신호봉 다음 봉부터 timeout_bars 안에 지정가가 체결되는지 본다."""
    if signal_index >= len(candles) - 1:
        return FillOutcome(False, None, None, 0, "no_bars")

    last = min(signal_index + timeout_bars, len(candles) - 1)
    for i in range(signal_index + 1, last + 1):
        candle = candles[i]
        touched = candle.low <= price if side is Side.LONG else candle.high >= price
        if touched:
            return FillOutcome(True, i, price, i - signal_index, "filled")

    return FillOutcome(False, None, None, last - signal_index, "timeout")


def round_trip_cost(
    *, maker_fee: float, taker_fee: float, slippage: float, maker_entry: bool
) -> float:
    """왕복 비용.

    청산은 손절·익절 모두 **시장가**로 잡는다. 손절을 지정가로 걸 수는 없고,
    익절만 메이커로 넣으면 안 닿을 때 그대로 되돌아오기 때문이다.
    진입만 메이커로 바꾸는 게 현실적으로 확실한 절감분이다.

    메이커 진입은 슬리피지가 없다. 자기가 낸 가격에 체결되거나 안 되거나다.
    """
    entry = maker_fee if maker_entry else taker_fee + slippage
    exit_ = taker_fee + slippage
    return entry + exit_
