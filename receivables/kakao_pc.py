"""PC 카카오톡(Windows)을 조작해 내 계정으로 메시지를 보낸다.

카카오 공식 기능이 아니므로 카카오 운영정책상 계정이 제한될 수 있다.
위험을 줄이려고 메시지마다 넉넉한 간격을 두고, 하루 발송 수를 제한하며,
채팅방 제목이 받는 사람 이름과 정확히 같을 때만 보낸다(엉뚱한 방에 보내지 않도록).

동작 순서 (고객 1명마다)
  1. 카카오톡 메인 창의 친구 검색칸(친구 이름) 또는 채팅 검색칸(채팅방 이름)에 입력 → Enter → 채팅방이 열린다
  2. 창 제목이 그 이름과 같은 채팅방을 찾는다. 없으면 '채팅방을 찾지 못함'으로 실패 처리
  3. 입력칸에 메시지를 넣고 Enter
  4. 첨부(사진·파일)가 있으면 파일을 클립보드에 담아 채팅방에 붙여넣기(Ctrl+V) → 전송 확인 Enter
     (이 단계는 채팅방 창을 맨 앞으로 가져와 키보드 입력을 쓰므로, 발송 중에는 키보드·마우스를 쓰지 않는다)
  5. 창을 닫는다
"""
from __future__ import annotations

import random
import re
import unicodedata
import struct
import sys
import time
from pathlib import Path
from typing import Callable, Protocol

from .sender import OutgoingMessage, ResultCallback, SendResult

# PC 카카오톡 창 구조(클래스 이름). 카카오톡 업데이트로 바뀌면 여기만 고치면 된다.
MAIN_TITLE = "카카오톡"
CLASS_CHILD = "EVA_ChildWindow"
CLASS_PANEL = "EVA_Window"
CLASS_SEARCH = "Edit"
CLASS_INPUT = "RichEdit50W"
# 새 카톡 화면: 검색칸이 따로 창(Edit)이 아니라 화면을 눌러 입력하는 칸일 때의 표시
VIRTUAL_BOX = -1
# 2026-10-10 - 카톡 메인 창을 이 크기로 맞춘 뒤 검색 버튼을 찾는다(진단에 찍힌 지금 크기)
KAKAO_MAIN_SIZE = (510, 642)


class ChatNotFound(Exception):
    pass


class KakaoDriver(Protocol):
    def open_chat(self, name: str, tab: str | None = None) -> object: ...
    def send_text(self, chat: object, text: str) -> None: ...
    def send_files(self, chat: object, files: list[str]) -> None: ...
    def close_chat(self, chat: object) -> None: ...


def same_title(a: str, b: str) -> bool:
    """카톡 창 제목과 방 이름이 같은지. 한글 조합 방식·대시 모양(–, —, －)·띄어쓰기 차이는 같은 것으로 본다.
    2026-10-09 - 사이트에는 'KF - 유진애견샵', 실제 방은 'KF-유진애견샵'(띄어쓰기 없음)이라 못 보낸 일이 있어
    띄어쓰기는 아예 무시한다. (글자 자체가 다르면 다른 방 - 엉뚱한 방에 보내지 않기 위해 그 이상은 느슨하게 보지 않는다)"""
    def norm(s: str) -> str:
        s = unicodedata.normalize("NFC", s or "")
        for d in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d":
            s = s.replace(d, "-")
        return "".join(s.split())
    return norm(a) == norm(b)


def search_queries(name: str) -> list[str]:
    """카톡 검색창에 넣어 볼 검색어들. 먼저 방 이름 그대로, 못 찾으면 'KF -' 같은 앞머리를 뺀 핵심 이름으로.
    카톡 검색은 띄어쓰기까지 맞아야 걸리므로 'KF - 유진애견샵' 으로는 'KF-유진애견샵' 방이 안 나온다."""
    core = re.sub(r"^\s*KF\s*[-\u2010-\u2015\u2212\uff0d]\s*", "", unicodedata.normalize("NFC", name or ""), flags=re.I).strip()
    out = [name]
    for q in (core, name.replace(" ", "")):
        if q and q not in out:
            out.append(q)
    return out


# 친구 탭 검색 결과의 머리말 줄('친구 1', '즐겨찾기' 등) - 사람 이름 줄이 아니다
FRIEND_HEADER = re.compile(r"^(친구|즐겨찾기|내\s*프로필|채널|추천\s*친구|업데이트한\s*프로필|생일인\s*친구)\s*\d*$")


def find_line(lines, want: str, exact: bool = False, below: float | None = None):
    """OCR 줄들 중 글자가 want 와 같은(또는 want 를 포함하는) 줄. 띄어쓰기·기호는 무시. below 가 있으면 그 아래 줄만."""
    def key(s: str) -> str:
        return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFC", s or "")).lower()

    k = key(want)
    best = None
    for ln in lines:
        if below is not None and ln.y <= below:
            continue
        t = key(ln.text)
        if t == k or (not exact and k and k in t):
            if best is None or (t == k and key(best.text) != k):
                best = ln
    return best


