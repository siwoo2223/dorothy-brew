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
import unicodedata
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
        if on:  # 트레이로 숨겨진 창도 꺼내 보이게
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST if on else win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        time.sleep(0.3)
    except Exception:
        pass


class NameVoter:
    """여러 번 읽은 이름을 모아 같은 항목끼리 묶고, 가장 많이 나온 표기를 고른다.

    글자 인식(OCR)은 같은 이름도 읽을 때마다 조금씩 다르게 읽을 수 있어서(기나글로벌/기나글로별),
    비슷한 표기(fuzzy=True)를 한 항목으로 보고 다수결로 정한다.
    """

    def __init__(self, fuzzy: bool = False, similarity: float = 0.8):
        self.fuzzy = fuzzy
        self.similarity = similarity
        self.clusters: list[dict[str, int]] = []  # 항목마다 {표기: 횟수}

    def _find(self, name: str) -> dict[str, int] | None:
        for cluster in self.clusters:
            if name in cluster:
                return cluster
        if not self.fuzzy:
            return None
        key = _norm(name) or name
        for cluster in self.clusters:
            for variant in cluster:
                other = _norm(variant) or variant
                if difflib.SequenceMatcher(None, key, other).ratio() >= self.similarity:
                    return cluster
        return None

    def add(self, names: list[str]) -> int:
        """이름들을 더하고, 새로 생긴 항목 수를 돌려준다."""
        new = 0
        for name in dict.fromkeys(n for n in names if n):  # 한 화면 안의 중복은 한 번만
            cluster = self._find(name)
            if cluster is None:
                self.clusters.append({name: 1})
                new += 1
            else:
                cluster[name] = cluster.get(name, 0) + 1
        return new

    def result(self, min_count: int = 1) -> list[str]:
        out = []
        for cluster in self.clusters:
            if sum(cluster.values()) >= min_count:
                out.append(max(cluster.items(), key=lambda kv: kv[1])[0])  # 동률이면 먼저 나온 표기
        return out


def _collect(read_visible, hwnd: int, wait: float, fuzzy: bool = False, max_scrolls: int = 400) -> list[str]:
    """맨 위로 올린 뒤 '보이는 이름 읽기 → 조금 아래로' 를 목록 끝(화면이 더 안 바뀜)까지 반복."""
    for _ in range(30):
        _scroll(hwnd, 10)
    time.sleep(wait)
    voter = NameVoter(fuzzy=fuzzy)
    previous = None
    same = reads = 0
    for step in range(max_scrolls):
        visible = read_visible()
        reads += 1
        voter.add(visible)
        if step == 0:  # 맨 위 항목도 두 번 이상 읽히도록
            voter.add(read_visible())
            reads += 1
        same = same + 1 if visible == previous else 0
        if same >= 2:  # 스크롤해도 화면이 그대로 = 목록 끝
            break
        previous = visible
        _scroll(hwnd, -2)
        time.sleep(wait)
    # OCR 은 한 번만 보인 표기(잘린 줄·잘못 읽은 글자)를 버린다
    return voter.result(min_count=2 if fuzzy and reads >= 3 else 1)


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
        text = re.sub(r"^[0OoＯ○◎@]\s+", "", text)  # 오픈채팅 아이콘을 '0' 으로 읽은 것
        if not text or len(text) > 30:  # 너무 긴 줄은 대화 미리보기·상태메시지일 가능성이 큼
            continue
        if any(re.search(p, text) for p in NOISE_PATTERNS):
            continue
        out.append(text)
    return out


OCR_INSTALL_CMD = 'Add-WindowsCapability -Online -Name "Language.OCR~~~ko-KR~0.0.1.0"'


def ocr_languages() -> list[str]:
    """이 PC 에서 쓸 수 있는 Windows 글자 인식 언어 목록 (예: ['en-US', 'ko'])."""
    from winrt.windows.media.ocr import OcrEngine

    return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]


