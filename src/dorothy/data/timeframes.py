"""타임프레임 이름 → 밀리초. **여기가 유일한 출처다.**

전에는 loader와 resample이 각자 표를 들고 있었고 서로 달랐다.
loader에는 6h·12h가 없고 resample에는 8h가 없어서, 같은 이름이
한쪽에서는 되고 한쪽에서는 안 되는 상태였다. 표를 둘로 두면 반드시 어긋난다.
"""

from __future__ import annotations

TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,        # 펀딩 주기와 같다
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
}


def timeframe_ms(name: str) -> int:
    if name not in TIMEFRAME_MS:
        raise ValueError(f"지원하지 않는 타임프레임: {name} (가능: {sorted(TIMEFRAME_MS)})")
    return TIMEFRAME_MS[name]
