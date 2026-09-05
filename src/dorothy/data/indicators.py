"""지표 계산. 외부 의존성 없이 순수 파이썬으로 구현.

주의: 모든 함수는 입력과 같은 길이의 리스트를 반환하며, 계산이 불가능한
앞부분은 None으로 채운다. 이 None을 무시하고 인덱싱하면 '미래를 본' 것과
같은 버그가 생기므로 전략에서 반드시 None 체크를 해야 한다.
"""

from __future__ import annotations


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        raise ValueError("period는 1 이상이어야 합니다.")
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        raise ValueError("period는 1 이상이어야 합니다.")
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period   # 첫 값은 SMA로 시드
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float | None]:
    """평균 진폭. 손절 폭을 종목 변동성에 맞춰 잡는 데 쓴다.

    고정 퍼센트 손절은 변동성이 커지면 너무 자주 털리고, 작아지면 너무 멀다.
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    trs: list[float] = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    prev = sum(trs[1 : period + 1]) / period
    out[period] = prev
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + trs[i]) / period   # Wilder 평활
        out[i] = prev
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    n = len(values)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        gains += max(diff, 0.0)
        losses += max(-diff, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, n):
        diff = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(diff, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def supertrend(
    highs: list[float], lows: list[float], closes: list[float],
    period: int = 10, multiplier: float = 3.0,
) -> tuple[list[int | None], list[float | None]]:
    """수퍼트렌드. (추세방향, 추세선)을 돌려준다. 방향은 +1 상승 / -1 하락.

    ATR 기반 추적 손절선이다. 밴드가 한 방향으로만 조여지기 때문에
    잔파동에서 방향이 잘 바뀌지 않는다 — **거래 빈도가 낮다**는 뜻이고,
    수수료가 성과를 먹는 구간에서는 그 자체가 장점이다.

    미래를 보지 않는다. i 시점 계산에 i까지의 값만 쓴다.
    """
    n = len(closes)
    trend: list[int | None] = [None] * n
    line: list[float | None] = [None] * n
    atr_line = atr(highs, lows, closes, period)

    upper = lower = None
    for i in range(n):
        a = atr_line[i]
        if a is None:
            continue
        mid = (highs[i] + lows[i]) / 2
        basic_upper = mid + multiplier * a
        basic_lower = mid - multiplier * a

        # 밴드는 추세 방향으로만 조여진다(느슨해지지 않는다).
        # 이게 수퍼트렌드가 잔파동에 덜 흔들리는 이유다.
        if upper is None or lower is None:
            upper, lower = basic_upper, basic_lower
            trend[i] = 1 if closes[i] >= mid else -1
            line[i] = lower if trend[i] == 1 else upper
            continue

        upper = basic_upper if (basic_upper < upper or closes[i - 1] > upper) else upper
        lower = basic_lower if (basic_lower > lower or closes[i - 1] < lower) else lower

        prev = trend[i - 1] if trend[i - 1] is not None else 1
        if prev == 1 and closes[i] < lower:
            current = -1
        elif prev == -1 and closes[i] > upper:
            current = 1
        else:
            current = prev
        trend[i] = current
        line[i] = lower if current == 1 else upper
    return trend, line


def tma(values: list[float], period: int) -> list[float | None]:
    """삼각이동평균 (SMA를 두 번 건 것). 인과적 버전.

    부드럽지만 그만큼 느리다. 아래 tma_centered와 반드시 구분할 것.
    """
    first = sma(values, period)
    usable = [v for v in first if v is not None]
    if not usable:
        return [None] * len(values)
    second = sma(usable, period)
    out: list[float | None] = [None] * len(values)
    offset = len(values) - len(second)
    for i, v in enumerate(second):
        out[i + offset] = v
    return out


def tma_centered(values: list[float], period: int) -> list[float | None]:
    """**미래를 보는** 중심이동 TMA. MT4/MT5 'TMA 밴드'가 쓰는 방식이다.

    ⚠ 이 함수는 i 시점 값을 계산할 때 i 이후의 캔들을 쓴다.
    차트에서는 가격을 기가 막히게 따라가는 것처럼 보이고 백테스트도 훌륭하게 나오지만,
    실시간에는 그 값을 알 수 없다. 새 캔들이 오면 과거 선이 다시 그려진다(리페인팅).

    **전략에 쓰라고 만든 게 아니라, 왜 쓰면 안 되는지 보여주려고 만들었다.**
    `dorothy repaint` 계열 도구와 인과성 테스트로 그 차이를 실측할 수 있다.
    """
    n = len(values)
    half = period // 2
    out: list[float | None] = [None] * n
    for i in range(n):
        start, end = i - half, i + half + 1      # ← end가 i를 넘어간다 = 미래 참조
        if start < 0 or end > n:
            continue
        window = values[start:end]
        out[i] = sum(window) / len(window)
    return out
