"""PC 카카오톡 친구/채팅 목록에서 이름을 읽어 오고, 엑셀 고객명과 짝을 맞춘다.

PC 카카오톡 목록은 직접 그린(owner-drawn) 목록이라 일반적인 방법으로는 글자가 안 보일 수 있다.
그래서 아래 순서로 시도하고, 처음으로 이름이 나온 방법을 쓴다.
  1. MSAA  : 화면낭독기가 쓰는 접근성 정보를 목록 창에서 직접 읽기
  2. UIA   : Windows UI Automation 으로 목록 안의 모든 요소 이름 읽기
  3. OCR   : 목록 부분을 화면 캡처해서 Windows 내장 글자 인식(한국어)으로 읽기
목록을 조금씩 내리면서(스크롤) 끝까지 모은다. 어느 방법도 안 되면 diagnose() 결과를 개발자에게 보낸다.
"""
from __future__ import annotations

import difflib
import re
import sys
import time
from dataclasses import dataclass

MAIN_TITLE = "카카오톡"


# ───────────────────────── 목록 창 찾기 / 스크롤 (Windows) ─────────────────────────
def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("카톡 이름 읽어 오기는 Windows 에서만 됩니다.")


def _main_window() -> int:
    import win32gui

    main = win32gui.FindWindow(None, MAIN_TITLE)
    if not main:
        raise RuntimeError("카카오톡 창을 찾지 못했습니다. PC 카카오톡을 켜고 로그인해 주세요.")
    return main


def _child_windows(parent: int) -> list[int]:
    import win32gui

    found: list[int] = []
    win32gui.EnumChildWindows(parent, lambda h, _: found.append(h) or True, None)
    return found


def _list_window(main: int) -> int:
    """지금 보이는 친구/채팅 목록 창(클래스 이름에 ListControl 이 들어간 가장 큰 창)."""
    import win32gui

    best, best_area = 0, 0
    for h in _child_windows(main):
        if not win32gui.IsWindowVisible(h) or "ListControl" not in win32gui.GetClassName(h):
            continue
        left, top, right, bottom = win32gui.GetWindowRect(h)
        area = (right - left) * (bottom - top)
        if area > best_area:
            best, best_area = h, area
    if not best:
        raise RuntimeError("카카오톡 목록 창을 찾지 못했습니다. 메인 창에서 '친구' 또는 '채팅' 탭을 눌러 두세요.")
    return best


def _scroll(hwnd: int, notches: int) -> None:
    """목록 창에 마우스 휠 메시지를 보낸다 (음수: 아래로)."""
    import win32api
    import win32con
    import win32gui

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    x, y = (left + right) // 2, (top + bottom) // 2
    wparam = ((120 * notches) & 0xFFFF) << 16
    lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    win32api.SendMessage(hwnd, win32con.WM_MOUSEWHEEL, wparam, lparam)


def _keep_on_top(hwnd: int, on: bool) -> None:
    """카톡 창을 잠시 맨 앞에 고정했다가 푼다."""
    import win32con
    import win32gui

    try:
        if on:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST if on else win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        time.sleep(0.3)
    except Exception:
        pass


def _collect(read_visible, hwnd: int, wait: float, max_scrolls: int = 300) -> list[str]:
    """맨 위로 올린 뒤, 보이는 이름 읽기 → 아래로 스크롤 을 새 이름이 안 나올 때까지 반복."""
    for _ in range(30):
        _scroll(hwnd, 10)
    time.sleep(wait)
    names: list[str] = []
    seen: set[str] = set()
    still = 0
    for _ in range(max_scrolls):
        added = 0
        for name in read_visible():
            if name and name not in seen:
                seen.add(name)
                names.append(name)
                added += 1
        still = still + 1 if added == 0 else 0
        if still >= 3:
            break
        _scroll(hwnd, -3)
        time.sleep(wait)
    return names