def _korean_ocr_engine():
    """한국어 OCR 엔진. 언어 태그 표기가 PC 마다 달라서 여러 방법으로 찾는다."""
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine

    for tag in ["ko-KR", "ko"] + [t for t in ocr_languages() if t.lower().startswith("ko")]:
        try:
            lang = Language(tag)
            if OcrEngine.is_language_supported(lang):
                engine = OcrEngine.try_create_from_language(lang)
                if engine:
                    return engine
        except Exception:
            continue
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine and engine.recognizer_language.language_tag.lower().startswith("ko"):
        return engine
    raise RuntimeError(
        "Windows 한국어 글자 인식 기능이 없습니다 (설치된 언어: " + (", ".join(ocr_languages()) or "없음") + "). "
        "화면의 '한국어 글자 인식 설치' 버튼을 눌러 설치해 주세요."
    )


def install_korean_ocr() -> None:
    """관리자 권한 PowerShell 로 한국어 OCR 을 설치한다 (Windows 가 권한 허용 창을 띄운다)."""
    _require_windows()
    import ctypes

    args = f"-NoProfile -Command \"{OCR_INSTALL_CMD.replace(chr(34), chr(92) + chr(34))}; pause\""
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", args, None, 1)
    if rc <= 32:
        raise RuntimeError("설치 창을 열지 못했습니다(권한 허용을 거절했을 수 있습니다).")


@dataclass
class OcrLine:
    text: str
    x: float
    y: float
    w: float
    h: float


def item_names_from_lines(lines: list[OcrLine], width: float) -> list[str]:
    """목록 화면의 OCR 줄들에서 항목마다 이름만 골라낸다."""
    return [ln.text for ln in item_title_lines(lines, width)]


def item_title_lines(lines: list[OcrLine], width: float) -> list[OcrLine]:
    """목록 화면의 OCR 줄들에서 항목마다 맨 윗줄(이름)만 골라낸다.

    카톡 목록 항목은 [굵은 이름] 아래에 [상태메시지/대화 미리보기]가 붙어 있다.
    같은 항목 안의 줄 간격은 좁고 항목 사이 간격은 넓으므로, 간격으로 항목을 나눈 뒤 첫 줄만 쓴다.
    오른쪽의 시간·안 읽은 수처럼 이름 칸 밖에 있는 줄은 뺀다.
    """
    if not lines:
        return []
    # 이름 칸의 왼쪽 위치 = 가장 많은 줄이 시작하는 x (5px 단위로 묶어서)
    buckets: dict[int, int] = {}
    for ln in lines:
        buckets[round(ln.x / 5)] = buckets.get(round(ln.x / 5), 0) + 1
    column_x = max(buckets.items(), key=lambda kv: (kv[1], -kv[0]))[0] * 5
    column = sorted((ln for ln in lines if abs(ln.x - column_x) <= width * 0.06), key=lambda ln: ln.y)
    if not column:
        return []
    heights = sorted(ln.h for ln in column)
    line_h = heights[len(heights) // 2]

    titles = []
    prev_bottom = None
    for ln in column:
        if prev_bottom is None or ln.y - prev_bottom > line_h * 0.9:  # 간격이 넓으면 새 항목
            cleaned = filter_ocr_lines([ln.text])
            if cleaned:
                titles.append(OcrLine(cleaned[0], ln.x, ln.y, ln.w, ln.h))
        prev_bottom = ln.y + ln.h
    return titles


def _dpi_aware() -> None:
    """화면 배율(125%·150%)에서도 창 위치와 캡처 위치가 맞도록."""
    import ctypes

    try:
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        pass


def _ocr_screen(hwnd: int):
    """목록 창을 캡처해 OCR 하고, 항목 이름 줄을 '화면 좌표'로 돌려주는 함수를 만든다."""
    import asyncio

    import win32gui
    from PIL import Image, ImageGrab, ImageOps

    try:
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter
    except ImportError as exc:
        raise RuntimeError("OCR 모듈이 없습니다. 실행.bat 을 다시 실행해 설치해 주세요.") from exc

    engine = _korean_ocr_engine()
    _dpi_aware()
    scale = 3

    async def recognize(img):
        writer = DataWriter()
        writer.write_bytes(img.tobytes())
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.RGBA8, img.width, img.height
        )
        return await engine.recognize_async(bitmap)

    def read_titles() -> list[OcrLine]:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
        # 크게·흑백·대비를 올리면 작은 한글 인식이 좋아진다
        img = ImageOps.autocontrast(ImageOps.grayscale(img))
        img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS).convert("RGBA")
        result = asyncio.run(recognize(img))
        lines = []
        for line in result.lines:
            rects = [w.bounding_rect for w in line.words]
            if not rects:
                continue
            x0 = min(r.x for r in rects)
            y0 = min(r.y for r in rects)
            x1 = max(r.x + r.width for r in rects)
            y1 = max(r.y + r.height for r in rects)
            lines.append(OcrLine(line.text, x0, y0, x1 - x0, y1 - y0))
        return [
            OcrLine(t.text, left + t.x / scale, top + t.y / scale, t.w / scale, t.h / scale)
            for t in item_title_lines(lines, img.width)
        ]

    return read_titles


