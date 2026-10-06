"""고객 목록 + 템플릿 → 고객별 발송 메시지 준비, 발송, 이력 기록."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

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

    @property
    def sendable(self) -> bool:
        return not self.problems


def prepare(
    customers: list[Customer],
    template: str,
    already_sent: set[str] | None = None,
) -> list[Draft]:
    already_sent = already_sent or set()
    drafts = []
    for c in customers:
        result = render(template, c.variables)
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
                already_sent_today=c.phone in already_sent,
            )
        )
    return drafts


def send(
    drafts: list[Draft],
    sender,
    log_path: Path = history.DEFAULT_LOG,
) -> list[SendResult]:
    targets = [d for d in drafts if d.sendable]
    messages = [
        OutgoingMessage(key=d.customer.key, to=d.customer.phone, text=d.text, variables=d.variables)
        for d in targets
    ]
    results = sender.send(messages) if messages else []
    by_key = {d.customer.key: d for d in targets}
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    history.append(
        [
            {
                "발송시각": now,
                "방식": sender.label,
                "고객명": by_key[r.key].customer.name,
                "전화번호": r.to,
                "미수총액": format_value(by_key[r.key].customer.variables.get("미수총액")),
                "결과": "성공" if r.ok else "실패",
                "상세": r.detail,
                "본문": by_key[r.key].text,
            }
            for r in results
        ],
        log_path,
    )
    return results