class Win32KakaoDriver:
    """pywin32 로 PC 카카오톡 창에 직접 메시지를 보내는 드라이버 (Windows 전용)."""

    def __init__(self, search_tab: str = "friends", wait: float = 1.5, find_mode: str = "ocr"):
        if sys.platform != "win32":
            raise RuntimeError("PC 카카오톡 발송은 Windows 에서만 됩니다.")
        try:
            import win32api  # noqa: F401
            import win32con  # noqa: F401
            import win32gui  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("pywin32 가 필요합니다: pip install pywin32") from exc
        self.search_tab = search_tab  # "friends"(친구 탭) | "chats"(채팅 탭)
        self.wait = wait
        # 검색 결과에서 방을 고르는 방식: "ocr"(글자 인식으로 비슷한 줄 고르기) | "keyboard"(위에서부터 차례로 열어 창 제목 확인)
        self.find_mode = find_mode
        self.max_open_tries = 5
        self.trace: list[str] = []  # 마지막 발송의 단계별 기록 (발송 점검용)
        self.pasted = False
        self.send_key = ""  # 이 PC 카톡의 전송 키: "enter" 또는 "ctrl+enter" (한 번 확인되면 기억)
        self.unverified = False

    # ── 내부 도우미 ──
    def _w(self):
        import win32api
        import win32con
        import win32gui

        return win32api, win32con, win32gui

    def _press_enter(self, hwnd) -> None:
        win32api, win32con, _ = self._w()
        win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
        time.sleep(0.05)
        win32api.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 0)

    def _log(self, text: str) -> None:
        self.trace.append(text)

    def _main(self) -> int:
        win32api, win32con, win32gui = self._w()
        main = win32gui.FindWindow(None, MAIN_TITLE)
        if not main:
            raise RuntimeError("카카오톡이 실행되어 있지 않거나 로그인되지 않았습니다. PC 카카오톡을 먼저 켜 주세요.")
        if not win32gui.IsWindowVisible(main) or win32gui.IsIconic(main):  # 트레이·최소화 상태면 꺼낸다
            win32gui.ShowWindow(main, win32con.SW_SHOW)
            win32gui.ShowWindow(main, win32con.SW_RESTORE)
            time.sleep(0.5)
        return main

    def _panel_box(self, main: int, tab: str) -> int:
        _, _, win32gui = self._w()
        child = win32gui.FindWindowEx(main, None, CLASS_CHILD, None)
        friends = win32gui.FindWindowEx(child, None, CLASS_PANEL, None)
        panel = win32gui.FindWindowEx(child, friends, CLASS_PANEL, None) if tab == "chats" else friends
        return win32gui.FindWindowEx(panel, None, CLASS_SEARCH, None) if panel else 0

    def _search_box(self, tab: str) -> tuple[int, str]:
        """검색칸과 실제로 쓴 탭. 고른 탭의 검색칸이 숨겨져(크기 0) 있으면 다른 탭 검색칸을 쓴다."""
        _, _, win32gui = self._w()
        main = self._main()

        def visible(t: str) -> int:
            box = self._panel_box(main, t)
            if box:
                left, top, right, bottom = win32gui.GetWindowRect(box)
                self._log(f"검색칸({t}) 찾음: 크기 {right - left}x{bottom - top}")
                if right - left > 0:
                    return box
            return 0

        box = visible(tab)
        if not box:
            # 2026-10-10 진단 '입력칸 0x0': 새 카톡 화면은 목록 위 🔍 아이콘을 눌러야 검색칸이 나온다 → 먼저 열어 본다.
            box = self._open_search(main, tab, visible)
        if not box:
            # 2026-10-10 요청 - "검색이 채팅으로 안되어있으면 채팅으로 바꾸어서 눌러서 진행": 카톡 메인 창이 다른 탭
            # (친구·더보기 등)을 보고 있으면 그 탭의 검색칸이 숨겨져 있다 → 그 탭으로 바꾼 뒤 다시 찾는다.
            self._switch_tab(main, tab)
            box = visible(tab)
        if box:
            return box, tab
        other = "chats" if tab == "friends" else "friends"
        box = visible(other)
        if box:
            return box, other
        # 2026-10-10 - PC 를 다시 켠 뒤 '검색칸을 찾지 못했습니다': 카톡이 트레이에서 막 꺼내져 화면이 덜 그려졌을 수 있다
        # → 창을 제대로 펼치고 기다렸다가 두 번 더 찾아본다.
        from .kakao_names import _focus
        win32api, win32con, _ = self._w()
        for _try in range(2):
            win32gui.ShowWindow(main, win32con.SW_SHOWNORMAL)
            _focus(main)
            time.sleep(2.0)
            self._switch_tab(main, tab)
            time.sleep(0.5)
            box = visible(tab) or self._open_search(main, tab, visible) or visible(other)
            if box:
                return box, (tab if box in (VIRTUAL_BOX, self._panel_box(main, tab)) else other)
        raise RuntimeError("카카오톡 검색칸을 찾지 못했습니다. 카카오톡 메인 창이 열려 있고 잠금 화면이 아닌지 확인한 뒤 "
                           "'채팅' 탭을 한 번 눌러 두세요. [진단: " + self._diagnose(main) + "]")

    def _open_search(self, main: int, tab: str, visible) -> int:
        """새 카톡 화면의 목록 검색칸을 연다.

        2026-10-10 요청 - "사이즈 먼저 확정하고 검색 버튼을 찾아야 할 것 같아 / 빨간 박스에서 마우스가 왔다갔다":
        창 크기에 따라 🔍 위치가 달라져 정해진 거리로 누르면 빗나갔고, 새 검색칸은 예전 입력칸(Edit)이 아니라
        열려도 '못 열었다'고 보고 계속 눌렀다. →
          1) 카톡 창 크기를 정해진 크기로 맞춘다
          2) 이미 검색칸('채팅방, 참여자 검색')이 보이면 그 칸을 쓴다(화면 글자로 확인)
          3) 아니면 Ctrl+F, 그래도 아니면 제목 줄 오른쪽 아이콘 셋(🔍·오픈채팅·새 채팅)을 화면 그림에서 찾아 🔍 를 누른다
        반환: 예전 입력칸 창 번호, 또는 VIRTUAL_BOX(화면의 검색칸을 눌러 붙여넣기로 입력)."""
        from .kakao_names import _focus, _press

        _, win32con, win32gui = self._w()
        self._fix_size(main)
        box = visible(tab) or self._find_search_field(main, tab)
        if box:
            return box
        _focus(main)
        if win32gui.GetForegroundWindow() == main:
            _press(win32con.VK_CONTROL, ord("F"))
            time.sleep(0.8)
            box = visible(tab) or self._find_search_field(main, tab)
            if box:
                self._log("검색칸을 열었습니다(Ctrl+F)")
                return box
        icon = self._find_search_icon(main, tab)
        if icon:
            before = self._kakao_windows()
            _focus(main)
            self._click_at(*icon)
            time.sleep(0.8)
            for h in set(self._kakao_windows()) - set(before):  # 다른 창이 뜨면 닫는다
                win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
            box = visible(tab) or self._find_search_field(main, tab)
            if box:
                self._log("목록 위 🔍 아이콘을 눌러 검색칸을 열었습니다")
                return box
        return 0

    def _fix_size(self, main: int) -> None:
        """카톡 메인 창을 정해진 크기로(위치는 그대로). 최대화돼 있으면 먼저 원래 크기로."""
        _, win32con, win32gui = self._w()
        try:
            if win32gui.IsZoomed(main):
                win32gui.ShowWindow(main, win32con.SW_RESTORE)
                time.sleep(0.4)
            left, top, right, bottom = win32gui.GetWindowRect(main)
            w, h = KAKAO_MAIN_SIZE
            if abs((right - left) - w) > 8 or abs((bottom - top) - h) > 8:
                win32gui.SetWindowPos(main, 0, left, top, w, h, win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
                time.sleep(0.6)
                self._log(f"카톡 창 크기를 {w}x{h} 로 맞춤(전: {right - left}x{bottom - top})")
        except Exception as exc:
            self._log(f"카톡 창 크기를 맞추지 못함: {exc}")

    def _find_search_field(self, main: int, tab: str) -> int:
        """화면에 새 검색칸('채팅방, 참여자 검색' 등)이 보이면 그 위치를 기억하고 VIRTUAL_BOX, 아니면 0."""
        from .kakao_names import ocr_all_lines

        _, _, win32gui = self._w()
        try:
            lines = ocr_all_lines(main)
        except Exception:
            return 0
        _l, top, _r, bottom = win32gui.GetWindowRect(main)
        upper = top + (bottom - top) * 0.35
        hit = None
        for ln in lines:
            t = re.sub(r"\s+", "", ln.text)
            if ln.y > upper or "검색" not in t or t == "통합검색":
                continue
            if any(k in t for k in ("참여자", "채팅방", "이름", "친구", "검색")):
                hit = ln
                break
        if not hit:
            return 0
        self._search_pt = (hit.x + min(hit.w, 60) / 2, hit.y + hit.h / 2)
        self._search_bottom = hit.y + hit.h + 8
        self._log(f"화면의 검색칸 사용('{hit.text}')")
        return VIRTUAL_BOX

    def _find_search_icon(self, main: int, tab: str):
        """제목 줄 오른쪽의 아이콘 셋(🔍, 오픈채팅, 새 채팅)을 화면 그림에서 찾아 🔍 가운데 좌표를 돌려준다."""
        from PIL import ImageGrab

        from .kakao_names import _dpi_aware, ocr_all_lines

        _, _, win32gui = self._w()
        _dpi_aware()
        left, top, right, bottom = win32gui.GetWindowRect(main)
        try:
            scale = __import__("ctypes").windll.user32.GetDpiForWindow(main) / 96.0
        except Exception:
            scale = 1.0
        cy, title_right = top + 57 * scale, left + (right - left) * 0.4
        try:
            head = find_line(ocr_all_lines(main), "채팅" if tab == "chats" else "친구")
            if head and head.y - top < 140 * scale:
                cy, title_right = head.y + head.h / 2, head.x + head.w + 20 * scale
        except Exception:
            pass
        band = int(12 * scale)
        img = ImageGrab.grab(bbox=(int(title_right), int(cy - band), right, int(cy + band)), all_screens=True).convert("L")
        w, h = img.size
        px = img.load()
        dark = [any(px[x, y] < 110 for y in range(h)) for x in range(w)]
        clusters, run = [], None  # 어두운 세로줄 묶음 = 아이콘
        gap = int(5 * scale)
        for x, d in enumerate(dark):
            if d:
                if run and x - run[1] <= gap:
                    run[1] = x
                else:
                    run = [x, x]
                    clusters.append(run)
        clusters = [c for c in clusters if c[1] - c[0] >= 4 * scale]
        # 채팅 탭: 🔍·오픈채팅·새 채팅(오른쪽에서 셋째가 🔍), 친구 탭: 🔍·친구 추가(오른쪽에서 둘째가 🔍)
        need = 3 if tab == "chats" else 2
        if len(clusters) < need:
            self._log(f"🔍 아이콘을 화면에서 찾지 못함(아이콘 {len(clusters)}개)")
            return None
        c = clusters[-need]
        return int(title_right) + (c[0] + c[1]) / 2, cy

    def _set_search(self, box: int, text: str) -> None:
        """검색칸에 글자 넣기(빈 글자면 지우기). 예전 입력칸은 바로 넣고, 새 화면 검색칸은 눌러서 붙여넣는다."""
        win32api, win32con, _ = self._w()
        if box != VIRTUAL_BOX:
            win32api.SendMessage(box, win32con.WM_SETTEXT, 0, text)
            return
        from .kakao_names import _focus

        _focus(self._main())
        self._click_at(*self._search_pt)
        time.sleep(0.2)
        self._key(win32con.VK_CONTROL, ord("A"))
        self._key(win32con.VK_DELETE)
        if text:
            self._paste_text(text)

    def _box_bottom(self, box: int) -> float:
        _, _, win32gui = self._w()
        return self._search_bottom if box == VIRTUAL_BOX else win32gui.GetWindowRect(box)[3]

    def _diagnose(self, main: int) -> str:
        """검색칸을 못 찾았을 때 원인을 알 수 있게 메인 창 상태를 짧게 적는다."""
        _, _, win32gui = self._w()
        parts = []
        try:
            l, t, r, b = win32gui.GetWindowRect(main)
            parts.append(f"창 {r - l}x{b - t}{' 최소화' if win32gui.IsIconic(main) else ''}{'' if win32gui.IsWindowVisible(main) else ' 숨김'}")
            edits = []

            def visit(h, _):
                if win32gui.GetClassName(h) == CLASS_SEARCH:
                    el, et, er, eb = win32gui.GetWindowRect(h)
                    edits.append(f"{er - el}x{eb - et}")
                return True

            win32gui.EnumChildWindows(main, visit, None)
            parts.append("입력칸 " + (",".join(edits) or "없음"))
        except Exception as exc:
            parts.append(f"창 정보 오류 {exc}")
        try:
            from .kakao_names import ocr_all_lines
            parts.append("화면 글자: " + ", ".join(ln.text for ln in ocr_all_lines(main)[:8]))
        except Exception:
            pass
        return " / ".join(parts)

    def _switch_tab(self, main: int, tab: str) -> None:
        """카톡 메인 창 왼쪽의 친구/채팅 탭으로 바꾼다. 단축키(Ctrl+1 친구, Ctrl+2 채팅)를 먼저 쓰고,
        그래도 안 바뀌면 왼쪽 아이콘(친구 = 위에서 첫째, 채팅 = 둘째)을 누른다."""
        from .kakao_names import _focus, _press

        win32api, win32con, win32gui = self._w()
        _focus(main)
        if win32gui.GetForegroundWindow() == main:
            _press(win32con.VK_CONTROL, ord("2" if tab == "chats" else "1"))
            time.sleep(0.6)
            box = self._panel_box(main, tab)
            if box:
                left, _t, right, _b = win32gui.GetWindowRect(box)
                if right - left > 0:
                    self._log(f"카톡 메인 창을 '{'채팅' if tab == 'chats' else '친구'}' 탭으로 바꿈(단축키)")
                    return
        # 단축키가 안 먹으면 왼쪽 아이콘 클릭 (화면 배율에 맞춰 위치 계산)
        try:
            import ctypes
            scale = ctypes.windll.user32.GetDpiForWindow(main) / 96.0
        except Exception:
            scale = 1.0
        left, top, _r, _b = win32gui.GetWindowRect(main)
        x = left + int(34 * scale)
        y = top + int((62 if tab == "friends" else 122) * scale)
        _focus(main)
        if win32gui.GetForegroundWindow() == main:
            if win32gui.GetAncestor(win32gui.WindowFromPoint((x, y)), 2) == main:  # 2 = GA_ROOT, 다른 창에 가려지지 않았을 때만
                win32api.SetCursorPos((x, y))
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.6)
            self._log(f"카톡 메인 창 왼쪽 '{'채팅' if tab == 'chats' else '친구'}' 아이콘을 누름")

    def _kakao_windows(self) -> dict[int, str]:
        """카카오톡이 띄운 창들 {창: 제목}."""
        import win32process

        _, _, win32gui = self._w()
        main = win32gui.FindWindow(None, MAIN_TITLE)
        pid = win32process.GetWindowThreadProcessId(main)[1] if main else None
        found: dict[int, str] = {}

        def visit(h, _):
            if win32gui.IsWindowVisible(h) and win32process.GetWindowThreadProcessId(h)[1] == pid:
                found[h] = win32gui.GetWindowText(h)
            return True

        if pid:
            win32gui.EnumWindows(visit, None)
        return found

    def _text_len(self, box: int) -> int:
        """입력칸 글자 수 (다른 프로그램 창이라 WM_GETTEXTLENGTH 로 직접 묻는다)."""
        win32api, win32con, _ = self._w()
        return win32api.SendMessage(box, win32con.WM_GETTEXTLENGTH, 0, 0)

    def _input_box(self, chat) -> int:
        """채팅방 입력칸. 카톡 버전에 따라 창 안쪽 깊이 있을 수 있어 전체를 뒤진다."""
        _, _, win32gui = self._w()
        box = win32gui.FindWindowEx(chat, None, CLASS_INPUT, None)
        if box:
            return box
        found: list[int] = []

        def visit(h, _):
            if not found and win32gui.GetClassName(h).startswith("RichEdit"):
                found.append(h)
            return True

        win32gui.EnumChildWindows(chat, visit, None)
        return found[0] if found else 0

    # ── KakaoDriver 구현 ──
    def _search_list(self, main: int, tab: str) -> int:
        """검색어를 넣었을 때 나타나는 검색 결과 목록 창 (보이는 것)."""
        _, _, win32gui = self._w()
        child = win32gui.FindWindowEx(main, None, CLASS_CHILD, None)
        friends = win32gui.FindWindowEx(child, None, CLASS_PANEL, None)
        panel = win32gui.FindWindowEx(child, friends, CLASS_PANEL, None) if tab == "chats" else friends
        found: list[int] = []

        def visit(h, _):
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h).startswith("SearchListCtrl"):
                found.append(h)
            return True

        if panel:
            win32gui.EnumChildWindows(panel, visit, None)
        return found[0] if found else 0

    def _double_click(self, x: int, y: int) -> None:
        win32api, win32con, _ = self._w()
        win32api.SetCursorPos((x, y))
        for _ in range(2):
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.05)

    def _wait_chat(self, name: str, seconds: float = 4.0) -> int:
        _, _, win32gui = self._w()
        name = unicodedata.normalize("NFC", name)
        main = win32gui.FindWindow(None, MAIN_TITLE)
        for _ in range(int(seconds / 0.2)):
            time.sleep(0.2)
            chat = self._find_chat(name, main)  # 창 제목이 같은 채팅방만
            if chat:
                return chat
        return 0

    def _find_chat(self, name: str, main: int = 0) -> int:
        """이미 떠 있는 카톡 창 중 제목이 방 이름과 같은 것."""
        for h, title in self._kakao_windows().items():
            if h != main and title != MAIN_TITLE and same_title(title, name):
                return h
        return 0

    def _close_wrong(self, before: dict[int, str], name: str) -> str:
        """찾는 방이 아닌데 열린 창이 있으면 닫고 그 제목을 돌려준다."""
        win32api, win32con, _ = self._w()
        wrong = ""
        for h, title in self._kakao_windows().items():
            if h not in before and title != MAIN_TITLE and not same_title(title, name):
                wrong = wrong or title
                win32api.PostMessage(h, win32con.WM_CLOSE, 0, 0)
        return wrong

    def _open_by_ocr(self, name: str, results: int, before: dict[int, str]) -> tuple[int, list[str]]:
        """글자 인식으로 검색 결과에서 맞는 줄을 찾아 열고 창 제목으로 확인.

        화면에 안 보이는 아래쪽 결과까지 목록을 내리며 찾는다(최대 6화면).
        정확히 같은 줄이 있으면 그 줄을, 없으면 가장 비슷한 줄(최대 2개, 짧은 이름은 제외)을 연다.
        """
        from .kakao_names import _ocr_screen, _scroll, rank_search_results

        if not results:
            raise ChatNotFound(f"검색 결과에 '{name}' 방이 없습니다(검색 결과 목록이 보이지 않음)")
        below = getattr(self, "_results_below", None)
        if below is None:
            read = _ocr_screen(results)
        else:  # 메인 창 전체를 읽고 검색칸 아래 줄만 쓴다
            from .kakao_names import ocr_all_lines

            def read():
                return [ln for ln in ocr_all_lines(results) if ln.y > below]
        seen: list[str] = []
        previous = None
        chat, tried = 0, []
        for page in range(6):
            titles = read()
            names = [t.text for t in titles]
            self._log(f"검색 결과({page + 1}화면): " + (" | ".join(names) if names else "(읽지 못함)"))
            seen += [n for n in names if n not in seen]
            order = rank_search_results(name, names)
            for idx in order:  # 정확히 같은 줄 하나, 또는 가장 비슷한 줄부터 최대 3개
                t = titles[idx]
                self._log(f"'{t.text}' 더블클릭")
                self._double_click(int(t.x + min(t.w, 40) / 2), int(t.y + t.h / 2))
                chat = self._wait_chat(name, seconds=3.0)  # 창 제목이 엑셀 이름과 '정확히' 같아야 함
                if chat:
                    return chat, tried
                wrong = self._close_wrong(before, name)
                tried.append(wrong or t.text)
                self._log(f"열린 방 '{wrong}' 은(는) 이름이 달라 바로 닫음" if wrong else "창이 열리지 않음")
                time.sleep(0.5)
            if tried or names == previous:  # 이미 시도했거나, 내려도 화면이 그대로 = 결과 끝
                break
            previous = names
            _scroll(results, -5)  # 아래쪽 결과 보기
            time.sleep(0.5)
        if not tried:
            raise ChatNotFound(
                f"검색 결과에 '{name}' 방이 없습니다(같은 이름이 없어 아무 방도 열지 않음)"
                + (f". 검색된 방: {', '.join(seen[:8])}" if seen else "")
            )
        return chat, tried

    def _open_by_keyboard(self, name: str, results: int, main: int, before: dict[int, str]) -> tuple[int, list[str]]:
        """글자 인식 없이: 검색 결과 첫 줄 선택 → Enter(열기) → 창 제목 확인 → 다르면 닫고 ↓ 다음 줄. 최대 max_open_tries 줄."""
        from .kakao_names import _focus, _post_key, _press, _process_windows, _wait_new_window

        win32api, win32con, win32gui = self._w()
        import win32process

        if not results:
            raise ChatNotFound(f"검색 결과에 '{name}' 방이 없습니다(검색 결과 목록이 보이지 않음)")
        pid = win32process.GetWindowThreadProcessId(main)[1]
        left, top, right, _bottom = win32gui.GetWindowRect(results)
        if results == main and getattr(self, "_results_below", None):  # 결과 목록 창이 없으면 검색칸 바로 아래 첫 줄
            top = int(self._results_below)
        win32api.SetCursorPos(((left + right) // 2, top + 30))  # 첫 줄을 한 번 눌러 선택만 한다
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.4)
        tried: list[str] = []
        last = None
        for i in range(self.max_open_tries):
            opened = _process_windows(pid)
            _post_key(results, win32con.VK_RETURN)
            new = _wait_new_window(opened, main, pid, timeout=2.0)
            if not new:  # 목록에 직접 보낸 키가 안 먹으면 실제 키로 (카톡 메인 창이 맨 앞일 때만)
                _focus(main)
                if win32gui.GetForegroundWindow() == main:
                    _press(win32con.VK_RETURN)
                    new = _wait_new_window(opened, main, pid, timeout=2.0)
            if not new:
                self._log(f"{i + 1}번째 줄: 창이 열리지 않음")
                break
            title = win32gui.GetWindowText(new[0]).strip()
            self._log(f"{i + 1}번째 줄 열림: '{title}'")
            if same_title(title, name):
                return new[0], tried
            for h in new:
                win32api.PostMessage(h, win32con.WM_CLOSE, 0, 0)
            time.sleep(0.4)
            if title == last:  # ↓ 를 눌러도 같은 방 = 결과 끝
                break
            last = title
            tried.append(title)
            _post_key(results, win32con.VK_DOWN)
            time.sleep(0.3)
        return 0, tried

    # ── KakaoDriver 구현 ──
    def open_chat(self, name: str, tab: str | None = None):
        """검색 결과에서 가장 비슷한 줄을 열고, 창 제목이 엑셀 이름과 정확히 같을 때만 그 방을 쓴다.

        글자 인식(OCR)은 후보를 고르는 데만 쓰고(글자를 조금 틀려도 됨), 최종 확인은 창 제목(정확한 글자)으로 한다.
        맨 위 결과를 그냥 열지 않으며, 다른 방이 열리면 바로 닫고 다음 후보를 하나만 더 시도한다.

        tab: "friends"(친구 이름으로 찾기) | "chats"(채팅방 이름으로 찾기). 없으면 기본값.
        """
        win32api, win32con, win32gui = self._w()
        self.trace = []
        tab = tab or self.search_tab
        before = self._kakao_windows()
        already = self._find_chat(name, win32gui.FindWindow(None, MAIN_TITLE))
        if already:
            self._log(f"이미 열려 있는 채팅방 사용: '{name}'")
            return already

        # 2026-10-10 - 광고의 프로필 전송 뒤 카톡 메인 창이 친구 탭에 남아, 채팅방 'MANILA OFFICE' 를 친구 목록에서
        # 찾다가 '방이 없습니다' 가 났다(채팅 탭 검색칸은 친구 탭에서도 크기가 있어 '보인다'고 잘못 판단).
        # → 채팅방으로 찾을 때는 화면 글자로 채팅 탭인지 확인하고, 그래도 못 찾으면 채팅 탭으로 다시 바꿔 한 번 더 찾는다.
        if tab == "chats":
            self._ensure_chats_tab(self._main())
        chat, tried = self._find_and_open(name, tab, before)
        if not chat and tab == "chats":
            self._log("채팅방을 못 찾음 → 카톡 메인 창을 채팅 탭으로 다시 바꾸고 한 번 더 찾기")
            self._ensure_chats_tab(self._main(), force_click=True)
            chat, more = self._find_and_open(name, tab, before)
            tried += [t for t in more if t not in tried]

        if not chat:
            raise ChatNotFound(
                f"검색 결과에 '{name}' 방이 없습니다"
                + (f"(비슷한 방 {', '.join(repr(x) for x in tried)} 은 이름이 달라 닫음)" if tried
                   else f"(검색어 {', '.join(repr(q) for q in search_queries(name))} 로 찾아봤지만 같은 이름의 방이 없음)")
            )
        self._log(f"채팅방 창 열림: '{win32gui.GetWindowText(chat)}'")
        return chat

    def _find_and_open(self, name: str, tab: str, before: dict[int, str]) -> tuple[int, list[str]]:
        from .kakao_names import _keep_on_top

        win32api, win32con, win32gui = self._w()
        box, used = self._search_box(tab)
        main = win32gui.FindWindow(None, MAIN_TITLE)
        _keep_on_top(main, True)  # 검색 결과를 읽고 누르는 동안 가려지지 않게
        chat, tried = 0, []
        try:
            for query in search_queries(name):
                chat, more = self._search_and_open(name, query, box, used, main, before)
                tried += [t for t in more if t not in tried]
                if chat:
                    break
        finally:
            self._set_search(box, "")  # 검색어 지우기
            _keep_on_top(main, False)
        return chat, tried

    def _search_and_open(self, name: str, query: str, box: int, used: str, main: int, before: dict[int, str]) -> tuple[int, list[str]]:
        """검색창에 query 를 넣고 결과에서 창 제목이 name 과 같은 방을 연다. 반환: (창 또는 0, 열어 봤던 다른 방들)"""
        win32api, win32con, win32gui = self._w()
        self._set_search(box, "")
        time.sleep(0.2)
        self._set_search(box, query)
        time.sleep(self.wait)
        self._log(f"검색어 '{query}' 입력({used})")
        results = self._search_list(main, used)
        if not results:
            # 2026-10-10 - 새 카톡 화면은 검색 결과를 따로 목록 창에 띄우지 않을 수 있다 → 메인 창의 검색칸 아래를 읽는다
            results = main
            self._results_below = self._box_bottom(box) + 2
            self._log("검색 결과 목록 창이 없어 메인 창에서 검색칸 아래 글자를 읽습니다")
        else:
            self._results_below = None
        if self.find_mode == "keyboard":
            try:
                return self._open_by_keyboard(name, results, main, before)
            except ChatNotFound as exc:
                self._log(f"못 찾음({exc})")
                return 0, []
        # 2026-10-09 - 글자 인식이 이름을 잘못 읽어('양승태'→'야승대', '유니'→'(20') 있는 방을 못 찾는 일이
        # 있었다. 글자 인식으로 못 찾으면 검색 결과를 위에서부터 차례로 열어 창 제목으로 확인한다
        # (검색어로 걸러진 결과라 대개 첫 줄이 맞는 방이고, 아니면 바로 닫는다).
        try:
            chat, tried = self._open_by_ocr(name, results, before)
        except ChatNotFound as exc:
            chat, tried = 0, []
            self._log(f"글자 인식으로 못 찾음({exc}) → 차례로 열어 확인")
        if chat:
            return chat, tried
        if tried:  # 글자 인식으로 연 방이 있었다면 검색 목록을 다시 띄운다
            self._set_search(box, "")
            time.sleep(0.3)
            self._set_search(box, query)
            time.sleep(self.wait)
            results = self._search_list(main, used) or main
        try:
            chat, more = self._open_by_keyboard(name, results, main, before)
        except ChatNotFound as exc:
            self._log(f"못 찾음({exc})")
            chat, more = 0, []
        return chat, tried + [t for t in more if t not in tried]

    # ── 직원 프로필 전송 (광고 올리기) ──
    def _click_at(self, x: float, y: float, right: bool = False) -> None:
        win32api, win32con, _ = self._w()
        win32api.SetCursorPos((int(x), int(y)))
        down, up = ((win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP) if right
                    else (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP))
        win32api.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.05)
        win32api.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.4)

    def _new_window(self, before: dict[int, str], seconds: float = 4.0) -> int:
        """before 에 없던 카톡 창(메뉴·대화상자)이 뜨면 그 창."""
        end = time.time() + seconds
        while time.time() < end:
            new = [h for h in self._kakao_windows() if h not in before]
            if new:
                time.sleep(0.4)
                return new[-1]
            time.sleep(0.2)
        return 0

    def send_profile(self, friend: str, room: str) -> None:
        """친구 목록의 friend(직원)를 오른쪽 클릭 → '프로필 전송' → 공유 대상 선택에서 '채팅' 탭 → room 검색 → 선택 → 확인.

        2026-10-10 요청 - "내프로필은 아니라 직원들 프로필을 올릴꺼야": 광고 올리기의 마지막 단계.
        글자 인식(OCR)으로 메뉴·탭·버튼 글자를 찾아 누른다. 못 찾으면 대화상자를 닫고 오류를 낸다(이미 간 사진·문구는 그대로).
        """
        from .kakao_names import _keep_on_top, _ocr_screen, ocr_all_lines, pick_search_result

        win32api, win32con, win32gui = self._w()
        main = self._main()
        _keep_on_top(main, True)
        dialog = 0
        try:
            # 2026-10-10 - 프로필 실패 로그의 '읽은 글자: 전체, 즐겨찾기 안읽음, 기나글로벌…' = 채팅 탭 화면이었다.
            # 친구 탭 검색칸은 채팅 탭에서도 크기가 있어 '보인다'고 잘못 판단했다 → 항상 친구 탭으로 바꾸고 화면 글자로 확인한다.
            self._ensure_friends_tab(main)
            def visible_box(t: str) -> int:
                b = self._panel_box(main, t)
                if b:
                    bl, _bt, br, _bb = win32gui.GetWindowRect(b)
                    if br - bl > 0:
                        return b
                return 0

            # 새 카톡 화면은 🔍 아이콘을 눌러야 검색칸이 나온다
            box = visible_box("friends") or self._open_search(main, "friends", visible_box)
            if not box:
                raise RuntimeError("친구 탭 검색칸을 찾지 못했습니다 [진단: " + self._diagnose(main) + "]")
            self._set_search(box, friend)
            time.sleep(self.wait)
            results = self._search_list(main, "friends")
            if results:
                titles = _ocr_screen(results)()
            else:
                # 2026-10-10 - "kflogistics 검색 결과가 보이지 않습니다": 친구 탭은 검색 결과를 따로 목록 창에 띄우지 않고
                # 메인 창의 친구 목록 자체를 걸러서 보여 준다 → 메인 창에서 검색칸 아래 글자를 읽어 그 이름 줄을 찾는다.
                box_bottom = self._box_bottom(box)
                titles = [ln for ln in ocr_all_lines(main) if ln.y > box_bottom + 2]
            idx = pick_search_result(friend, [x.text for x in titles])
            if idx is None:
                # 2026-10-10 - 'kflogistics' 는 검색 결과가 한 줄인데 이름 글자를 못 읽고 상태 메시지
                # ('평일8-5시 토요일 8-13시 상담가능')만 읽혔다. 검색으로 걸러진 목록이라 머리말('친구 1' 등)을 뺀
                # 첫 줄이 그 친구다 → 그 줄을 쓴다.
                rows = [x for x in titles if not FRIEND_HEADER.match(x.text.strip())]
                if not rows:
                    raise RuntimeError(f"친구 목록에 '{friend}' 이(가) 없습니다(읽은 글자: {', '.join(x.text for x in titles[:6])})")
                idx = titles.index(rows[0])
                self._log(f"'{friend}' 이름 글자는 못 읽었지만 검색 결과 첫 줄('{rows[0].text}')을 사용")
            row = titles[idx]
            before = self._kakao_windows()
            self._click_at(row.x + min(row.w, 40) / 2, row.y + row.h / 2, right=True)
            menu = self._new_window(before, 3.0)
            if not menu:
                raise RuntimeError("오른쪽 클릭 메뉴가 뜨지 않았습니다")
            item = find_line(ocr_all_lines(menu), "프로필 전송")
            if not item:
                win32api.PostMessage(menu, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
                raise RuntimeError("메뉴에서 '프로필 전송'을 찾지 못했습니다")
            before = self._kakao_windows()
            self._click_at(item.x + item.w / 2, item.y + item.h / 2)
            dialog = self._new_window(before, 4.0)
            if not dialog:
                raise RuntimeError("'공유 대상 선택' 창이 뜨지 않았습니다")
            _keep_on_top(dialog, True)
            time.sleep(0.5)  # 창 안 글자가 다 그려질 시간
            lines = ocr_all_lines(dialog)
            head = find_line(lines, "공유 대상 선택") or find_line(lines, "대화상대 선택")
            # 2026-10-10 - "'채팅' 탭을 찾지 못했습니다": 탭 글자가 '친구 채팅' 처럼 한 줄로 붙어 읽히면 줄 단위로는
            # 못 찾는다 → 낱말 단위로도 찾는다. 그래도 없으면(탭 없는 창) 탭을 누르지 않고 바로 검색한다.
            tab = self._find_tab(dialog, lines, head)
            if tab:
                self._click_at(tab.x + tab.w / 2, tab.y + tab.h / 2)
                time.sleep(0.5)
            else:
                self._log(f"공유 대상 선택 창에 '채팅' 탭이 안 보임 → 바로 검색(읽은 글자: {', '.join(x.text for x in lines[:8])})")
            dl, dt, dr, db = win32gui.GetWindowRect(dialog)
            top_y = (tab.y + tab.h) if tab else ((head.y + head.h) if head else dt + (db - dt) * 0.1)
            # 검색칸: 창 안의 입력칸(Edit)에 방 이름을 넣는다. 못 찾으면 탭(또는 제목) 바로 아래를 눌러 붙여넣기.
            edit = self._find_child(dialog, CLASS_SEARCH)
            if edit:
                win32api.SendMessage(edit, win32con.WM_SETTEXT, 0, room)
                top_y = max(top_y, win32gui.GetWindowRect(edit)[3])
            else:
                self._click_at((dl + dr) / 2, top_y + (db - dt) * 0.07)
                self._paste_text(room)
                top_y += (db - dt) * 0.1
            time.sleep(self.wait)
            lines = ocr_all_lines(dialog)
            names = [ln for ln in lines if ln.y > top_y and find_line([ln], "확인", exact=True) is None
                     and find_line([ln], "취소", exact=True) is None
                     and find_line([ln], "전송", exact=True) is None and find_line([ln], "공유", exact=True) is None]
            # 결과 줄은 'MANILA OFFICE 5' 처럼 방 이름 뒤에 인원수가 붙는다 → 숫자를 떼고 비교
            idx = pick_search_result(room, [re.sub(r"\s+\d+$", "", ln.text.strip()) for ln in names])
            if idx is None:
                raise RuntimeError(f"공유 대상 선택 창에서 '{room}' 방을 찾지 못했습니다(읽은 글자: {', '.join(x.text for x in names[:6])})")
            target = names[idx]
            # 2026-10-10 화면 - 방 줄 오른쪽 끝의 동그라미(선택 버튼)를 누른다. 그 뒤 '확인' 버튼이 노랗게 켜진다.
            dl, dt, dr, db = win32gui.GetWindowRect(dialog)
            try:
                import ctypes
                scale = ctypes.windll.user32.GetDpiForWindow(dialog) / 96.0
            except Exception:
                scale = 1.0
            self._click_at(dr - 40 * scale, target.y + target.h / 2 + 4 * scale)
            time.sleep(0.4)
            lines = ocr_all_lines(dialog)
            ok = next((b for b in (find_line(lines, w, exact=True, below=target.y) for w in ("확인", "전송", "공유")) if b), None)
            if not ok:
                raise RuntimeError(f"'확인' 버튼을 찾지 못했습니다(읽은 글자: {', '.join(x.text for x in lines[-6:])})")
            self._click_at(ok.x + ok.w / 2, ok.y + ok.h / 2)
            time.sleep(1.0)
            if win32gui.IsWindow(dialog) and win32gui.IsWindowVisible(dialog):
                raise RuntimeError("'확인'을 눌렀지만 공유 대상 선택 창이 닫히지 않았습니다(방 선택이 안 됐을 수 있음)")
            dialog = 0
            self._log(f"'{friend}' 프로필을 '{room}' 에 전송")
        finally:
            if dialog and win32gui.IsWindow(dialog):
                win32api.PostMessage(dialog, win32con.WM_CLOSE, 0, 0)
            try:
                b = self._panel_box(main, "friends")
                if b:
                    win32api.SendMessage(b, win32con.WM_SETTEXT, 0, "")
                self._ensure_chats_tab(main)  # 다음 발송을 위해 채팅 탭으로 되돌림(화면 글자로 확인)
            except Exception:
                pass
            _keep_on_top(main, False)

    def _find_tab(self, dialog: int, lines, head):
        """공유 대상 선택 창의 '채팅' 탭 글자 위치(줄 → 낱말 순으로 찾기). 없으면 None."""
        from .kakao_names import ocr_all_lines

        below = head.y if head else None
        for want in ("채팅", "채팅방"):
            tab = find_line(lines, want, exact=True, below=below)
            if tab:
                return tab
        try:
            words = ocr_all_lines(dialog, words=True)
        except Exception:
            return None
        for want in ("채팅", "채팅방"):
            tab = find_line(words, want, exact=True, below=below)
            if tab:
                return tab
        return None

    def _find_child(self, parent: int, cls: str) -> int:
        """parent 안(몇 겹 안쪽 포함)에서 보이는 cls 창 하나."""
        _, _, win32gui = self._w()
        found = []

        def visit(h, _):
            if not found and win32gui.GetClassName(h) == cls and win32gui.IsWindowVisible(h):
                found.append(h)
            return True

        try:
            win32gui.EnumChildWindows(parent, visit, None)
        except Exception:
            pass
        return found[0] if found else 0

    def send_photo_album(self, chat, files: list[str]) -> None:
        """사진 여러 장을 카톡 '사진 묶음'으로: 채팅방 왼쪽 아래 📄(파일) 버튼 → 파일 선택 창에서 그 폴더로 이동 →
        사진 전부 선택 → 열기 → (카톡 확인 창이 뜨면) 전송.

        2026-10-10 요청 - "탐색기에서 복사해서 하는 방식은 안되고, 왼쪽 아래 파일 버튼 눌러서 위치로 가서 1~15번 사진을 눌러줘야".
        사진은 짧은 이름(01.jpg …)으로 한 폴더에 모아 순서대로 선택한다.
        """
        import shutil
        import tempfile

        win32api, win32con, win32gui = self._w()
        folder = Path(tempfile.mkdtemp(prefix="kf_album_"))
        names = []
        for i, f in enumerate(files, start=1):
            name = f"{i:02d}{Path(f).suffix.lower() or '.jpg'}"
            shutil.copyfile(f, folder / name)
            names.append(name)

        self._raise_chat(chat)
        before = self._kakao_windows()
        dialog = 0
        # ① 채팅방 왼쪽 아래 세 번째 아이콘(📄 파일)을 누른다. 안 뜨면 단축키(Ctrl+T)
        try:
            import ctypes
            scale = ctypes.windll.user32.GetDpiForWindow(chat) / 96.0
        except Exception:
            scale = 1.0
        left, _t, _r, bottom = win32gui.GetWindowRect(chat)
        for dx, dy in ((100, 28), (96, 32), (104, 24)):
            self._raise_chat(chat)
            self._click_at(left + dx * scale, bottom - dy * scale)
            dialog = self._file_dialog(before, 3.0)
            if dialog:
                break
        if not dialog:
            self._raise_chat(chat)
            self._key(win32con.VK_CONTROL, ord("T"))
            dialog = self._file_dialog(before, 3.0)
        if not dialog:
            raise RuntimeError("파일 선택 창이 뜨지 않았습니다(채팅방 왼쪽 아래 📄 버튼)")
        edit = self._dialog_filename_edit(dialog)
        if not edit:
            win32api.PostMessage(dialog, win32con.WM_CLOSE, 0, 0)
            raise RuntimeError("파일 선택 창의 '파일 이름' 칸을 찾지 못했습니다")
        open_btn = win32gui.GetDlgItem(dialog, 1)  # '열기' 버튼(IDOK)
        # ② 폴더로 이동: 파일 이름 칸에 폴더 경로를 넣고 열기
        win32api.SendMessage(edit, win32con.WM_SETTEXT, 0, str(folder))
        time.sleep(0.3)
        win32api.SendMessage(open_btn, win32con.BM_CLICK, 0, 0)
        time.sleep(1.2)
        # ③ 사진 전부 선택: "01.jpg" "02.jpg" … 를 넣고 열기
        edit = self._dialog_filename_edit(dialog) or edit
        win32api.SendMessage(edit, win32con.WM_SETTEXT, 0, " ".join(f'"{n}"' for n in names))
        time.sleep(0.3)
        before = self._kakao_windows()
        win32api.SendMessage(win32gui.GetDlgItem(dialog, 1), win32con.BM_CLICK, 0, 0)
        time.sleep(1.5)
        if win32gui.IsWindow(dialog) and win32gui.IsWindowVisible(dialog):
            win32api.PostMessage(dialog, win32con.WM_CLOSE, 0, 0)
            raise RuntimeError("파일 선택 창에서 사진을 열지 못했습니다")
        self._log(f"파일 선택 창에서 사진 {len(names)}장 선택")
        # ④ 카톡이 '전송' 확인 창을 띄우면 '전송' 버튼을 누른다(안 뜨면 바로 올라가는 것)
        confirm = next((h for h in self._kakao_windows() if h not in before and h != chat), 0)
        if confirm:
            from .kakao_names import ocr_all_lines

            lines = ocr_all_lines(confirm)
            button = find_line(lines, "전송", exact=True) or find_line(lines, "보내기", exact=True) or find_line(lines, "확인", exact=True)
            if button:
                self._click_at(button.x + button.w / 2, button.y + button.h / 2)
                self._log(f"사진 전송 확인 창의 '{button.text}' 누름")
            else:
                self._key(win32con.VK_RETURN)
            time.sleep(1.0)
        time.sleep(3.0 + 0.7 * len(names))  # 올라가는 시간(닫을 때도 다 올라갈 때까지 기다림)

    def _file_dialog(self, before: dict[int, str], seconds: float) -> int:
        """새로 뜬 윈도우 파일 선택 창(#32770)."""
        _, _, win32gui = self._w()
        end = time.time() + seconds
        while time.time() < end:
            for h in self._kakao_windows():
                if h not in before and win32gui.GetClassName(h) == "#32770":
                    time.sleep(0.5)
                    return h
            time.sleep(0.2)
        return 0

    def _dialog_filename_edit(self, dialog: int) -> int:
        """파일 선택 창의 '파일 이름' 입력칸(ComboBoxEx32 > ComboBox > Edit)."""
        _, _, win32gui = self._w()
        found: list[int] = []

        def visit(h, _):
            if win32gui.GetClassName(h) == "Edit" and win32gui.IsWindowVisible(h):
                parent = win32gui.GetParent(h)
                if win32gui.GetClassName(parent) == "ComboBox" and win32gui.GetClassName(win32gui.GetParent(parent)) == "ComboBoxEx32":
                    found.append(h)
            return True

        win32gui.EnumChildWindows(dialog, visit, None)
        return found[0] if found else 0

    def _ensure_friends_tab(self, main: int) -> None:
        """카톡 메인 창을 친구 탭으로. 단축키(Ctrl+1) → 화면에 채팅 탭 글자(안읽음 등)가 보이면 왼쪽 첫째 아이콘 클릭."""
        from .kakao_names import _focus, _press, ocr_all_lines

        win32api, win32con, win32gui = self._w()

        def on_chats() -> bool:
            texts = " ".join(ln.text for ln in ocr_all_lines(main))
            return "안읽음" in texts or "안 읽음" in texts or ("채팅" in texts and "친구" not in texts)

        _focus(main)
        _press(win32con.VK_CONTROL, ord("1"))
        time.sleep(0.8)
        if not on_chats():
            self._log("카톡 메인 창 '친구' 탭(단축키)")
            return
        try:
            import ctypes
            scale = ctypes.windll.user32.GetDpiForWindow(main) / 96.0
        except Exception:
            scale = 1.0
        left, top, _r, _b = win32gui.GetWindowRect(main)
        for y in (62, 70, 55):  # 왼쪽 맨 위 사람 모양 아이콘
            _focus(main)
            self._click_at(left + 34 * scale, top + y * scale)
            time.sleep(0.6)
            if not on_chats():
                self._log("카톡 메인 창 왼쪽 '친구' 아이콘을 누름")
                return
        raise RuntimeError("카톡 메인 창을 친구 탭으로 바꾸지 못했습니다(왼쪽 맨 위 사람 아이콘을 한 번 눌러 두세요)")

    def _ensure_chats_tab(self, main: int, force_click: bool = False) -> None:
        """카톡 메인 창을 채팅 탭으로. 단축키(Ctrl+2) → 화면 글자로 확인 → 아니면 왼쪽 둘째(말풍선) 아이콘 클릭.
        못 바꿔도 오류는 내지 않는다(검색이 실패하면 그때 '방이 없습니다'로 알려짐)."""
        from .kakao_names import _focus, _press, ocr_all_lines

        win32api, win32con, win32gui = self._w()

        def on_chats() -> bool:
            try:
                texts = " ".join(ln.text for ln in ocr_all_lines(main))
            except Exception:
                return True  # 화면을 못 읽으면 예전처럼 그냥 진행
            if "안읽음" in texts or "안 읽음" in texts:
                return True
            return "채팅" in texts and "친구" not in texts

        if not force_click:
            _focus(main)
            _press(win32con.VK_CONTROL, ord("2"))
            time.sleep(0.8)
            if on_chats():
                return
        try:
            import ctypes
            scale = ctypes.windll.user32.GetDpiForWindow(main) / 96.0
        except Exception:
            scale = 1.0
        left, top, _r, _b = win32gui.GetWindowRect(main)
        for y in (122, 115, 130, 110):  # 왼쪽 위에서 둘째 말풍선 아이콘
            _focus(main)
            self._click_at(left + 34 * scale, top + y * scale)
            time.sleep(0.6)
            if on_chats():
                self._log("카톡 메인 창 왼쪽 '채팅' 아이콘을 누름")
                return
        self._log("⚠ 카톡 메인 창을 채팅 탭으로 바꾸지 못함(왼쪽 둘째 말풍선 아이콘을 한 번 눌러 두세요)")

    def _paste_text(self, text: str) -> None:
        import win32clipboard

        win32api, win32con, _ = self._w()
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        self._key(win32con.VK_CONTROL, ord("V"))
        time.sleep(0.3)

    def _post_enter(self, hwnd) -> None:
        """Enter 키 메시지를 정식 키 정보(스캔코드 0x1C)와 함께 보낸다."""
        win32api, win32con, _ = self._w()
        win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0x001C0001)
        time.sleep(0.05)
        win32api.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 0xC01C0001)

    def _raise_chat(self, chat) -> None:
        """채팅방 창을 모든 창 위로 올리고 맨 앞(키보드 입력 대상)으로 만든다."""
        win32api, win32con, win32gui = self._w()
        main = win32gui.FindWindow(None, MAIN_TITLE)
        flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
        if main:  # 메인 창이 '맨 위 고정'으로 남아 채팅방을 덮지 않게
            win32gui.SetWindowPos(main, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags | win32con.SWP_NOACTIVATE)
        win32gui.ShowWindow(chat, win32con.SW_RESTORE)
        win32gui.SetWindowPos(chat, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
        win32gui.SetWindowPos(chat, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)  # 위로 올린 뒤 고정은 푼다
        if win32gui.GetForegroundWindow() != chat:
            self._bring_to_front(chat)
        time.sleep(0.3)

    def _guard(self, chat) -> None:
        """키를 누르기 직전: 맨 앞 창이 채팅방이 아니면 멈춘다(다른 창에 키가 들어가지 않게)."""
        _, _, win32gui = self._w()
        fg = win32gui.GetForegroundWindow()
        if fg != chat:
            title = win32gui.GetWindowText(fg) if fg else ""
            raise RuntimeError(f"채팅방이 아닌 창('{title}')이 앞에 있어 키 입력을 멈췄습니다")

    def _click(self, hwnd, chat=None) -> None:
        """hwnd 가운데를 클릭. chat 을 주면 그 자리가 정말 그 채팅방인지 먼저 확인한다."""
        win32api, win32con, win32gui = self._w()
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        x, y = (left + right) // 2, (top + bottom) // 2
        if chat is not None:
            under = win32gui.WindowFromPoint((x, y))
            if under != hwnd and win32gui.GetAncestor(under, 2) != chat:  # 2 = GA_ROOT
                title = win32gui.GetWindowText(win32gui.GetAncestor(under, 2))
                raise RuntimeError(f"채팅방 입력칸이 다른 창('{title}')에 가려져 있어 누르지 않았습니다")
        win32api.SetCursorPos((x, y))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.3)

    def _paste(self, chat, box, text) -> None:
        """채팅방을 맨 위로 → 입력칸 클릭 → 전체 선택 → 붙여넣기(Ctrl+V). 카톡이 사람이 입력한 글로 인식한다."""
        import win32clipboard

        win32api, win32con, _ = self._w()
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        self._raise_chat(chat)
        self._click(box, chat)  # 입력칸에 키보드 포커스 (가려져 있으면 누르지 않음)
        self._guard(chat)
        win32api.SendMessage(box, 0x00B1, 0, -1)  # EM_SETSEL: 입력칸 글 전체 선택 (Ctrl+A 는 메인 창에서 '친구 추가'라 쓰지 않음)
        self._guard(chat)
        self._key(win32con.VK_CONTROL, ord("V"))
        time.sleep(0.6)
        if not self._text_len(box):
            raise RuntimeError("붙여넣기가 되지 않았습니다")
        self.pasted = True

    def send_text(self, chat, text: str) -> None:
        """붙여넣기로 글을 넣고 전송 키를 '한 번만' 누른다.

        중복 전송을 막기 위해, '확실히 안 보내졌다'는 증거(Enter 가 줄바꿈으로 들어가 글자가 늘어남)가 있을 때만
        다른 키(Ctrl+Enter)로 한 번 더 시도한다. 판단이 애매하면 다시 보내지 않고 self.unverified 로 표시한다.
        """
        win32api, win32con, win32gui = self._w()
        self.unverified = False
        box = self._input_box(chat)
        if not box:
            self._log("입력칸 못 찾음")
            raise RuntimeError("채팅방 입력칸을 찾지 못했습니다")
        self._log(f"입력칸 찾음({win32gui.GetClassName(box)})")
        self.pasted = False
        text = text.strip("\r\n ").replace("\r\n", "\n").replace("\n", "\r\n")  # 끝 빈 줄 제거
        self._paste(chat, box, text)  # 실패하면 예외 → 아무것도 안 보낸 상태
        self._log(f"붙여넣기 완료(글자 수 {self._text_len(box)})")

        keys = [(win32con.VK_CONTROL, win32con.VK_RETURN)] if self.send_key == "ctrl+enter" else [(win32con.VK_RETURN,)]
        if self.send_key != "ctrl+enter":
            keys.append((win32con.VK_CONTROL, win32con.VK_RETURN))
        for combo in keys:
            label = "Ctrl+Enter" if len(combo) == 2 else "Enter"
            before = self._text_len(box)
            self._guard(chat)
            self._key(*combo)  # 붙여넣기 바로 뒤에, 다른 동작 없이 누른다
            time.sleep(1.2)
            after = self._text_len(box)
            self._log(f"{label} 누름: 글자 수 {before} → {after}")
            if after > before:  # 줄바꿈이 들어감 = 확실히 안 보내짐 → 지우고 다음 키
                self._guard(chat)
                self._key(win32con.VK_BACK)
                time.sleep(0.3)
                self._log(f"{label} 가 줄바꿈으로 들어가 지움")
                continue
            self.send_key = "ctrl+enter" if label == "Ctrl+Enter" else "enter"
            if after < before:
                self._log(f"전송됨({label}, 입력칸 비워짐)")
            else:  # 입력칸이 그대로면 확신할 수 없음 → 다시 보내지도, 지우지도 않는다
                self.unverified = True
                self._log(f"{label} 누름 — 입력칸에 글이 그대로 있음(전송 확인 필요, 글은 남겨 둠)")
            return
        raise RuntimeError("Enter·Ctrl+Enter 모두 줄바꿈으로만 들어가 전송되지 않았습니다")

    def _bring_to_front(self, hwnd) -> None:
        win32api, win32con, win32gui = self._w()
        if win32gui.GetForegroundWindow() == hwnd:
            return  # 이미 맨 앞이면 Alt 를 누르지 않는다(Alt 가 입력칸 포커스를 빼앗아 Enter 가 안 먹힘)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        # Windows 는 다른 프로그램 창을 함부로 앞으로 못 가져오게 막아서, Alt 키를 눌렀다 떼는 방법을 쓴다
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        finally:
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.5)
        if win32gui.GetForegroundWindow() != hwnd:
            raise RuntimeError("채팅방 창을 맨 앞으로 가져오지 못했습니다(발송 중에는 다른 창을 누르지 마세요)")

    def _key(self, *keys) -> None:
        """keys 를 차례로 누르고 거꾸로 뗀다. 예: _key(VK_CONTROL, ord('V')) → Ctrl+V"""
        win32api, win32con, _ = self._w()
        for k in keys:
            win32api.keybd_event(k, 0, 0, 0)
            time.sleep(0.05)
        for k in reversed(keys):
            win32api.keybd_event(k, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.05)

    def send_image(self, chat, image_path: str) -> None:
        """사진 한 장을 '그림'으로 클립보드에 넣고 붙여넣는다(사이트 '전체 복사'와 같은 방식).
        파일(CF_HDROP)로 붙여넣으면 카톡이 사진을 안 보내는 경우가 있어 이 방식을 쓴다."""
        import io

        import win32clipboard
        from PIL import Image

        win32api, win32con, win32gui = self._w()
        buf = io.BytesIO()
        Image.open(image_path).convert("RGB").save(buf, "BMP")
        dib = buf.getvalue()[14:]  # BMP 파일 머리(14바이트)를 뺀 나머지가 CF_DIB
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
        finally:
            win32clipboard.CloseClipboard()
        self._paste_and_confirm(chat, wait_upload=3.0)

    def _paste_and_confirm(self, chat, wait_upload: float) -> None:
        """클립보드 내용을 채팅 입력칸에 붙여넣고, 카톡이 띄우는 '전송' 확인 창에서 Enter."""
        import win32clipboard

        win32api, win32con, win32gui = self._w()
        self._raise_chat(chat)
        box = self._input_box(chat)
        if box:  # 입력칸을 눌러 커서를 둔다 (가려져 있으면 누르지 않음)
            self._click(box, chat)
        self._guard(chat)
        before = self._kakao_windows()
        self._key(win32con.VK_CONTROL, ord("V"))
        time.sleep(2.0)
        # 2026-10-10 - "사진도 한번에 안올라갔어": 여러 장을 붙여넣으면 카톡이 '전송' 확인 창을 띄우는데, Enter 만으로는
        # 전송 버튼이 눌리지 않는 경우가 있었다 → 새로 뜬 확인 창에서 '전송' 글자를 찾아 직접 누른다(못 찾으면 Enter).
        confirm = next((h for h in self._kakao_windows() if h not in before and h != chat), 0)
        clicked = False
        if confirm:
            try:
                from .kakao_names import ocr_all_lines

                lines = ocr_all_lines(confirm)
                button = find_line(lines, "전송", exact=True) or find_line(lines, "보내기", exact=True) or find_line(lines, "확인", exact=True)
                if button:
                    self._click_at(button.x + button.w / 2, button.y + button.h / 2)
                    clicked = True
                    self._log(f"전송 확인 창의 '{button.text}' 버튼을 누름")
            except Exception as exc:
                self._log(f"전송 확인 창 글자 읽기 실패({exc}) → Enter")
        if not clicked:
            import win32process
            fg = win32gui.GetForegroundWindow()
            if not fg or win32process.GetWindowThreadProcessId(fg)[1] != win32process.GetWindowThreadProcessId(chat)[1]:
                raise RuntimeError("전송 확인 창이 카카오톡 창이 아니어서 Enter 를 누르지 않았습니다")
            self._key(win32con.VK_RETURN)  # '전송' 확인 창
        time.sleep(1.0)
        if confirm and win32gui.IsWindow(confirm) and win32gui.IsWindowVisible(confirm):
            raise RuntimeError("전송 확인 창이 닫히지 않았습니다(전송 버튼이 눌리지 않음)")
        time.sleep(wait_upload)  # 업로드 시간
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
        finally:
            win32clipboard.CloseClipboard()

    def send_files(self, chat, files: list[str]) -> None:
        import win32clipboard

        win32api, win32con, win32gui = self._w()
        paths = [str(Path(f).resolve()) for f in files]
        missing = [p for p in paths if not Path(p).is_file()]
        if missing:
            raise RuntimeError("첨부 파일이 없습니다: " + ", ".join(missing))

        # 탐색기에서 파일을 복사한 것과 같은 형식(CF_HDROP)으로 클립보드에 담는다
        dropfiles = struct.pack("<IiiII", 20, 0, 0, 0, 1)  # pFiles, pt.x, pt.y, fNC, fWide
        data = dropfiles + ("\0".join(paths) + "\0\0").encode("utf-16-le")
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
        finally:
            win32clipboard.CloseClipboard()

        # 붙여넣으면 카톡의 '파일 전송' 확인 창이 뜬다 → 그 창(카톡 것)에서만 Enter
        self._paste_and_confirm(chat, wait_upload=2.0 + 1.0 * len(paths))

    CLOSE_WAIT_MAX = 300  # 사진·파일 올리기가 끝나길 최대 몇 초 기다릴지

    def close_chat(self, chat) -> None:
        """채팅방을 닫는다. 사진·파일이 아직 올라가는 중이면 끝날 때까지 기다렸다가 닫는다.

        2026-10-08 요청 - "전송을 하다가 말고 그러고 있어 그러니까 다 전송을 하면 끄는 시스템으로 바꿔줘":
        올리는 중에 창을 닫으면 카톡이 '전송 중인 파일이 있습니다. 창을 닫으면 전송이 취소됩니다' 창을 띄운다.
        그 창이 뜨면 '닫지 않기'(그 확인 창만 닫음 = 취소)로 답하고 몇 초 뒤 다시 닫아 본다. 끝내 안 끝나면
        채팅방을 열어 둔 채로 둔다(닫아서 전송이 취소되는 것보다 낫다).
        """
        win32api, win32con, win32gui = self._w()
        deadline = time.time() + self.CLOSE_WAIT_MAX
        while True:
            before = set(self._kakao_windows())
            win32api.PostMessage(chat, win32con.WM_CLOSE, 0, 0)
            time.sleep(1.0)
            if not win32gui.IsWindow(chat) or not win32gui.IsWindowVisible(chat):
                return
            # 채팅방이 안 닫혔으면 '전송 중인 파일이 있습니다' 확인 창이 뜬 것 → 그 창만 닫는다(= 취소, 전송 계속)
            for h in set(self._kakao_windows()) - before:
                if h != chat:
                    win32api.PostMessage(h, win32con.WM_CLOSE, 0, 0)
            if time.time() > deadline:
                self.trace.append("⚠️ 사진·파일 올리기가 오래 걸려 채팅방을 닫지 않고 두었습니다")
                return
            time.sleep(5.0)


