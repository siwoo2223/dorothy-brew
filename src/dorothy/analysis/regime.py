"""시장 국면 판별 — "어떤 지표를 쓸까" 대신 "지금 어떤 시장인가".

지금까지의 전략은 전부 **항상 매매**한다. 추세추종은 횡보장에서 계속 털리고
평균회귀는 추세장에서 크게 잃는데, 둘 다 그 사실을 모른 채 신호를 낸다.

국면 판별은 다른 질문을 던진다: **지금 이 시장이 내 전략에 맞는가?**
맞지 않으면 매매하지 않는 것이 최선이다.

두 축으로 본다.

**1. 분산비(Variance Ratio)** — 추세성 대 회귀성
    VR = Var(k봉 수익률) / (k × Var(1봉 수익률))

    VR > 1  같은 방향이 이어진다 → 추세장 (추세추종에 유리)
    VR ≈ 1  랜덤워크 → 착취할 편향이 없다
    VR < 1  되돌린다 → 횡보/회귀장 (평균회귀에 유리)

    허스트 지수보다 짧은 구간에서 수치적으로 안정적이라 이쪽을 쓴다.

**2. 변동성 국면** — 현재 ATR이 자기 과거 대비 어디쯤인가
    변동성이 낮으면 손절이 좁아 잘 털리고, 높으면 같은 리스크에 수량이 줄어든다.

전부 과거 캔들만으로 계산한다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from enum import Enum

from ..data.indicators import atr as atr_indicator
from ..models import Candle


class Trendiness(str, Enum):
    TRENDING = "trending"      # VR이 뚜렷이 1보다 큼
    RANDOM = "random"          # 1 근처 — 편향 없음
    REVERTING = "reverting"    # VR이 뚜렷이 1보다 작음


class Volatility(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


@dataclass(frozen=True)
class Regime:
    trendiness: Trendiness
    volatility: Volatility
    variance_ratio: float
    atr_percentile: float

    @property
    def label(self) -> str:
        trend = {"trending": "추세", "random": "무작위", "reverting": "회귀"}[self.trendiness.value]
        vol = {"low": "저변동", "normal": "보통", "high": "고변동"}[self.volatility.value]
        return f"{trend}/{vol}"

    @property
    def suits_trend_following(self) -> bool:
        return self.trendiness is Trendiness.TRENDING

    @property
    def suits_mean_reversion(self) -> bool:
        return self.trendiness is Trendiness.REVERTING


def log_returns(closes: list[float]) -> list[float]:
    import math

    out: list[float] = []
    for a, b in zip(closes, closes[1:]):
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def variance_ratio(closes: list[float], k: int = 4) -> float:
    """분산비. 1보다 크면 추세성, 작으면 회귀성.

    수익률이 독립이면 k봉 수익률의 분산은 1봉 분산의 k배가 된다.
    그보다 크다는 건 같은 방향이 이어졌다는 뜻이고, 작다는 건 되돌렸다는 뜻이다.
    """
    if k < 2:
        raise ValueError("k는 2 이상이어야 합니다.")
    returns = log_returns(closes)
    if len(returns) < k * 4:
        return 1.0

    var_1 = statistics.pvariance(returns)
    # 수익률이 사실상 일정하면(합성 데이터, 거래정지 구간 등) 분산이 0에 붙는다.
    # 그 상태로 나누면 부동소수 노이즈가 그대로 증폭돼 엉뚱한 판정이 나온다.
    # 실제로 단조 상승 시계열이 '회귀장'으로 분류되는 것을 검산에서 잡았다.
    if var_1 < 1e-12:
        return 1.0

    # k봉 누적 수익률 (겹치는 창을 써서 표본을 확보한다)
    aggregated = [sum(returns[i : i + k]) for i in range(len(returns) - k + 1)]
    var_k = statistics.pvariance(aggregated)
    return var_k / (k * var_1)


def autocorrelation(closes: list[float], lag: int = 1) -> float:
    """수익률의 자기상관. 양수면 추세, 음수면 되돌림."""
    returns = log_returns(closes)
    if len(returns) < lag + 10:
        return 0.0
    a, b = returns[:-lag], returns[lag:]
    mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    den = (
        sum((x - mean_a) ** 2 for x in a) ** 0.5
        * sum((y - mean_b) ** 2 for y in b) ** 0.5
    )
    return num / den if den else 0.0


def atr_percentile(candles: list[Candle], period: int = 14, lookback: int = 200) -> float:
    """현재 ATR이 최근 lookback 구간에서 몇 퍼센타일인가 (0~100)."""
    line = atr_indicator(
        [c.high for c in candles], [c.low for c in candles],
        [c.close for c in candles], period,
    )
    values = [v for v in line[-lookback:] if v is not None]
    if len(values) < 20:
        return 50.0
    current = values[-1]
    below = sum(1 for v in values if v < current)
    return below / len(values) * 100


def classify(
    candles: list[Candle],
    *,
    window: int = 200,
    vr_k: int = 4,
    trend_threshold: float = 1.15,
    revert_threshold: float = 0.85,
    atr_period: int = 14,
    low_vol_pct: float = 30.0,
    high_vol_pct: float = 70.0,
) -> Regime:
    """최근 window 봉으로 국면을 판정한다.

    임계값(1.15 / 0.85)은 '뚜렷할 때만 판정한다'는 뜻이다.
    1 근처는 무작위로 두는 편이 낫다 — 애매한 신호로 전략을 바꾸면
    국면 판별이 또 하나의 노이즈 원천이 된다.
    """
    recent = candles[-window:] if len(candles) > window else candles
    closes = [c.close for c in recent]

    vr = variance_ratio(closes, vr_k)
    if vr >= trend_threshold:
        trendiness = Trendiness.TRENDING
    elif vr <= revert_threshold:
        trendiness = Trendiness.REVERTING
    else:
        trendiness = Trendiness.RANDOM

    pct = atr_percentile(recent, atr_period, lookback=window)
    if pct <= low_vol_pct:
        volatility = Volatility.LOW
    elif pct >= high_vol_pct:
        volatility = Volatility.HIGH
    else:
        volatility = Volatility.NORMAL

    return Regime(trendiness, volatility, vr, pct)
