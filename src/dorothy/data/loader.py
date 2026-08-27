"""캔들 데이터 적재.

- CSV: 이미 받아둔 데이터 (ts,open,high,low,close,volume)
- 거래소: ccxt로 과거 구간을 페이지네이션하며 수집
- 합성: 네트워크 없이 파이프라인을 검증하기 위한 랜덤워크
"""

from __future__ import annotations

import csv
import logging
import math
import random
import time
from pathlib import Path

from ..models import Candle
from .timeframes import timeframe_ms  # noqa: F401  (재수출)

log = logging.getLogger(__name__)



def load_csv(path: str | Path) -> list[Candle]:
    rows: list[Candle] = []
    with Path(path).open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"ts", "open", "high", "low", "close", "volume"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"CSV 헤더에 {sorted(required)} 가 필요합니다. 현재: {reader.fieldnames}")
        for r in reader:
            rows.append(
                Candle(
                    int(float(r["ts"])), float(r["open"]), float(r["high"]),
                    float(r["low"]), float(r["close"]), float(r["volume"]),
                )
            )
    rows.sort(key=lambda c: c.ts)
    return rows


def save_csv(candles: list[Candle], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.ts, c.open, c.high, c.low, c.close, c.volume])
    log.info("%d개 캔들 저장: %s", len(candles), p)


def fetch_history(
    symbol: str, timeframe: str, *, days: int = 90, exchange_id: str = "bitget"
) -> list[Candle]:
    """공개 API로 과거 캔들을 수집한다. API 키가 없어도 된다."""
    try:
        import ccxt  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ImportError("데이터 수집에는 ccxt가 필요합니다: pip install ccxt") from exc

    client = getattr(ccxt, exchange_id)({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    step = timeframe_ms(timeframe)
    since = int(time.time() * 1000) - days * 86_400_000
    out: list[Candle] = []
    while True:
        batch = client.fetch_ohlcv(symbol, timeframe, since, 1000)
        if not batch:
            break
        out.extend(Candle.from_ccxt(r) for r in batch)
        next_since = batch[-1][0] + step
        if next_since <= since or len(batch) < 2:
            break
        since = next_since
        log.info("수집 중... %d개 (마지막 %s)", len(out), batch[-1][0])
        if since > time.time() * 1000:
            break

    # 중복 제거 + 정렬 (거래소가 경계에서 겹쳐서 주는 경우가 있다)
    dedup = {c.ts: c for c in out}
    return [dedup[k] for k in sorted(dedup)]


def synthetic(
    n: int = 2000, *, start: float = 60_000.0, timeframe: str = "5m", seed: int = 42,
    trend: float = 0.00002, vol: float = 0.004,
) -> list[Candle]:
    """오프라인 검증용 랜덤워크 캔들.

    이걸로 나온 수익률에는 아무 의미가 없다. 파이프라인이 도는지만 본다.
    """
    rng = random.Random(seed)
    step = timeframe_ms(timeframe)
    ts = int(time.time() * 1000) - n * step
    price = start
    out: list[Candle] = []
    for i in range(n):
        # 완만한 추세 + 사인파 사이클 + 노이즈
        drift = trend + 0.0006 * math.sin(i / 120)
        ret = rng.gauss(drift, vol)
        open_ = price
        close = max(price * (1 + ret), 1e-6)
        high = max(open_, close) * (1 + abs(rng.gauss(0, vol / 3)))
        low = min(open_, close) * (1 - abs(rng.gauss(0, vol / 3)))
        out.append(Candle(ts + i * step, open_, high, low, close, abs(rng.gauss(100, 30))))
        price = close
    return out
