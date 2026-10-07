"""발송 중지 요청. 화면의 '발송 중지' 버튼이나 예약 실행 중에도 쓸 수 있도록 파일로 주고받는다."""
from __future__ import annotations

import sys
from pathlib import Path

STOP_FILE = Path(__file__).resolve().parent.parent / "logs" / "STOP"


def request_stop() -> None:
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STOP_FILE.write_text("stop", encoding="utf-8")


def clear_stop() -> None:
    STOP_FILE.unlink(missing_ok=True)


def escape_held() -> bool:
    """키보드 ESC 가 지금 눌려 있는지 (Windows)."""
    if sys.platform != "win32":
        return False
    try:
        import win32api
        import win32con

        return bool(win32api.GetAsyncKeyState(win32con.VK_ESCAPE) & 0x8000)
    except Exception:
        return False


def stop_requested() -> bool:
    return STOP_FILE.exists() or escape_held()
