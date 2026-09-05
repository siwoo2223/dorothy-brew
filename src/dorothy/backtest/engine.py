"""백테스트 엔진.

과거 캔들을 한 개씩 재생하며 실전과 같은 경로(전략 → 리스크 → 실행)를 태운다.
백테스트 전용 코드로 신호를 만들면 실전과 결과가 달라지므로 경로를 공유한다.

미래참조(look-ahead) 방지:
- 전략에는 candles[:i+1]만 넘긴다 (i 이후 캔들은 존재하지 않는 것처럼)
- 체결은 캔들 i의 종가 기준 (그 봉의 고가/저가로 유리하게 체결하지 않는다)
"""

from __future__ import annotations

import logging

from ..config import Config
from ..execution.executor import Executor
from ..models import Candle
from ..risk.manager import RiskManager
from ..strategy.base import Strategy
from ..exchange.paper import PaperExchange
from . import metrics as metrics_mod

log = logging.getLogger(__name__)


def run(
    candles: list[Candle],
    strategy: Strategy,
    config: Config,
    *,
    funding_series=None,
) -> metrics_mod.Metrics:
    if len(candles) <= strategy.warmup:
        raise ValueError(
            f"캔들이 부족합니다: {len(candles)}개 (전략 워밍업 {strategy.warmup}개 필요)"
        )

    exchange = PaperExchange(
        equity=config.initial_equity,
        taker_fee=config.exchange.taker_fee,
        slippage=config.exchange.slippage,
        min_size=config.exchange.min_order_size,
        size_step=config.exchange.size_step,
        funding_rate=config.exchange.funding_rate,
        funding_interval_hours=config.exchange.funding_interval_hours,
        funding_series=funding_series,
    )
    # 백테스트에서는 '지금'이 캔들 시각이다. 이래야 일일 손실 한도가 날짜별로 리셋된다.
    clock_holder = {"ts": candles[0].ts}
    risk = RiskManager(
        config.risk,
        kill_switch_file="/nonexistent-kill-switch",
        clock=lambda: clock_holder["ts"],
    )
    risk.state.day_start_equity = config.initial_equity
    executor = Executor(
        exchange,
        risk,
        symbol=config.exchange.symbol,
        leverage=config.exchange.leverage,
        min_size=config.exchange.min_order_size,
        size_step=config.exchange.size_step,
    )

    settled = 0
    for i, candle in enumerate(candles):
        clock_holder["ts"] = candle.ts
        exchange.feed_candle(candle)   # 스탑 체결 판정이 여기서 일어난다

        # 스탑으로 청산된 건을 리스크 매니저에 반영
        while settled < len(exchange.trades):
            risk.record_trade(exchange.trades[settled])
            settled += 1
        risk.roll_day(exchange.total_equity())

        if i < strategy.warmup:
            continue

        position = exchange.fetch_position(config.exchange.symbol)
        signal = strategy.generate(candles[: i + 1], position)
        executor.handle(
            signal,
            position=position,
            equity=exchange.total_equity(),
            candle_ts=candle.ts,
        )

        while settled < len(exchange.trades):
            risk.record_trade(exchange.trades[settled])
            settled += 1

    # 마지막에 남은 포지션은 종가로 정리해야 지표가 왜곡되지 않는다
    if exchange.fetch_position(config.exchange.symbol) is not None:
        exchange.close_position(config.exchange.symbol, reason="백테스트 종료")
        exchange.equity_curve.append((candles[-1].ts, exchange.total_equity()))

    return metrics_mod.compute(exchange.trades, exchange.equity_curve, config.initial_equity)
