"""PC 카카오톡 친구/채팅 목록에서 이름을 읽어 오고, 엑셀 고객명과 짝을 맞춘다.

읽어 오기는 Windows 접근성 기능(UI Automation, 화면낭독기가 쓰는 방식)으로
목록에 보이는 항목 이름을 읽고, 목록을 조금씩 내리면서 끝까지 모은다.
카카오톡 버전에 따라 이름이 안 읽힐 수 있어서, 그때는 이름을 직접 붙여 넣어도 짝맞추기는 그대로 쓸 수 있다.
"""
from __future__ import annotations

import difflib
import re
import sys
import time
from dataclasses import dataclass

MAIN_TITLE = "카카오톡"


def extract_names(tab: str = "friends", max_scrolls: int = 300, wait: float = 0.3) -> list[str]:
    """PC 카카오톡 메인 창의 친구(friends) 또는 채팅(chats) 목록 이름을 위에서부터 모두 읽는다."""
    if sys.platform != "win32":
        raise RuntimeError("카톡 이름 읽어 오기는 Windows 에서만 됩니다.")
    try:
        from pywinauto import Application, mouse
    except ImportError as exc:
        raise RuntimeError("pywinauto 가 필요합니다: pip install pywinauto") from exc

    try:
        app = Application(backend="uia").connect(title=MAIN_TITLE, timeout=5)
    except Exception as exc:
        raise RuntimeError("카카오톡 창을 찾지 못했습니다. PC 카카오톡을 켜고 로그인해 주세요.") from exc
    win = app.window(title=MAIN_TITLE)
    win.restore()
    win.set_focus()

    lists = [c for c in win.descendants(control_type="List") if c.is_visible()]
    if not lists:
        raise RuntimeError(
            "카카오톡 목록을 읽지 못했습니다. 메인 창에서 "
            + ("'친구'" if tab == "friends" else "'채팅'")
            + " 탭을 눌러 둔 뒤 다시 시도해 주세요."
        )
    target = max(lists, key=lambda c: len(c.children()))  # 가장 항목이 많은 목록 = 친구/채팅 목록
    rect = target.rectangle()
    center = ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)

    names: list[str] = []
    seen: set[str] = set()
    still = 0
    for _ in range(max_scrolls):
        added = 0
        for item in target.children():
            name = clean_name(item.window_text())
            if name and name not in seen:
                seen.add(name)
                names.append(name)
                added += 1
        still = still + 1 if added == 0 else 0
        if still >= 3:  # 세 번 내려도 새 이름이 없으면 끝
            break
        mouse.scroll(coords=center, wheel_dist=-5)
        time.sleep(wait)

    if not names:
        raise RuntimeError("목록에서 이름을 하나도 읽지 못했습니다. 이 카카오톡 버전에서는 직접 붙여 넣기를 써 주세요.")
    return names


def clean_name(text: str) -> str:
    """목록 항목 글자에서 이름만 남긴다(상태메시지·안 읽은 수 등이 붙어 오는 경우 대비)."""
    text = (text or "").strip()
    if not text:
        return ""
    return text.splitlines()[0].strip()


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
