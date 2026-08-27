"""시간대·세션 분류.

암호화폐는 24시간 거래되지만 **거래량은 전통 시장 시간을 따라간다.**
아시아가 깨어날 때, 런던이 열릴 때, 뉴욕이 겹칠 때 유동성과 변동성이 다르다.
ICT에서 말하는 킬존도 이 구조를 전제로 한다.

⚠ **시간대 분석에는 고유한 함정이 있다.**
24개 시간대를 다 재보면 그중 최고는 반드시 좋아 보인다. 24번 뽑기에서
최고를 고른 것이지 우위를 찾은 것이 아니다. `session_report`가 순열검정으로
"이 차이가 우연히 나올 확률"을 함께 내놓는 이유다.

모든 시각은 UTC 기준이다. 거래소 캔들 타임스탬프가 UTC이기 때문이다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum


class Session(str, Enum):
    ASIA = "asia"              # 00:00–08:00 UTC (도쿄 09~17시)
    LONDON = "london"          # 08:00–12:00 UTC (뉴욕 개장 전)
    OVERLAP = "overlap"        # 12:00–16:00 UTC (런던·뉴욕 겹침 — 거래량 최대)
    NEW_YORK = "new_york"      # 16:00–21:00 UTC
    LATE = "late"              # 21:00–24:00 UTC (한산)

    @property
    def korean(self) -> str:
        return {
            "asia": "아시아", "london": "런던", "overlap": "런던·뉴욕 겹침",
            "new_york": "뉴욕", "late": "심야",
        }[self.value]


class Killzone(str, Enum):
    """ICT 킬존. 위 세션보다 좁은 구간이다."""

    ASIAN_RANGE = "asian_range"      # 00:00–06:00 UTC
    LONDON_OPEN = "london_open"      # 07:00–10:00 UTC
    NEW_YORK_OPEN = "new_york_open"  # 12:00–15:00 UTC
    LONDON_CLOSE = "london_close"    # 15:00–17:00 UTC
    NONE = "none"

    @property
    def korean(self) -> str:
        return {
            "asian_range": "아시안 레인지", "london_open": "런던 오픈",
            "new_york_open": "뉴욕 오픈", "london_close": "런던 클로즈",
            "none": "킬존 외",
        }[self.value]


WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def utc_hour(ts_ms: int) -> int:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).hour


def weekday(ts_ms: int) -> str:
    return WEEKDAYS[datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).weekday()]


def is_weekend(ts_ms: int) -> bool:
    """주말은 거래량이 줄고 성격이 달라진다. 암호화폐도 예외가 아니다."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).weekday() >= 5


def session_of(ts_ms: int) -> Session:
    hour = utc_hour(ts_ms)
    if hour < 8:
        return Session.ASIA
    if hour < 12:
        return Session.LONDON
    if hour < 16:
        return Session.OVERLAP
    if hour < 21:
        return Session.NEW_YORK
    return Session.LATE


def killzone_of(ts_ms: int) -> Killzone:
    hour = utc_hour(ts_ms)
    if hour < 6:
        return Killzone.ASIAN_RANGE
    if 7 <= hour < 10:
        return Killzone.LONDON_OPEN
    if 12 <= hour < 15:
        return Killzone.NEW_YORK_OPEN
    if 15 <= hour < 17:
        return Killzone.LONDON_CLOSE
    return Killzone.NONE


def parse_sessions(names: list[str]) -> set[Session]:
    table = {s.value: s for s in Session}
    out = set()
    for name in names:
        if name not in table:
            raise ValueError(f"알 수 없는 세션: {name} (가능: {sorted(table)})")
        out.add(table[name])
    return out


def parse_killzones(names: list[str]) -> set[Killzone]:
    table = {k.value: k for k in Killzone}
    out = set()
    for name in names:
        if name not in table:
            raise ValueError(f"알 수 없는 킬존: {name} (가능: {sorted(table)})")
        out.add(table[name])
    return out