# ───────────────────────── 1. MSAA ─────────────────────────
def _msaa_reader(hwnd: int):
    import ctypes

    import comtypes.client
    from comtypes.automation import VARIANT

    comtypes.client.GetModule("oleacc.dll")
    from comtypes.gen.Accessibility import IAccessible

    oleacc = ctypes.oledll.oleacc
    acc = ctypes.POINTER(IAccessible)()
    OBJID_CLIENT = ctypes.c_long(-4)
    oleacc.AccessibleObjectFromWindow(hwnd, OBJID_CLIENT, ctypes.byref(IAccessible._iid_), ctypes.byref(acc))

    def read_visible() -> list[str]:
        count = acc.accChildCount
        if not count:
            return []
        children = (VARIANT * count)()
        got = ctypes.c_long()
        oleacc.AccessibleChildren(acc, 0, count, children, ctypes.byref(got))
        out = []
        for v in children[: got.value]:
            try:
                value = v.value
                if isinstance(value, int):
                    name = acc.accName(value)
                else:
                    name = value.QueryInterface(IAccessible).accName(0)
            except Exception:
                continue
            out.append(name or "")
        return filter_ocr_lines(out)

    return read_visible


# ───────────────────────── 2. UIA ─────────────────────────
def _uia_reader(hwnd: int):
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_element_info import UIAElementInfo

    root = UIAWrapper(UIAElementInfo(hwnd))

    skip = {"ScrollBar", "Button", "Thumb", "Image"}

    def read_visible() -> list[str]:
        return filter_ocr_lines(
            [e.window_text() for e in root.descendants() if e.element_info.control_type not in skip]
        )

    return read_visible


# ───────────────────────── 3. OCR ─────────────────────────
NOISE_PATTERNS = [
    r"^(오전|오후)\s*\d{1,2}:\d{2}$",
    r"^\d{1,2}:\d{2}$",
    r"^(어제|그저께|오늘)",
    r"\d+\s*월\s*\d+\s*일",
    r"^\d{4}[.\-]\s*\d{1,2}[.\-]\s*\d{1,2}",
    r"^[\d\s,+.:\-]*$",  # 숫자·기호뿐 (안 읽은 수, 인원수 등)
    r"^(선물하기|이름 검색|통합검색|친구|채팅|전체|즐겨찾기|안읽음)$",
    r"^(즐겨찾는 친구|업데이트한 프로필|생일인 친구|채널|친구의 생일|추천 친구)",
    r"생일을 확인해",
    r"\d+개의 채팅방",
]


def filter_ocr_lines(lines: list[str]) -> list[str]:
    """OCR 로 읽은 줄 중 시간·날짜·숫자·메뉴 글자 같은 것을 빼고 이름 후보만 남긴다."""
    out = []
    for raw in lines:
        text = clean_name(raw)
        text = re.sub(r"\s+\d{1,4}$", "", text)  # 채팅방 이름 뒤 인원수 '기나글로벌 15'
        if not text or len(text) > 30:  # 너무 긴 줄은 대화 미리보기·상태메시지일 가능성이 큼
            continue
        if any(re.search(p, text) for p in NOISE_PATTERNS):
            continue
        out.append(text)
    return out


def _ocr_reader(hwnd: int):
    import asyncio

    import win32gui
    from PIL import ImageGrab

    try:
        import winocr
    except ImportError as exc:
        raise RuntimeError("OCR 모듈(winocr)이 없습니다. 실행.bat 을 다시 실행해 설치해 주세요.") from exc

    def read_visible() -> list[str]:
        img = ImageGrab.grab(bbox=win32gui.GetWindowRect(hwnd), all_screens=True)
        img = img.resize((img.width * 2, img.height * 2))  # 크게 하면 한글 인식이 좋아진다
        try:
            result = asyncio.run(winocr.to_coroutine(winocr.recognize_pil(img, "ko")))
        except AssertionError as exc:
            raise RuntimeError(
                "Windows 한국어 글자 인식 기능이 설치돼 있지 않습니다. 관리자 PowerShell 에서 "
                "Add-WindowsCapability -Online -Name \"Language.OCR~~~ko-KR~0.0.1.0\" 를 실행해 주세요."
            ) from exc
        return filter_ocr_lines([line.text for line in result.lines])

    return read_visible


