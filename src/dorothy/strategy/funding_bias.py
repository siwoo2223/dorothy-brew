"""펀딩률 역방향 전략 — 쏠린 쪽의 반대에 선다.

가격 지표가 아니라 **포지션 쏠림**을 본다는 점이 다르다.

- 펀딩률이 이례적으로 높다 = 롱이 몰려 비싼 비용을 내며 버티고 있다 → 숏
- 펀딩률이 이례적으로 낮다(음수) = 숏이 몰려 있다 → 롱

쏠린 쪽은 청산에 취약하다. 그 방향으로 한 번 밀리면 연쇄 청산이 나온다.

**절대값이 아니라 자기 과거 대비(z-score)로 판단한다.**
"0.05%면 높다"는 종목·시기마다 다르다. 이 종목 기준으로 지금이 이례적인가를 물어야 한다.

펀딩은 8시간마다 확정되므로 신호가 자주 나오지 않는다.
수수료가 성과를 결정하는 소액 계좌에서는 그 자체가 장점이다.

## 이 전략에는 구조적 성질이 하나 있다

펀딩이 높을 때 숏, 낮을 때 롱을 잡으므로 **항상 펀딩을 받는 쪽에 선다.**
합성 데이터 검증에서 실제로 그랬다: 펀딩 지불 0.00, 수령 16.98.
순손익의 약 35%가 펀딩 수입이었다. 이건 우연이 아니라 설계의 결과다.

**그런데 그게 공짜 점심이라는 뜻은 아니다.** 현실에서 펀딩을 받는 쪽은
'쏠림의 반대편'이고, 가격은 종종 그쪽으로 밀린다 — 그 위험을 감수한 대가로
펀딩을 받는 것이다. 합성 데이터는 펀딩과 가격의 그 관계를 전혀 모형화하지
않으므로, 여기서 나온 가격 손익은 신뢰할 수 없다.

⚠ **실제 데이터로 검증되지 않았다.** 개발 환경에서 거래소 API가 막혀
실제 펀딩률을 한 번도 받아보지 못했다. 반드시 직접 받아 확인할 것:

    python -m dorothy fetch-funding --days 365 --out data/funding.csv
    python -m dorothy walkforward --csv data/btc_1h.csv --config config/config.yaml
"""

from __future__ import annotations

import logging

from ..data.funding import FundingSeries, load_csv
from ..models import Action, Candle, Position, Side, Signal
from .base import Strategy, register
from .common import atr_at, entry_signal

log = logging.getLogger(__name__)


@register
class FundingBiasStrategy(Strategy):
    name = "funding_bias"

    def __init__(
        self,
        funding_csv: str | None = None,
        entry_z: float = 2.0,          # 이 표준편차를 넘으면 이례적으로 본다
        exit_z: float = 0.5,           # 정상으로 돌아오면 청산
        lookback: int = 90,            # z-score 계산에 쓸 과거 펀딩 횟수 (90회 ≈ 30일)
        smooth: int = 1,               # >1이면 최근 N회 평균을 쓴다 (단발 튐 제거)
        atr_period: int = 14,
        atr_stop_mult: float = 2.0,
        atr_target_mult: float = 3.0,
        allow_short: bool = True,
        min_candles: int = 100,
    ) -> None:
        super().__init__(
            funding_csv=funding_csv, entry_z=entry_z, exit_z=exit_z, lookback=lookback,
            smooth=smooth, atr_period=atr_period, atr_stop_mult=atr_stop_mult,
            atr_target_mult=atr_target_mult, allow_short=allow_short,
            min_candles=min_candles,
        )
        if entry_z <= 0:
            raise ValueError("entry_z는 0보다 커야 합니다.")
        if exit_z >= entry_z:
            raise ValueError("exit_z는 entry_z보다 작아야 합니다 (즉시 청산 방지).")

        self.entry_z = entry_z
        self.exit_z = exit_z
        self.lookback = lookback
        self.smooth = max(1, smooth)
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.allow_short = allow_short
        self.min_candles = min_candles

        self.funding: FundingSeries | None = load_csv(funding_csv) if funding_csv else None
        if self.funding is None:
            log.info(
                "펀딩률이 주입되지 않았습니다. set_funding()으로 넣거나 "
                "funding_csv 파라미터를 지정하세요 — 그 전에는 신호가 나오지 않습니다."
            )

    def set_funding(self, series: FundingSeries) -> None:
        """펀딩률 시계열을 주입한다. 설정 파일 대신 코드에서 넣을 때 쓴다."""
        self.funding = series

    @property
    def warmup(self) -> int:
        return max(self.min_candles, self.atr_period * 3)

    def _z(self, ts: int) -> float | None:
        """이 시점에 알 수 있는 펀딩률의 z-score."""
        if self.funding is None:
            return None
        if self.smooth > 1:
            history = self.funding.history_at(ts, self.lookback)
            if len(history) < 20:
                return None
            import statistics

            recent = statistics.fmean(history[-self.smooth :])
            mean = statistics.fmean(history)
            stdev = statistics.pstdev(history)
            return 0.0 if stdev <= 1e-12 else (recent - mean) / stdev
        return self.funding.zscore_at(ts, self.lookback)

    def generate(self, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return Signal(Action.HOLD, "워밍업 부족")
        if self.funding is None:
            return Signal(Action.HOLD, "펀딩률 데이터 없음")

        ts = candles[-1].ts
        z = self._z(ts)
        if z is None:
            return Signal(Action.HOLD, "펀딩률 표본 부족")

        atr = atr_at(candles, self.atr_period)
        if atr is None:
            return Signal(Action.HOLD, "ATR 미계산")

        price = candles[-1].close

        if position is not None:
            # 쏠림이 풀리면 나간다 (손절·익절은 거래소 스탑이 처리)
            if abs(z) <= self.exit_z:
                return Signal(Action.EXIT, f"펀딩 정상화 (z={z:+.2f})")
            return Signal(Action.HOLD, f"쏠림 유지 (z={z:+.2f})")

        # 펀딩이 이례적으로 높다 = 롱 쏠림 → 숏
        if z >= self.entry_z:
            if not self.allow_short:
                return Signal(Action.HOLD, "숏 비활성화")
            return entry_signal(
                long=False, price=price, atr=atr,
                stop_mult=self.atr_stop_mult, target_mult=self.atr_target_mult,
                reason=f"롱 쏠림 (펀딩 z={z:+.2f})",
                meta={"funding_z": z},
            )
        # 펀딩이 이례적으로 낮다 = 숏 쏠림 → 롱
        if z <= -self.entry_z:
            return entry_signal(
                long=True, price=price, atr=atr,
                stop_mult=self.atr_stop_mult, target_mult=self.atr_target_mult,
                reason=f"숏 쏠림 (펀딩 z={z:+.2f})",
                meta={"funding_z": z},
            )
        return Signal(Action.HOLD, f"펀딩 정상 범위 (z={z:+.2f})")
