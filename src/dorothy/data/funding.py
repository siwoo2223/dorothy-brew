"""펀딩률 시계열 — 가격이 아닌 데이터.

지금까지의 분석은 전부 캔들에서 나왔다. 펀딩률은 다르다.
**포지션이 어느 쪽으로 쏠렸는지를 직접 보여주는 숫자**다.

- 펀딩률이 크게 양수 = 롱이 숏에게 돈을 낸다 = 롱이 쏠려 있다
- 펀딩률이 크게 음수 = 숏이 롱에게 돈을 낸다 = 숏이 쏠려 있다

쏠린 쪽은 청산당하기 쉽다. 그래서 극단적 펀딩률은 **역방향 신호**로 쓴다.
가격 지표와 상관이 낮다는 점이 특히 쓸모 있다 — 같은 정보를 다르게 보는 게
아니라 아예 다른 정보다.

**인과성이 이 모듈의 핵심이다.** 펀딩은 8시간마다 확정되므로, 어떤 시점에서
알 수 있는 값은 '그 시점 이전에 확정된 마지막 값'뿐이다. 다음 펀딩률을
미리 알 수 없다. `rate_at()`이 그 규칙을 강제한다.
"""

from __future__ import annotations

import bisect
import csv
import statistics
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FundingPoint:
    ts: int        # 확정 시각 (epoch ms)
    rate: float    # 해당 구간 펀딩률 (예: 0.0001 = 0.01%)


class FundingSeries:
    """시간순 펀딩률. 조회는 항상 '그 시점까지 확정된 값'만 돌려준다."""

    def __init__(self, points: list[FundingPoint]) -> None:
        self.points = sorted(points, key=lambda p: p.ts)
        self._times = [p.ts for p in self.points]

    def __len__(self) -> int:
        return len(self.points)

    def __bool__(self) -> bool:
        return bool(self.points)

    def rate_at(self, ts: int) -> float | None:
        """ts 시점에 **알 수 있는** 펀딩률.

        ts 이전(또는 같은 시각)에 확정된 마지막 값을 돌려준다.
        미래 값을 절대 반환하지 않는다 — 이걸 어기면 백테스트가 통째로 거짓이 된다.
        """
        if not self.points:
            return None
        index = bisect.bisect_right(self._times, ts) - 1
        return self.points[index].rate if index >= 0 else None

    def history_at(self, ts: int, count: int) -> list[float]:
        """ts 시점까지 확정된 최근 count개 펀딩률."""
        if not self.points:
            return []
        end = bisect.bisect_right(self._times, ts)
        start = max(0, end - count)
        return [p.rate for p in self.points[start:end]]

    def zscore_at(self, ts: int, lookback: int = 90) -> float | None:
        """현재 펀딩률이 최근 분포에서 몇 표준편차인가.

        절대값(0.01% 등)은 종목·시기마다 기준이 달라 그대로 쓰기 어렵다.
        자기 과거 대비로 보면 '이 종목 기준으로 지금이 이례적인가'를 물을 수 있다.
        """
        history = self.history_at(ts, lookback)
        if len(history) < 20:
            return None
        current = history[-1]
        mean = statistics.fmean(history)
        stdev = statistics.pstdev(history)
        if stdev <= 1e-12:
            return 0.0
        return (current - mean) / stdev

    def percentile_at(self, ts: int, lookback: int = 90) -> float | None:
        history = self.history_at(ts, lookback)
        if len(history) < 20:
            return None
        current = history[-1]
        return sum(1 for v in history if v < current) / len(history) * 100

    def average_at(self, ts: int, count: int = 3) -> float | None:
        """최근 몇 회 평균. 단발 튐을 걸러낸다."""
        history = self.history_at(ts, count)
        return statistics.fmean(history) if history else None


# --------------------------------------------------------------------------
# 적재
# --------------------------------------------------------------------------
def load_csv(path: str | Path) -> FundingSeries:
    """CSV에서 읽는다. 헤더: ts,rate (또는 timestamp,fundingRate)."""
    aliases_ts = {"ts", "timestamp", "time", "fundingtime", "시각"}
    aliases_rate = {"rate", "fundingrate", "funding_rate", "펀딩률"}

    points: list[FundingPoint] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ts = rate = None
            for key, value in row.items():
                name = str(key).strip().lower()
                if name in aliases_ts:
                    ts = int(float(value))
                elif name in aliases_rate:
                    rate = float(value)
            if ts is not None and rate is not None:
                points.append(FundingPoint(ts, rate))
    if not points:
        raise ValueError(f"펀딩률을 읽지 못했습니다: {path} (헤더에 ts,rate 필요)")
    return FundingSeries(points)


def save_csv(series: FundingSeries, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "rate"])
        for point in series.points:
            writer.writerow([point.ts, point.rate])


def fetch_history(
    symbol: str, *, days: int = 180, exchange_id: str = "bitget"
) -> FundingSeries:
    """거래소에서 과거 펀딩률을 수집한다. API 키가 필요 없다."""
    try:
        import ccxt  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ImportError("펀딩률 수집에는 ccxt가 필요합니다: pip install ccxt") from exc

    import time as _time

    client = getattr(ccxt, exchange_id)(
        {"enableRateLimit": True, "options": {"defaultType": "swap"}}
    )
    since = int(_time.time() * 1000) - days * 86_400_000
    points: list[FundingPoint] = []
    while True:
        batch = client.fetch_funding_rate_history(symbol, since, 200)
        if not batch:
            break
        for row in batch:
            ts = row.get("timestamp")
            rate = row.get("fundingRate")
            if ts is not None and rate is not None:
                points.append(FundingPoint(int(ts), float(rate)))
        next_since = batch[-1]["timestamp"] + 1
        if next_since <= since:
            break
        since = next_since
        if since > _time.time() * 1000:
            break

    dedup = {p.ts: p for p in points}
    return FundingSeries([dedup[k] for k in sorted(dedup)])


def synthetic(
    start_ts: int, count: int, *, interval_hours: int = 8, seed: int = 7,
    base: float = 0.0001, volatility: float = 0.00015, spike_chance: float = 0.06,
) -> FundingSeries:
    """검증용 합성 펀딩률.

    평상시에는 0.01% 근처를 오가다 가끔 극단으로 튀는 실제 패턴을 흉내낸다.
    수익률에는 아무 의미가 없고 파이프라인 점검용이다.
    """
    import random

    rng = random.Random(seed)
    step = interval_hours * 3_600_000
    points: list[FundingPoint] = []
    level = base
    for i in range(count):
        level = level * 0.85 + base * 0.15 + rng.gauss(0, volatility)
        if rng.random() < spike_chance:
            level += rng.choice([-1, 1]) * rng.uniform(0.0005, 0.002)
        points.append(FundingPoint(start_ts + i * step, level))
    return FundingSeries(points)
