"""특징(feature) 생성.

**모든 특징은 i 시점까지의 캔들만으로 계산된다.** 하나라도 미래를 보면
모델이 그걸 학습해 백테스트에서만 완벽해진다. 금융 ML에서 가장 흔한 사고다.

특징 설계 원칙:
- **정규화한다.** 가격 자체(65,000)를 넣으면 모델이 '가격이 높으면 오른다' 같은
  시기 의존적 규칙을 외운다. 수익률·ATR 배수·백분위처럼 시기와 무관한 값을 쓴다.
- **적게 넣는다.** 특징이 많을수록 과최적화가 쉽다. 금융 데이터는 신호 대 잡음이
  극도로 낮아 특징 50개를 주면 모델은 잡음 50개를 외운다.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from ..analysis.regime import variance_ratio
from ..analysis.sessions import utc_hour
from ..data.indicators import atr, ema, rsi
from ..models import Candle

FEATURE_NAMES = [
    "ret_1", "ret_6", "ret_24",          # 최근 수익률 (1/6/24봉)
    "vol_ratio",                          # 현재 변동성 / 장기 변동성
    "atr_pct",                            # ATR / 가격 (변동성 수준)
    "rsi",                                # 과매수/과매도
    "ema_dist",                           # 가격이 EMA에서 몇 ATR 떨어졌나
    "variance_ratio",                     # 추세성 (>1 추세, <1 회귀)
    "volume_z",                           # 거래량 이례도
    "hour_sin", "hour_cos",               # 시각 (주기성 보존)
]


@dataclass(frozen=True)
class FeatureRow:
    index: int
    ts: int
    values: list[float]


def _safe(value: float) -> float:
    return value if math.isfinite(value) else 0.0


def compute_at(candles: list[Candle], index: int, *, atr_period: int = 14) -> list[float] | None:
    """index 시점의 특징 벡터. 미래 캔들을 절대 쓰지 않는다."""
    if index < 220 or index >= len(candles):
        return None

    window = candles[: index + 1]
    closes = [c.close for c in window]
    highs = [c.high for c in window]
    lows = [c.low for c in window]
    volumes = [c.volume for c in window]

    price = closes[-1]
    if price <= 0:
        return None

    atr_line = atr(highs[-200:], lows[-200:], closes[-200:], atr_period)
    atr_now = atr_line[-1]
    if not atr_now or atr_now <= 0:
        return None

    def ret(n: int) -> float:
        past = closes[-1 - n]
        return _safe(math.log(price / past)) if past > 0 else 0.0

    recent_returns = [
        math.log(b / a) for a, b in zip(closes[-49:-1], closes[-48:]) if a > 0 and b > 0
    ]
    long_returns = [
        math.log(b / a) for a, b in zip(closes[-201:-1], closes[-200:]) if a > 0 and b > 0
    ]
    short_vol = statistics.pstdev(recent_returns) if len(recent_returns) > 5 else 0.0
    long_vol = statistics.pstdev(long_returns) if len(long_returns) > 5 else 0.0

    rsi_now = rsi(closes[-60:], 14)[-1]
    ema_now = ema(closes[-160:], 50)[-1]

    vol_mean = statistics.fmean(volumes[-100:])
    vol_std = statistics.pstdev(volumes[-100:])

    hour = utc_hour(candles[index].ts)

    return [
        _safe(ret(1)),
        _safe(ret(6)),
        _safe(ret(24)),
        _safe(short_vol / long_vol) if long_vol > 0 else 1.0,
        _safe(atr_now / price),
        _safe((rsi_now if rsi_now is not None else 50.0) / 100.0),
        _safe((price - ema_now) / atr_now) if ema_now else 0.0,
        _safe(variance_ratio(closes[-200:], 4)),
        _safe((volumes[-1] - vol_mean) / vol_std) if vol_std > 0 else 0.0,
        math.sin(2 * math.pi * hour / 24),
        math.cos(2 * math.pi * hour / 24),
    ]


def warmup() -> int:
    """특징 계산에 필요한 최소 캔들 수."""
    return 220
