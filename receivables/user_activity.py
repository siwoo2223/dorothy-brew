"""이 PC 를 사람이 쓰고 있는지 알아낸다 (Windows 마지막 입력 시각).

2026-10-08 요청 - "도우미기능으로 진행을 해보자": 사장님 PC 한 대로 일도 하고 카톡 발송도 하므로,
마우스·키보드를 한동안 안 쓸 때만 보내고, 보내는 중에 손을 대면 바로 멈추게 하는 데 쓴다.
"""
from __future__ import annotations

import ctypes
import sys


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def last_input_tick() -> int:
    """마지막 마우스·키보드 입력 시각(부팅 후 ms, 32비트). Windows 가 아니면 0."""
    if sys.platform != "win32":
        return 0
    info = _LastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    return int(info.dwTime)


def idle_seconds() -> float:
    """마지막 입력 뒤로 몇 초 지났는지. Windows 가 아니면 아주 큰 값(늘 쉬는 중)."""
    if sys.platform != "win32":
        return 1e9
    now = int(ctypes.windll.kernel32.GetTickCount()) & 0xFFFFFFFF
    return ((now - last_input_tick()) & 0xFFFFFFFF) / 1000.0
