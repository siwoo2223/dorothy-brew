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


class ChatNotFound(Exception):
    pass


class KakaoDriver(Protocol):
    def open_chat(self, name: str, tab: str | None = None) -> object: ...
    def send_text(self, chat: object, text: str) -> None: ...
    def send_files(self, chat: object, files: list[str]) -> None: ...
    def close_chat(self, chat: object) -> None: ...


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
        for t in (tab, "chats" if tab == "friends" else "friends"):
            box = self._panel_box(main, t)
            if box:
                left, top, right, bottom = win32gui.GetWindowRect(box)
                self._log(f"검색칸({t}) 찾음: 크기 {right - left}x{bottom - top}")
                if right - left > 0:
                    return box, t
        raise RuntimeError("카카오톡 검색칸을 찾지 못했습니다. 카카오톡 메인 창에서 '채팅' 탭을 한 번 눌러 두고 다시 시도해 주세요.")

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
            chat = win32gui.FindWindow(None, name)  # 창 제목이 정확히 같은 채팅방만
            if chat and chat != main:
                return chat
        return 0

    def _close_wrong(self, before: dict[int, str], name: str) -> str:
        """찾는 방이 아닌데 열린 창이 있으면 닫고 그 제목을 돌려준다."""
        win32api, win32con, _ = self._w()
        wrong = ""
        for h, title in self._kakao_windows().items():
            if h not in before and title not in (MAIN_TITLE, name):
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
        read = _ocr_screen(results)
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
            if title == name:
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
        from .kakao_names import _keep_on_top

        win32api, win32con, win32gui = self._w()
        self.trace = []
        tab = tab or self.search_tab
        before = self._kakao_windows()
        already = win32gui.FindWindow(None, name)
        if already and already != win32gui.FindWindow(None, MAIN_TITLE):
            self._log(f"이미 열려 있는 채팅방 사용: '{name}'")
            return already

        box, used = self._search_box(tab)
        main = win32gui.FindWindow(None, MAIN_TITLE)
        _keep_on_top(main, True)  # 검색 결과를 읽고 누르는 동안 가려지지 않게
        try:
            win32api.SendMessage(box, win32con.WM_SETTEXT, 0, name)
            time.sleep(self.wait)
            self._log(f"검색어 '{name}' 입력({used})")
            results = self._search_list(main, used)
            if self.find_mode == "keyboard":
                chat, tried = self._open_by_keyboard(name, results, main, before)
            else:
                chat, tried = self._open_by_ocr(name, results, before)
        finally:
            win32api.SendMessage(box, win32con.WM_SETTEXT, 0, "")  # 검색어 지우기
            _keep_on_top(main, False)

        if not chat:
            raise ChatNotFound(
                f"검색 결과에 '{name}' 방이 없습니다(비슷한 방 {', '.join(repr(x) for x in tried)} 은 이름이 달라 닫음)"
            )
        self._log(f"채팅방 창 열림: '{win32gui.GetWindowText(chat)}'")
        return chat

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
        self._key(win32con.VK_CONTROL, ord("V"))
        time.sleep(2.0)
        import win32process
        fg = win32gui.GetForegroundWindow()
        if not fg or win32process.GetWindowThreadProcessId(fg)[1] != win32process.GetWindowThreadProcessId(chat)[1]:
            raise RuntimeError("전송 확인 창이 카카오톡 창이 아니어서 Enter 를 누르지 않았습니다")
        self._key(win32con.VK_RETURN)  # '전송' 확인 창
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

    def close_chat(self, chat) -> None:
        win32api, win32con, _ = self._w()
        win32api.PostMessage(chat, win32con.WM_CLOSE, 0, 0)


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
        try:
            if m.text.strip():
                self.driver.send_text(chat, m.text)
        except Exception as exc:
            self._close(chat)
            return SendResult(m.key, m.to, False, f"메시지 입력 실패: {exc}")
        note = " ⚠️ 전송 확인 필요(카톡 창에서 확인)" if getattr(self.driver, "unverified", False) else ""
        if m.attachments:
            try:
                note += self._send_attachments(chat, m)
            except Exception as exc:
                # 글은 이미 갔으므로 '성공'으로 남겨 중복 발송을 막고, 첨부 실패는 경고로 알린다
                note += f" ⚠️ 첨부 실패: {exc}"
        self._close(chat)
        return SendResult(m.key, m.to, True, f"{where}'{name}'에게 전송{note}")

    def _send_attachments(self, chat, m: OutgoingMessage) -> str:
        """사진은 세로로 이어 붙여 '그림'으로(사이트 '전체 복사'와 같음), 나머지 파일은 파일로 보낸다."""
        from .photo_merge import is_image, merge_photos

        photos = [a for a in m.attachments if is_image(a)]
        others = [a for a in m.attachments if a not in photos]
        parts = []
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
