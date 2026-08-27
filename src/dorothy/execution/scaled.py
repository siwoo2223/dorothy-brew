"""분할 주문(scaled order) — 한 가격이 아니라 구간에 나눠 넣는다.

단일 지정가는 **전부 잡히거나 전부 놓치거나**다. 이 저장소에서 재봤더니
놓친 신호가 잡은 신호보다 5배 좋았다 — 안 돌아온 캔들이 하필 크게 간
캔들이었기 때문이다. 그 이분법이 문제다.

분할 주문은 **부분 체결**을 만든다. 가격이 조금만 되돌아오면 위쪽 몇 개가
채워지고, 깊이 되돌아오면 전부 채워진다. 그대로 가버려도 위쪽 한두 개는
잡혀 있다. 즉 **되돌림 깊이에 비례해 수량이 정해진다.**

그리드와 다르다. 그리드는 방향 견해 없이 양방향으로 왕복을 먹는다.
분할 주문은 방향 견해를 그대로 두고 **진입가만 면으로 잡는 것**이다.

⚠ 손절은 여전히 시장가다. 지정가 손절은 가격이 그냥 지나가면 체결되지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Candle, Side


@dataclass(frozen=True)
class ScaleSpec:
    """분할 방식."""

    orders: int = 5              # 몇 개로 나눌지
    depth_atr: float = 0.50      # 가장 먼 주문을 몇 ATR 떨어뜨릴지
    timeout_bars: int = 6        # 이 봉 수를 넘기면 남은 주문 취소
    include_touch: bool = True   # 첫 주문을 신호가에 바로 걸지

    def __post_init__(self) -> None:
        if self.orders < 1:
            raise ValueError("orders는 1 이상이어야 합니다.")
        if self.depth_atr < 0:
            raise ValueError("depth_atr는 0 이상이어야 합니다.")

    def prices(self, reference: float, side: Side, atr_value: float) -> list[float]:
        """주문 가격들. 롱이면 아래로, 숏이면 위로 깔린다.

        include_touch면 첫 주문이 신호가에 붙고 나머지가 아래로 퍼진다.
        아니면 전부 신호가보다 유리한 쪽에 놓인다 — 체결은 덜 되지만 값은 좋다.
        """
        if self.orders == 1:
            offset = 0.0 if self.include_touch else self.depth_atr
            return [reference - offset * atr_value * side.sign]

        span = self.depth_atr * atr_value
        start = 0.0 if self.include_touch else span / self.orders
        step = (span - start) / (self.orders - 1) if self.orders > 1 else 0.0
        return [reference - (start + step * k) * side.sign for k in range(self.orders)]


@dataclass
class ScaledFill:
    """분할 주문의 결과."""

    filled: int = 0
    total: int = 0
    prices: list[float] = field(default_factory=list)
    last_index: int | None = None      # 마지막으로 체결된 봉

    @property
    def ratio(self) -> float:
        """의도한 수량 중 실제로 채워진 비율."""
        return self.filled / self.total if self.total else 0.0

    @property
    def avg_price(self) -> float | None:
        return sum(self.prices) / len(self.prices) if self.prices else None

    @property
    def any_filled(self) -> bool:
        return self.filled > 0


def simulate_scaled_entry(
    candles: list[Candle],
    signal_index: int,
    side: Side,
    reference: float,
    atr_value: float,
    spec: ScaleSpec,
) -> ScaledFill:
    """신호봉 다음 봉부터 분할 주문이 몇 개나 채워지는지 본다.

    각 주문은 독립이다. 가격이 스쳐간 만큼만 채워진다.
    **신호봉에서는 절대 체결되지 않는다** — 그 봉은 이미 끝났다.
    """
    result = ScaledFill(total=spec.orders)
    if signal_index >= len(candles) - 1 or atr_value <= 0:
        return result

    wanted = spec.prices(reference, side, atr_value)
    pending = list(range(len(wanted)))
    last = min(signal_index + spec.timeout_bars, len(candles) - 1)

    for i in range(signal_index + 1, last + 1):
        candle = candles[i]
        still: list[int] = []
        for k in pending:
            price = wanted[k]
            touched = candle.low <= price if side is Side.LONG else candle.high >= price
            if touched:
                result.filled += 1
                result.prices.append(price)
                result.last_index = i
            else:
                still.append(k)
        pending = still
        if not pending:
            break

    return result