class KakaoPCSender:
    """PC 카카오톡으로 고객별 메시지를 하나씩 보낸다."""

    label = "PC카카오톡"

    def __init__(
        self,
        driver: KakaoDriver,
        min_interval: float = 8.0,
        max_interval: float = 15.0,
        max_consecutive_failures: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        should_stop: Callable[[], bool] | None = None,
    ):
        self.driver = driver
        self.min_interval = min_interval
        self.max_interval = max(max_interval, min_interval)
        self.max_consecutive_failures = max_consecutive_failures
        self.sleep = sleep
        if should_stop is None:
            from .control import stop_requested

            should_stop = stop_requested
        self.should_stop = should_stop  # '발송 중지' 버튼·ESC 키

    def send(self, messages: list[OutgoingMessage], on_result: ResultCallback | None = None) -> list[SendResult]:
        results: list[SendResult] = []
        failures_in_row = 0
        stopped = False
        for i, m in enumerate(messages):
            if stopped or self.should_stop():
                stopped = True
                result = SendResult(m.key, m.to, False, "발송 중지됨(미발송)")
            elif failures_in_row >= self.max_consecutive_failures:
                result = SendResult(m.key, m.to, False, f"연속 {failures_in_row}건 실패로 발송 중단(미발송)")
            else:
                result = self._send_one(m)
                if result.ok:
                    failures_in_row = 0
                elif not result.detail.startswith("검색 결과에"):  # '방 없음'은 아무 방도 안 연 안전한 건너뛰기라 세지 않음
                    failures_in_row += 1
                if i < len(messages) - 1:
                    self._pause(random.uniform(self.min_interval, self.max_interval))
            results.append(result)
            if on_result:
                on_result(i + 1, len(messages), result)
        return results

    def _pause(self, seconds: float) -> None:
        """메시지 사이 대기. 기다리는 중에도 중지 요청이 오면 바로 끝낸다."""
        if self.sleep is not time.sleep:  # 테스트용 가짜 대기
            self.sleep(seconds)
            return
        end = time.time() + seconds
        while time.time() < end and not self.should_stop():
            time.sleep(min(0.3, max(end - time.time(), 0)))

    def _send_one(self, m: OutgoingMessage) -> SendResult:
        name = unicodedata.normalize("NFC", m.chat_name).strip()  # 카톡 창 제목과 같은 '완성형' 한글로
        try:
            chat = self.driver.open_chat(name, m.search_tab or None)
        except ChatNotFound as exc:
            return SendResult(m.key, m.to, False, str(exc))
        except Exception as exc:  # 카카오톡 창 문제 등
            return SendResult(m.key, m.to, False, f"채팅방 열기 실패: {exc}")
        where = "채팅방 " if m.search_tab == "chats" else ""
        note = ""
        if m.photos_first and m.attachments:  # 광고: 사진 먼저
            try:
                note += self._send_attachments(chat, m)
            except Exception as exc:
                self._close(chat)
                return SendResult(m.key, m.to, False, f"사진 올리기 실패: {exc}")
        try:
            if m.text.strip():
                self.driver.send_text(chat, m.text)
        except Exception as exc:
            self._close(chat)
            if m.photos_first and m.attachments:  # 사진은 이미 갔으므로 다시 보내면 겹친다
                return SendResult(m.key, m.to, True, f"{where}'{name}'에 사진만 전송{note} ⚠️ 문구 실패: {exc}")
            return SendResult(m.key, m.to, False, f"메시지 입력 실패: {exc}")
        note += " ⚠️ 전송 확인 필요(카톡 창에서 확인)" if getattr(self.driver, "unverified", False) else ""
        if m.attachments and not m.photos_first:
            try:
                note += self._send_attachments(chat, m)
            except Exception as exc:
                # 글은 이미 갔으므로 '성공'으로 남겨 중복 발송을 막고, 첨부 실패는 경고로 알린다
                note += f" ⚠️ 첨부 실패: {exc}"
        self._close(chat)
        # 2026-10-10 광고 올리기: 직원 프로필 전송(친구 목록 → 프로필 전송 → 이 방)
        for friend in m.profiles:
            if not hasattr(self.driver, "send_profile"):
                note += f" ⚠️ 프로필 전송 미지원({friend})"
                continue
            try:
                self.driver.send_profile(friend, name)
                note += f" (+{friend} 프로필)"
            except Exception as exc:
                note += f" ⚠️ {friend} 프로필 실패: {exc}"
        return SendResult(m.key, m.to, True, f"{where}'{name}'에게 전송{note}")

    def _send_attachments(self, chat, m: OutgoingMessage) -> str:
        """사진은 세로로 이어 붙여 '그림'으로(사이트 '전체 복사'와 같음), 나머지 파일은 파일로 보낸다."""
        from .photo_merge import is_image, merge_photos

        photos = [a for a in m.attachments if is_image(a)]
        others = [a for a in m.attachments if a not in photos]
        parts = []
        if photos and not m.merge_photos:
            # 2026-10-10 요청 - 광고 사진은 카톡 '사진 묶음'(바둑판 앨범)으로: 여러 장을 한 번에 붙여넣으면 카톡이 묶어서 보낸다.
            # (이어 붙인 세로 그림이 아니라) 카톡 한 번에 최대 30장이라 30장씩 나눈다.
            # 2026-10-10 - 붙여넣기로는 묶음이 안 가서, 채팅방 📄 버튼 → 파일 선택 창에서 한 번에 고르는 방식
            send = getattr(self.driver, "send_photo_album", None) or self.driver.send_files
            for i in range(0, len(photos), 30):
                send(chat, photos[i:i + 30])
            parts.append(f"사진 {len(photos)}장 묶음")
            photos = []
        if photos and hasattr(self.driver, "send_image"):
            try:
                merged = merge_photos(photos, Path(photos[0]).parent / "_merged", stem="입고사진")
            except ImportError:  # Pillow 가 없으면 파일로라도 보낸다
                merged = []
            if merged:
                for path in merged:
                    self.driver.send_image(chat, str(path))
                parts.append(f"사진 {len(photos)}장")
            else:
                others = photos + others
        else:
            others = photos + others
        if others:
            self.driver.send_files(chat, others)
            parts.append(f"첨부 {len(others)}개")
        return f" (+{', '.join(parts)})" if parts else ""

    def _close(self, chat) -> None:
        try:
            self.driver.close_chat(chat)
        except Exception:
            pass


def check_send(name: str, tab: str = "friends",
               text: str = "[발송 점검] KF로지스틱 발송 프로그램 점검 메시지입니다.", find_mode: str = "ocr") -> list[str]:
    """테스트 받을 곳에 점검 메시지 1건을 보내며 단계별로 무엇이 됐는지 기록해 돌려준다."""
    driver = Win32KakaoDriver(search_tab=tab, find_mode=find_mode)
    chat = None
    try:
        chat = driver.open_chat(name, tab)
        driver.send_text(chat, text)
        driver.trace.append("✅ 점검 메시지 전송 성공")
    except Exception as exc:
        driver.trace.append(f"❌ 멈춘 곳: {exc}")
    finally:
        if chat:
            try:
                driver.close_chat(chat)
            except Exception:
                pass
    return driver.trace
