"""고객 목록 + 템플릿 → 고객별 발송 메시지 준비, 발송, 이력 기록."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import history
from .loader import Customer
from .sender import OutgoingMessage, SendResult
from .template import ALIMTALK_MAX_LENGTH, find_variables, format_value, render


@dataclass
class Draft:
    customer: Customer
    text: str
    variables: dict[str, str]
    problems: list[str] = field(default_factory=list)
    already_sent_today: bool = False
    chat_name: str = ""
    search_tab: str = ""  # "chats" 면 채팅방 이름으로 찾는다

    @property
    def destination(self) -> str:
        """PC 카카오톡에서 어디로 보내는지 사람이 읽기 좋게."""
        return f"💬 {self.chat_name}" if self.search_tab == "chats" else f"👤 {self.chat_name}"

    @property
    def sendable(self) -> bool:
        return not self.problems


def prepare(
    customers: list[Customer],
    template: str,
    already_sent: set[str] | None = None,
    chat_name_col: str | None = None,
    room_col: str | None = None,
) -> list[Draft]:
    """already_sent: 오늘 이미 보낸 고객 key(이름|전화번호) 목록.
    chat_name_col: PC 카카오톡에서 찾을 친구 이름이 담긴 열. 비어 있으면 고객명을 쓴다.
    room_col: 채팅방 이름이 담긴 열. 값이 있는 고객은 친구 대신 그 채팅방(단톡방 포함)으로 보낸다."""
    already_sent = already_sent or set()
    drafts = []
    for c in customers:
        result = render(template, c.variables)
        room = format_value(c.variables.get(room_col)) if room_col else ""
        friend = (format_value(c.variables.get(chat_name_col)) if chat_name_col else "") or c.name
        problems = list(c.problems)
        if result.missing:
            problems.append("값이 없는 변수: " + ", ".join(result.missing))
        if len(result.text) > ALIMTALK_MAX_LENGTH:
            problems.append(f"본문이 {len(result.text)}자로 너무 깁니다(최대 {ALIMTALK_MAX_LENGTH}자)")
        drafts.append(
            Draft(
                customer=c,
                text=result.text,
                # 템플릿에 쓰인 변수만 알림톡 variables 로 넘긴다.
                variables={
                    name: format_value(c.variables.get(name)) for name in find_variables(template)
                },
                problems=problems,
                already_sent_today=c.key in already_sent,
                chat_name=room or friend,
                search_tab="chats" if room else "",
            )
        )
    return drafts


def send(
    drafts: list[Draft],
    sender,
    log_path: Path = history.DEFAULT_LOG,
    on_result: Callable[[int, int, SendResult], None] | None = None,
    kind: str = "미수금 안내",
    test_to: str | None = None,
    test_tab: str = "",
) -> list[SendResult]:
    """발송하고 결과가 나올 때마다 바로 이력에 남긴다(중간에 멈춰도 보낸 건은 기록됨).

    kind: 이력에 남길 구분(미수금 안내, 공지사항 등). 같은 날 같은 구분 중복 발송 판단에 쓴다.
    test_to: 테스트 모드. 고객 대신 이 대상(카톡 이름 또는 휴대폰 번호)에게 모든 메시지를 보낸다.
    test_tab: 테스트 대상이 채팅방 이름이면 "chats".
    """
    targets = [d for d in drafts if d.sendable]
    if test_to:
        messages = [
            OutgoingMessage(
                key=d.customer.key,
                to=test_to,
                text=f"[테스트 · 원래 받는 사람: {d.customer.name}]\n{d.text}",
                variables=d.variables,
                chat_name=test_to,
            )
            for d in targets
        ]
        for m in messages:
            m.search_tab = test_tab
        kind = history.TEST_KIND
    else:
        messages = [
            OutgoingMessage(
                key=d.customer.key, to=d.customer.phone, text=d.text, variables=d.variables,
                chat_name=d.chat_name, search_tab=d.search_tab,
            )
            for d in targets
        ]
    by_key = {d.customer.key: d for d in targets}

    def record(done: int, total: int, r: SendResult) -> None:
        d = by_key[r.key]
        history.append(
            [
                {
                    "발송시각": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "구분": kind,
                    "방식": sender.label,
                    "고객명": d.customer.name,
                    "전화번호": d.customer.phone,
                    "카톡이름": test_to or d.destination,
                    "미수총액": format_value(d.customer.variables.get("미수총액")),
                    "결과": "성공" if r.ok else "실패",
                    "상세": r.detail,
                    "본문": d.text,
                }
            ],
            log_path,
        )
        if on_result:
            on_result(done, total, r)

    return sender.send(messages, on_result=record) if messages else []
