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

    def __init__(self, search_tab: str = "friends", wait: float = 1.5):
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
        self.trace: list[str] = []  # 마지막 발송의 단계별 기록 (발송 점검용)
        self.preferred: str = ""  # 이 PC 카톡에서 한 번 성공한 전송 방법

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
    def open_chat(self, name: str, tab: str | None = None):
        """tab: "friends"(친구 이름으로 찾기) | "chats"(채팅방 이름으로 찾기). 없으면 기본값."""
        win32api, win32con, win32gui = self._w()
        self.trace = []
        tab = tab or self.search_tab
        before = self._kakao_windows()
        box, used = self._search_box(tab)
        win32api.SendMessage(box, win32con.WM_SETTEXT, 0, name)
        time.sleep(self.wait)
        self._press_enter(box)
        self._log(f"검색어 '{name}' 입력 후 Enter")

        chat = 0
        for _ in range(int(4 / 0.2)):  # 최대 4초 동안 채팅방이 뜨기를 기다림
            time.sleep(0.2)
            chat = win32gui.FindWindow(None, name)  # 창 제목이 정확히 같은 채팅방만
            if chat and chat != win32gui.FindWindow(None, MAIN_TITLE):
                break
            chat = 0
        win32api.SendMessage(box, win32con.WM_SETTEXT, 0, "")  # 검색어 지우기
        if not chat:
            opened = [t for h, t in self._kakao_windows().items() if h not in before and t != MAIN_TITLE]
            where = "채팅방 이름" if used == "chats" else "카톡 친구 이름"
            hint = f" 대신 열린 창: '{opened[0]}'" if opened else " 새로 열린 창 없음"
            self._log("채팅방 창 못 찾음." + hint)
            raise ChatNotFound(f"'{name}' 채팅방을 찾지 못했습니다({where} 확인 필요,{hint})")
        self._log(f"채팅방 창 열림: '{win32gui.GetWindowText(chat)}'")
        return chat

    def _post_enter(self, hwnd) -> None:
        """Enter 키 메시지를 정식 키 정보(스캔코드 0x1C)와 함께 보낸다."""
        win32api, win32con, _ = self._w()
        win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0x001C0001)
        time.sleep(0.05)
        win32api.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 0xC01C0001)

    def _click(self, hwnd) -> None:
        win32api, win32con, win32gui = self._w()
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        win32api.SetCursorPos(((left + right) // 2, (top + bottom) // 2))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.3)

    def _sent(self, box, wait: float = 1.0) -> bool:
        """입력칸이 비었으면(=전송됨) True."""
        end = time.time() + wait
        while time.time() < end:
            time.sleep(0.2)
            if not self._text_len(box):
                return True
        return False

    def _try_post_enter(self, chat, box, text) -> None:
        self._post_enter(box)

    def _try_click_enter(self, chat, box, text) -> None:
        win32api, win32con, _ = self._w()
        self._bring_to_front(chat)
        self._click(box)
        self._key(win32con.VK_RETURN)

    def _try_paste_enter(self, chat, box, text) -> None:
        import win32clipboard

        win32api, win32con, _ = self._w()
        win32api.SendMessage(box, win32con.WM_SETTEXT, 0, "")
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        self._bring_to_front(chat)
        self._click(box)
        self._key(win32con.VK_CONTROL, ord("V"))
        time.sleep(0.5)
        self._key(win32con.VK_RETURN)

    SEND_METHODS = (
        ("Enter 메시지", "_try_post_enter"),
        ("입력칸 클릭 + Enter", "_try_click_enter"),
        ("붙여넣기 + Enter", "_try_paste_enter"),
    )

    def send_text(self, chat, text: str) -> None:
        win32api, win32con, win32gui = self._w()
        box = self._input_box(chat)
        if not box:
            self._log("입력칸 못 찾음")
            raise RuntimeError("채팅방 입력칸을 찾지 못했습니다")
        self._log(f"입력칸 찾음({win32gui.GetClassName(box)})")
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
        win32api.SendMessage(box, win32con.WM_SETTEXT, 0, text)
        time.sleep(0.5)
        if not self._text_len(box):
            self._log("입력칸에 글이 들어가지 않음")
            raise RuntimeError("채팅방 입력칸에 글을 넣지 못했습니다")

        # 한 번 성공한 방법을 먼저 쓰고, 안 되면 다음 방법. 전송되면(입력칸이 비면) 바로 멈추므로 두 번 가지 않는다.
        methods = list(self.SEND_METHODS)
        if self.preferred:
            methods.sort(key=lambda m: m[0] != self.preferred)
        previous = ""
        for label, attr in methods:
            if not self._text_len(box):  # 앞 방법이 늦게 전송됨 → 다시 넣지 않고 멈춘다(중복 발송 방지)
                self.preferred = previous
                self._log(f"전송됨({previous}, 늦게 처리됨)")
                return
            previous = label
            try:
                getattr(self, attr)(chat, box, text)
            except Exception as exc:
                self._log(f"{label}: 오류 {exc}")
                continue
            if self._sent(box, wait=1.5):
                self.preferred = label
                self._log(f"전송됨({label})")
                return
            self._log(f"{label}: 전송 안 됨")
        raise RuntimeError("Enter 를 눌러도 전송되지 않았습니다(입력칸에 글이 남아 있음)")

    def _bring_to_front(self, hwnd) -> None:
        win32api, win32con, win32gui = self._w()
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

        self._bring_to_front(chat)
        box = win32gui.FindWindowEx(chat, None, CLASS_INPUT, None)
        if box:  # 입력칸을 눌러 커서를 둔다
            left, top, right, bottom = win32gui.GetWindowRect(box)
            win32api.SetCursorPos(((left + right) // 2, (top + bottom) // 2))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.3)
        self._key(win32con.VK_CONTROL, ord("V"))
        time.sleep(2.0)
        self._key(win32con.VK_RETURN)  # '전송' 확인 창
        time.sleep(2.0 + 1.0 * len(paths))  # 업로드 시간

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
        finally:
            win32clipboard.CloseClipboard()

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
    ):
        self.driver = driver
        self.min_interval = min_interval
        self.max_interval = max(max_interval, min_interval)
        self.max_consecutive_failures = max_consecutive_failures
        self.sleep = sleep

    def send(self, messages: list[OutgoingMessage], on_result: ResultCallback | None = None) -> list[SendResult]:
        results: list[SendResult] = []
        failures_in_row = 0
        for i, m in enumerate(messages):
            if failures_in_row >= self.max_consecutive_failures:
                result = SendResult(m.key, m.to, False, f"연속 {failures_in_row}건 실패로 발송 중단(미발송)")
            else:
                result = self._send_one(m)
                failures_in_row = 0 if result.ok else failures_in_row + 1
                if i < len(messages) - 1:
                    self.sleep(random.uniform(self.min_interval, self.max_interval))
            results.append(result)
            if on_result:
                on_result(i + 1, len(messages), result)
        return results

    def _send_one(self, m: OutgoingMessage) -> SendResult:
        name = m.chat_name
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
        note = ""
        if m.attachments:
            try:
                self.driver.send_files(chat, m.attachments)
                note = f" (+첨부 {len(m.attachments)}개)"
            except Exception as exc:
                # 글은 이미 갔으므로 '성공'으로 남겨 중복 발송을 막고, 첨부 실패는 경고로 알린다
                note = f" ⚠️ 첨부 실패: {exc}"
        self._close(chat)
        return SendResult(m.key, m.to, True, f"{where}'{name}'에게 전송{note}")

    def _close(self, chat) -> None:
        try:
            self.driver.close_chat(chat)
        except Exception:
            pass


def check_send(name: str, tab: str = "friends",
               text: str = "[발송 점검] KF로지스틱 발송 프로그램 점검 메시지입니다.") -> list[str]:
    """테스트 받을 곳에 점검 메시지 1건을 보내며 단계별로 무엇이 됐는지 기록해 돌려준다."""
    driver = Win32KakaoDriver(search_tab=tab)
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
