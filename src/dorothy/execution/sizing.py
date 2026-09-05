"""주문 수량 정규화.

거래소는 아무 수량이나 받지 않는다. 최소 수량이 있고, 그 위로는
정해진 단위(step)의 배수여야 한다. 이걸 백테스트에 반영하지 않으면
**소액 계좌에서 결과가 통째로 거짓말이 된다** — 실제로는 나가지도 못할
주문을 체결시키기 때문이다.

반올림은 **항상 내림**이다. 올림하면 계산한 리스크보다 더 큰 포지션을 잡게 되고,
그건 리스크 한도를 조용히 넘는 것과 같다.
"""

from __future__ import annotations

import math


def round_down_to_step(size: float, step: float) -> float:
    """수량을 단위의 배수로 내림한다.

    부동소수 오차로 0.0003이 0.00029999...가 되어 한 단위 깎이는 일을 막기 위해
    아주 작은 여유를 두고 내림한다.
    """
    if step <= 0:
        return max(size, 0.0)
    if size <= 0:
        return 0.0
    units = math.floor(size / step + 1e-9)
    return round(units * step, 12)


def normalize(size: float, *, min_size: float = 0.0, step: float = 0.0) -> float:
    """단위로 내림한 뒤 최소 수량을 만족하는지 본다.

    만족하지 못하면 0.0을 돌려준다 — 호출자는 이걸 '주문 불가'로 다뤄야 한다.
    최소 수량까지 억지로 올리면 감수하기로 한 리스크를 초과하게 된다.
    """
    normalized = round_down_to_step(size, step) if step > 0 else max(size, 0.0)
    if min_size > 0 and normalized < min_size:
        return 0.0
    return normalized