def _ocr_reader(hwnd: int):
    read_titles = _ocr_screen(hwnd)
    return lambda: [t.text for t in read_titles()]


# ───────────────────────── 정확하게 읽기: 채팅방을 열어 창 제목 읽기 ─────────────────────────
def _process_windows(pid: int) -> set[int]:
    import win32gui
    import win32process

    found: set[int] = set()

    def visit(h, _):
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h):
            if win32process.GetWindowThreadProcessId(h)[1] == pid:
                found.add(h)
        return True

    win32gui.EnumWindows(visit, None)
    return found


def _open_and_read_title(x: int, y: int, main: int, pid: int) -> str:
    """목록의 (x, y) 항목을 더블클릭해 채팅방을 열고, 새 창의 제목(정확한 이름)을 읽은 뒤 닫는다."""
    import win32api
    import win32con
    import win32gui

    before = _process_windows(pid)
    win32api.SetCursorPos((x, y))
    for _ in range(2):
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.05)
    new: list[int] = []
    for _ in range(15):
        time.sleep(0.2)
        new = [h for h in _process_windows(pid) - before if h != main]
        if new:
            break
    if not new:
        return ""
    title = win32gui.GetWindowText(new[0]).strip()
    for h in new:
        win32api.PostMessage(h, win32con.WM_CLOSE, 0, 0)
    time.sleep(0.3)
    return title if title != MAIN_TITLE else ""


def _seen_before(text: str, done: list[str], similarity: float = 0.9) -> bool:
    """이미 연 항목인지. 괄호 안 글자까지 비교해서, 비슷한 '다른 방'을 건너뛰지 않게 보수적으로 본다."""
    def loose(t: str) -> str:
        return re.sub(r"[\s\W_]+", "", t).lower() or t

    key = loose(text)
    return any(difflib.SequenceMatcher(None, key, loose(d)).ratio() >= similarity for d in done)


def _press(*keys) -> None:
    """키를 차례로 누르고 거꾸로 뗀다 (지금 맨 앞 창에 입력됨)."""
    import win32api
    import win32con

    for k in keys:
        win32api.keybd_event(k, 0, 0, 0)
        time.sleep(0.03)
    for k in reversed(keys):
        win32api.keybd_event(k, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)


def _post_key(hwnd: int, vk: int) -> None:
    """창(목록)에 키 입력 메시지를 직접 보낸다. 다른 창이 앞에 있어도 된다."""
    import win32api
    import win32con

    scan = win32api.MapVirtualKey(vk, 0)
    win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 1 | (scan << 16))
    time.sleep(0.03)
    win32api.PostMessage(hwnd, win32con.WM_KEYUP, vk, 1 | (scan << 16) | (0xC0 << 24))


