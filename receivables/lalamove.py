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


_WHEN = re.compile(r"Completed\s+(Today|Yesterday|[A-Z][a-z]{2,8}\.?\s+\d{1,2}(?:,?\s+\d{4})?)\s*,?\s*(\d{1,2}:\d{2}\s*[AP]M)?", re.I)
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def completed_date(text: str, today) -> str:
    """'Completed Yesterday, 4:02 PM' / 'Completed Oct 10, 4:02 PM' → 완료 날짜 'YYYY-MM-DD'. 못 읽으면 ''.
    2026-10-12 - 월요일에 넣은 링크가 토요일에 이미 끝난 라라무브 주문이라, 배송 안 한 날짜로 '배송 완료' 안내가 나갔다
    → 언제 완료됐는지 읽어 사이트가 링크 넣은 날보다 전에 끝난 주문이면 배송완료로 바꾸지 않게 한다."""
    import datetime as dt

    m = _WHEN.search(text or "")
    if not m:
        return ""
    day = m.group(1).strip().lower()
    if day == "today":
        d = today
    elif day == "yesterday":
        d = today - dt.timedelta(days=1)
    else:
        parts = re.split(r"[\s,.]+", day)
        mon = _MONTHS.get(parts[0][:3])
        if not mon or len(parts) < 2 or not parts[1].isdigit():
            return ""
        year = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else today.year
        try:
            d = dt.date(year, mon, int(parts[1]))
        except ValueError:
            return ""
        if d > today:  # 연도 없이 12월 날짜를 1월에 읽은 경우
            d = d.replace(year=d.year - 1)
    return d.isoformat()


def read_pages(links: Iterable[str], log: Callable[[str], None] = print, timeout_ms: int = 30000,
               detail: bool = False) -> dict:
    """링크마다 화면을 열어 상태를 읽는다. 반환: {링크: 상태}, detail=True 면 {링크: {"state", "completed_date"}}.
    playwright 가 없으면 ImportError."""
    import datetime as dt
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
                    text = page.inner_text("body")
                    state = page_state(text)
                    out[link] = {"state": state, "completed_date": completed_date(text, dt.date.today()) if state == "completed" else ""} if detail else state
                except Exception as exc:
                    log(f"  라라무브 링크를 열지 못함: {exc}")
                    out[link] = {"state": "unknown", "completed_date": ""} if detail else "unknown"
        finally:
            browser.close()
    return out