# ───────────────────────── 공개 함수 ─────────────────────────
def extract_names(tab: str = "friends", wait: float = 0.4) -> tuple[list[str], str]:
    """지금 카카오톡 메인 창에 보이는 목록(친구 또는 채팅)의 이름을 모두 읽는다.

    돌려주는 값: (이름 목록, 사용한 방법)
    """
    _require_windows()
    main = _main_window()
    hwnd = _list_window(main)
    errors = []
    _keep_on_top(main, True)  # 글자 인식(OCR)은 화면을 캡처하므로 카톡 창이 가려지면 안 된다
    try:
        for label, make in (("MSAA", _msaa_reader), ("UIA", _uia_reader), ("OCR(글자 인식)", _ocr_reader)):
            try:
                reader = make(hwnd)
                first = reader()
                if not first:
                    errors.append(f"{label}: 이름 없음")
                    continue
                return _collect(reader, hwnd, wait), label
            except Exception as exc:  # 방법마다 실패 이유를 모아 둔다
                errors.append(f"{label}: {exc}")
    finally:
        _keep_on_top(main, False)
    raise RuntimeError("목록에서 이름을 읽지 못했습니다. (" + " / ".join(errors) + ") "
                       "아래 '진단 정보'를 복사해서 보내 주세요.")


def diagnose() -> str:
    """카카오톡 창 구조를 글로 정리한다 (개발자에게 보내 문제를 고치는 데 쓴다)."""
    _require_windows()
    import win32gui

    lines = []
    main = _main_window()
    lines.append(f"main hwnd={main} class={win32gui.GetClassName(main)} rect={win32gui.GetWindowRect(main)}")
    for h in _child_windows(main):
        lines.append(
            f"  hwnd={h} parent={win32gui.GetParent(h)} class={win32gui.GetClassName(h)!r} "
            f"text={win32gui.GetWindowText(h)!r} visible={win32gui.IsWindowVisible(h)} rect={win32gui.GetWindowRect(h)}"
        )
    _keep_on_top(main, True)
    try:
        hwnd = _list_window(main)
        lines.append(f"list hwnd={hwnd}")
        for label, make in (("MSAA", _msaa_reader), ("UIA", _uia_reader), ("OCR", _ocr_reader)):
            try:
                lines.append(f"{label}: {make(hwnd)()[:15]}")
            except Exception as exc:
                lines.append(f"{label} 오류: {type(exc).__name__}: {exc}")
        try:
            from pywinauto.controls.uiawrapper import UIAWrapper
            from pywinauto.uia_element_info import UIAElementInfo

            for e in UIAWrapper(UIAElementInfo(hwnd)).descendants()[:40]:
                info = e.element_info
                lines.append(f"    uia type={info.control_type} class={info.class_name!r} name={info.name!r}")
        except Exception as exc:
            lines.append(f"UIA 트리 오류: {exc}")
    except Exception as exc:
        lines.append(f"목록 창 오류: {exc}")
    finally:
        _keep_on_top(main, False)
    return "\n".join(lines)


def clean_name(text: str) -> str:
    """목록 항목 글자에서 이름만 남긴다(상태메시지·안 읽은 수 등이 붙어 오는 경우 대비)."""
    text = (text or "").strip()
    if not text:
        return ""
    return text.splitlines()[0].strip()


# ───────────────────────── 고객명 짝맞추기 ─────────────────────────
def _norm(text: str) -> str:
    """비교용: 띄어쓰기·괄호·특수문자·흔한 호칭 제거."""
    text = re.sub(r"\(.*?\)|\[.*?\]", "", text)
    text = re.sub(r"(사장님|대표님|님|매니저|실장|과장|대리|부장|이사)$", "", text.strip())
    return re.sub(r"[\s\W_]+", "", text).lower()


@dataclass
class Match:
    customer: str
    kakao_name: str  # 제안하는 카톡 이름 (없으면 "")
    score: float  # 0~1
    candidates: list[str]

    @property
    def level(self) -> str:
        if not self.kakao_name:
            return "없음"
        if self.score >= 0.99:
            return "일치"
        return "비슷함" if self.score >= 0.75 else "확인 필요"


def _score(customer: str, kakao: str) -> float:
    if customer == kakao:
        return 1.0
    a, b = _norm(customer), _norm(kakao)
    if not a or not b:
        return 0.0
    if a == b:
        return 0.99
    if a in b or b in a:  # '카페하늘' ↔ '카페하늘 김사장'
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_names(customers: list[str], kakao_names: list[str], top: int = 3) -> list[Match]:
    """고객명마다 가장 비슷한 카톡 이름을 제안한다."""
    results = []
    for cust in customers:
        scored = sorted(((_score(cust, k), k) for k in kakao_names), key=lambda x: -x[0])
        candidates = [k for s, k in scored[:top] if s >= 0.5]
        best_score, best = scored[0] if scored else (0.0, "")
        results.append(Match(cust, best if best_score >= 0.6 else "", best_score, candidates))
    return results