def _focus(hwnd: int) -> None:
    import win32api
    import win32con
    import win32gui

    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)  # 다른 프로그램 창을 앞으로 가져오기 위한 Alt 요령
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    finally:
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.3)


def _wait_new_window(before: set[int], main: int, pid: int, timeout: float = 3.0) -> list[int]:
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.15)
        new = [h for h in _process_windows(pid) - before if h != main]
        if new:
            time.sleep(0.2)  # 창 제목이 채워질 때까지
            return new
    return []


def extract_names_by_keyboard(tab: str = "chats", wait: float = 0.4, on_progress=None,
                              max_items: int = 1500) -> list[str]:
    """나인톡처럼: 목록 첫 항목을 선택 → Enter 로 채팅방 열기 → 창 제목(정확한 이름) 읽고 닫기 → ↓ 반복.

    글자 인식(OCR)을 쓰지 않으므로 이름이 정확하다. 채팅방을 열기 때문에 안 읽은 메시지는 '읽음' 처리된다.
    """
    _require_windows()
    import win32api
    import win32con
    import win32gui
    import win32process

    _dpi_aware()
    main = _main_window()
    pid = win32process.GetWindowThreadProcessId(main)[1]
    _keep_on_top(main, True)
    try:
        hwnd = _list_window(main)
        for _ in range(30):  # 목록 맨 위로
            _scroll(hwnd, 10)
        time.sleep(wait)
        _focus(main)
        left, top, right, _bottom = win32gui.GetWindowRect(hwnd)
        win32api.SetCursorPos(((left + right) // 2, top + 30))  # 첫 항목을 한 번 눌러 선택
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(wait)

        names: list[str] = []
        last_title, repeats, misses = None, 0, 0
        mode = None  # "post"(목록 창에 직접 키 보내기) 또는 "keys"(실제 키 누르기). 처음에 되는 쪽으로 정함

        def send_key(vk: int, how: str) -> None:
            if how == "post":
                _post_key(hwnd, vk)
            else:
                _focus(main)
                _press(vk)

        for _ in range(max_items):
            before = _process_windows(pid)
            new: list[int] = []
            for how in ([mode] if mode else ["post", "keys"]):
                send_key(win32con.VK_RETURN, how)
                new = _wait_new_window(before, main, pid, timeout=2.0 if mode else 1.5)
                if new:
                    mode = how
                    break
            if new:
                misses = 0
                title = win32gui.GetWindowText(new[0]).strip()
                for h in new:
                    win32api.PostMessage(h, win32con.WM_CLOSE, 0, 0)
                time.sleep(0.3)
                if title == last_title:  # ↓ 를 눌러도 같은 방 = 목록 끝
                    repeats += 1
                    if repeats >= 2:
                        break
                else:
                    repeats = 0
                    last_title = title
                    if title and title != MAIN_TITLE and title not in names:
                        names.append(title)
                        if on_progress:
                            on_progress(len(names), title, True)
            else:  # 창이 안 열리는 항목(폴더·광고 등): 건너뜀
                misses += 1
                if misses >= 5:
                    break
            send_key(win32con.VK_DOWN, mode or "keys")
            time.sleep(0.15)
        return names
    finally:
        _keep_on_top(main, False)


def extract_names_exact(tab: str = "chats", wait: float = 0.4, on_progress=None) -> list[tuple[str, bool]]:
    """정확한 이름 읽기. 먼저 키보드 방식(나인톡 방식)을 쓰고, 안 되면 글자 인식으로 위치를 찾아 여는 방식을 쓴다."""
    try:
        names = extract_names_by_keyboard(tab, wait, on_progress)
    except RuntimeError:
        raise
    except Exception:
        names = []
    if names:
        return [(n, True) for n in names]
    return _extract_exact_by_ocr(tab, wait, on_progress)


def _extract_exact_by_ocr(tab: str = "chats", wait: float = 0.4, on_progress=None) -> list[tuple[str, bool]]:
    """채팅방(또는 친구)을 하나씩 열어 창 제목으로 정확한 이름을 읽는다.

    돌려주는 값: [(이름, 정확히 확인했는지)] — 창이 안 열려 확인 못 한 항목은 글자 인식 결과를 False 로 담는다.
    채팅방을 열기 때문에 안 읽은 메시지는 '읽음' 처리된다.
    """
    _require_windows()
    import win32process

    main = _main_window()
    pid = win32process.GetWindowThreadProcessId(main)[1]
    _keep_on_top(main, True)
    try:
        hwnd = _list_window(main)
        read_titles = _ocr_screen(hwnd)
        for _ in range(30):
            _scroll(hwnd, 10)
        time.sleep(wait)
        results: list[tuple[str, bool]] = []
        done_ocr: list[str] = []
        previous, same = None, 0
        for _ in range(400):
            titles = read_titles()
            for t in titles:
                if _seen_before(t.text, done_ocr):
                    continue
                done_ocr.append(t.text)
                title = _open_and_read_title(int(t.x + min(t.w, 40) / 2), int(t.y + t.h / 2), main, pid)
                name, exact = (title, True) if title else (t.text, False)
                if all(name != n for n, _ in results):
                    results.append((name, exact))
                if on_progress:
                    on_progress(len(results), name, exact)
            signature = tuple(t.text for t in titles)
            same = same + 1 if signature == previous else 0
            if same >= 2:
                break
            previous = signature
            _scroll(hwnd, -2)
            time.sleep(wait)
        return results
    finally:
        _keep_on_top(main, False)


# ───────────────────────── 공개 함수 ─────────────────────────
def extract_names(tab: str = "friends", wait: float = 0.4) -> tuple[list[str], str]:
    """지금 카카오톡 메인 창에 보이는 목록(친구 또는 채팅)의 이름을 모두 읽는다.

    돌려주는 값: (이름 목록, 사용한 방법)
    """
    _require_windows()
    main = _main_window()
    _keep_on_top(main, True)  # 숨겨진 창을 꺼내고, 글자 인식 중 가려지지 않게 맨 앞에 고정
    try:
        hwnd = _list_window(main)
    except Exception:
        _keep_on_top(main, False)
        raise
    errors = []
    try:
        for label, make in (("MSAA", _msaa_reader), ("UIA", _uia_reader), ("OCR(글자 인식)", _ocr_reader)):
            try:
                reader = make(hwnd)
                first = reader()
                if not first:
                    errors.append(f"{label}: 이름 없음")
                    continue
                return _collect(reader, hwnd, wait, fuzzy=label.startswith("OCR")), label
            except Exception as exc:  # 방법마다 실패 이유를 모아 둔다
                errors.append(f"{label}: {exc}")
    finally:
        _keep_on_top(main, False)
    raise RuntimeError("목록에서 이름을 읽지 못했습니다. (" + " / ".join(errors) + ") "
                       "아래 '진단 정보'를 복사해서 보내 주세요.")


def diagnose(open_test: bool = False) -> str:
    """카카오톡 창 구조를 글로 정리한다 (개발자에게 보내 문제를 고치는 데 쓴다).

    open_test: 목록 첫 방을 Enter 로 열어 보는 시험까지 한다 (그 방은 '읽음' 처리됨).
    """
    _require_windows()
    import win32api
    import win32con
    import win32gui
    import win32process

    lines = []
    main = _main_window()
    _keep_on_top(main, True)
    try:
        lines.append(f"OCR 언어: {ocr_languages()}")
    except Exception as exc:
        lines.append(f"OCR 언어 확인 오류: {exc}")
    lines.append(f"main hwnd={main} class={win32gui.GetClassName(main)} rect={win32gui.GetWindowRect(main)}")
    for h in _child_windows(main):
        lines.append(
            f"  hwnd={h} parent={win32gui.GetParent(h)} class={win32gui.GetClassName(h)!r} "
            f"text={win32gui.GetWindowText(h)!r} visible={win32gui.IsWindowVisible(h)} rect={win32gui.GetWindowRect(h)}"
        )
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
        if open_test:
            pid = win32process.GetWindowThreadProcessId(main)[1]
            for how in ("post", "keys"):
                before = _process_windows(pid)
                if how == "post":
                    _post_key(hwnd, win32con.VK_HOME)
                    _post_key(hwnd, win32con.VK_RETURN)
                else:
                    left, top, right, _ = win32gui.GetWindowRect(hwnd)
                    _focus(main)
                    win32api.SetCursorPos(((left + right) // 2, top + 30))
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                    time.sleep(0.3)
                    _press(win32con.VK_RETURN)
                new = _wait_new_window(before, main, pid, timeout=2.0)
                titles = [(win32gui.GetClassName(h), win32gui.GetWindowText(h)) for h in new]
                lines.append(f"열기 시험({how}): {titles or '창이 열리지 않음'}")
                for h in new:
                    win32api.PostMessage(h, win32con.WM_CLOSE, 0, 0)
                if new:
                    break
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


# ───────────────────────── 검색 결과에서 정확한 방 고르기 ─────────────────────────
def _key(text: str) -> str:
    # 한글을 '완성형'으로 통일(엑셀에서 '김'이 'ㄱ+ㅣ+ㅁ'로 나뉘어 저장된 경우 대비) 후 띄어쓰기·기호 제거
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFC", text or "")).lower()


def _pairs(target: str, candidate: str) -> list[tuple[str, str]]:
    """비교할 (찾는 이름, 결과 이름) 쌍. 찾는 이름이 자음(ㄱ 등)으로 시작하면, 글자 인식이 그 자음을
    '그'·'7' 처럼 다른 글자로 읽으므로 양쪽 첫 글자를 뺀 쌍도 비교한다."""
    pairs = [(target, candidate)]
    if target and "ㄱ" <= target[0] <= "ㅎ" and len(target) > 2 and candidate:
        pairs.append((target[1:], candidate[1:]))
    return pairs


def pick_search_result(name: str, titles: list[str], similarity: float = 0.85) -> int | None:
    """카톡 검색 결과 이름들 중 찾는 이름과 같은 항목의 순서(0부터). 없으면 None.

    카톡 검색은 참여자·대화 내용이 맞는 다른 단톡방도 위에 보여 주므로 '맨 위'가 아니라 '이름이 같은 것'을 고른다.
    띄어쓰기·기호는 무시하고, 글자 인식 오차를 감안해 아주 비슷한(기본 85%) 이름까지 인정한다.
    """
    target = _key(name)
    if not target:
        return None
    keys = [_key(t) for t in titles]
    for i, k in enumerate(keys):
        if any(a == b for a, b in _pairs(target, k)):
            return i
    best, best_score = None, 0.0
    for i, k in enumerate(keys):
        score = max(difflib.SequenceMatcher(None, a, b).ratio() for a, b in _pairs(target, k))
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= similarity else None


def rank_search_results(name: str, titles: list[str], minimum: float = 0.4, limit: int = 3) -> list[int]:
    """검색 결과를 '찾는 이름과 비슷한 순서'로 고른다(최대 limit 개). 이름이 정확히 같은 줄이 있으면 그것 하나만.

    글자 인식은 후보를 고르는 데만 쓰고, 맞는 방인지는 열린 채팅방 창 제목(정확한 글자)으로 최종 확인한다.
    """
    exact = pick_search_result(name, titles, similarity=1.01)
    if exact is not None:
        return [exact]
    target = _key(name)
    if not target or len(target) <= 2:  # 1~2글자 이름은 비슷한 이름(건 ↔ 건우)이 많아 정확히 같은 줄만 연다
        return []
    scored = []
    for i, t in enumerate(titles):
        k = _key(t)
        score = max(difflib.SequenceMatcher(None, a, b).ratio() for a, b in _pairs(target, k)) if k else 0.0
        if score >= minimum:
            scored.append((score, i))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [i for _, i in scored[:limit]]
