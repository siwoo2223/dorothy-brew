"""라라무브 배송 완료 확인.

2026-10-10 요청 - "라라무브 끝났는지는 링크 들어가면 확인을 할수 있는데, 이거를 도착 완료 해서 도착 완료 건을 보내줄수":
라라무브 공유 링크(share.lalamove.com)를 사람이 보는 것처럼 보이지 않는 Edge 창으로 열어, 화면에
'Drop-off Point / Completed …' 가 보이면 완료로 본다. (라라무브 서버에 직접 묻는 주소는 보안 서명이 있어
쓰지 않는다 - 화면을 읽기만 한다.)

PC 에 이미 있는 Microsoft Edge 를 쓰므로 브라우저를 따로 받지 않는다(playwright 의 channel="msedge").
화면을 못 읽거나 모양이 바뀌면 'unknown' - 그 송장은 건드리지 않는다(잘못된 완료 안내 방지).
"""
from __future__ import annotations

import re
from typing import Callable, Iterable

# 완료 화면: "Drop-off Point\nCompleted Yesterday, 4:02 PM" (+ 오른쪽 "How was … service?" 별점)
# 진행 중 화면: "Drop-off Point\nHeading to 7 Cario Drive" (+ "Rate the driver after delivery is completed.")
_DONE = re.compile(r"Drop-off Point\s+Completed\b")
_ONGOING = re.compile(r"Drop-off Point\s+\S")
_CANCELLED = re.compile(r"Drop-off Point\s+(Cancel|Canceled|Cancelled)", re.I)


def page_state(text: str) -> str:
    """공유 화면 글자에서 상태: completed | cancelled | ongoing | unknown"""
    text = text or ""
    if _DONE.search(text):
        return "completed"
    if _CANCELLED.search(text):
        return "cancelled"
    if _ONGOING.search(text):
        return "ongoing"
    return "unknown"


def read_pages(links: Iterable[str], log: Callable[[str], None] = print, timeout_ms: int = 30000) -> dict[str, str]:
    """링크마다 화면을 열어 상태를 읽는다. 반환: {링크: 상태}. playwright 가 없으면 ImportError."""
    from playwright.sync_api import sync_playwright

    out: dict[str, str] = {}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
        except Exception:  # Edge 를 못 찾으면 크롬으로
            browser = p.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page(locale="en-PH")
            for link in links:
                try:
                    page.goto(link, timeout=timeout_ms, wait_until="domcontentloaded")
                    try:
                        page.get_by_text("Drop-off Point").first.wait_for(timeout=timeout_ms)
                    except Exception:
                        pass
                    page.wait_for_timeout(1500)  # 상태 글자가 채워질 시간
                    out[link] = page_state(page.inner_text("body"))
                except Exception as exc:
                    log(f"  라라무브 링크를 열지 못함: {exc}")
                    out[link] = "unknown"
        finally:
            browser.close()
    return out
