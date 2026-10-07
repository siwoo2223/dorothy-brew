"""메시지 발송: 솔라피(SOLAPI) API 연동과 미리보기(모의 발송)."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from typing import Callable

import requests

SOLAPI_URL = "https://api.solapi.com/messages/v4/send-many/detail"
BATCH_SIZE = 500


@dataclass
class OutgoingMessage:
    key: str  # 고객 식별용 (이름|전화번호)
    to: str
    text: str
    variables: dict[str, str]  # 알림톡 변수 (#{...} 없이 이름만)
    chat_name: str = ""  # PC 카카오톡에서 찾을 친구/채팅방 이름
    search_tab: str = ""  # PC 카카오톡: "friends"(친구) | "chats"(채팅방) | ""(기본 설정)
    attachments: list[str] = field(default_factory=list)  # PC 카카오톡: 함께 보낼 사진·파일 경로


@dataclass
class SendResult:
    key: str
    to: str
    ok: bool
    detail: str


ResultCallback = Callable[[int, int, SendResult], None]  # (완료 건수, 전체 건수, 결과)


def _report(results: list[SendResult], done_before: int, total: int, on_result: ResultCallback | None) -> None:
    if on_result:
        for i, r in enumerate(results, start=done_before + 1):
            on_result(i, total, r)


class DryRunSender:
    """실제로 보내지 않고 성공으로 기록만 한다. 처음 연습할 때 쓴다."""

    label = "미리보기(실제 발송 안 함)"

    def send(self, messages: list[OutgoingMessage], on_result: ResultCallback | None = None) -> list[SendResult]:
        results = [SendResult(m.key, m.to, True, "모의 발송") for m in messages]
        _report(results, 0, len(messages), on_result)
        return results


class SolapiSender:
    """솔라피로 알림톡(실패 시 문자 대체) 또는 문자를 보낸다."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        sender_number: str,
        mode: str = "alimtalk",  # "alimtalk" | "sms"
        pf_id: str = "",
        template_id: str = "",
        sms_fallback: bool = True,
        subject: str = "",
        session: requests.Session | None = None,
    ):
        if not (api_key and api_secret and sender_number):
            raise ValueError("솔라피 API Key, Secret, 발신번호를 모두 입력해 주세요.")
        if mode == "alimtalk" and not (pf_id and template_id):
            raise ValueError("알림톡 발송에는 발신프로필 ID(pfId)와 템플릿 ID가 필요합니다.")
        self.api_key = api_key
        self.api_secret = api_secret
        self.sender_number = sender_number
        self.mode = mode
        self.pf_id = pf_id
        self.template_id = template_id
        self.sms_fallback = sms_fallback
        self.subject = subject
        self.session = session or requests.Session()

    @property
    def label(self) -> str:
        return "알림톡" if self.mode == "alimtalk" else "문자"

    def _auth_header(self) -> str:
        date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        salt = secrets.token_hex(16)
        signature = hmac.new(
            self.api_secret.encode(), (date + salt).encode(), hashlib.sha256
        ).hexdigest()
        return f"HMAC-SHA256 apiKey={self.api_key}, date={date}, salt={salt}, signature={signature}"

    def build_payload(self, m: OutgoingMessage) -> dict:
        payload: dict = {"to": m.to, "from": self.sender_number}
        if self.mode == "alimtalk":
            payload["type"] = "ATA"
            payload["kakaoOptions"] = {
                "pfId": self.pf_id,
                "templateId": self.template_id,
                "variables": {f"#{{{k}}}": v for k, v in m.variables.items()},
                "disableSms": not self.sms_fallback,
            }
        else:
            payload["text"] = m.text  # 길이에 따라 SMS/LMS 자동 선택
            if self.subject:
                payload["subject"] = self.subject
        return payload

    def send(self, messages: list[OutgoingMessage], on_result: ResultCallback | None = None) -> list[SendResult]:
        results: list[SendResult] = []
        for start in range(0, len(messages), BATCH_SIZE):
            batch = messages[start : start + BATCH_SIZE]
            batch_results = self._send_batch(batch)
            _report(batch_results, len(results), len(messages), on_result)
            results.extend(batch_results)
        return results

    def _send_batch(self, batch: list[OutgoingMessage]) -> list[SendResult]:
        body = {"messages": [self.build_payload(m) for m in batch]}
        try:
            resp = self.session.post(
                SOLAPI_URL,
                json=body,
                headers={"Authorization": self._auth_header()},
                timeout=30,
            )
        except requests.RequestException as exc:
            return [SendResult(m.key, m.to, False, f"네트워크 오류: {exc}") for m in batch]

        if resp.status_code >= 400:
            try:
                err = resp.json()
                reason = f"{err.get('errorCode', resp.status_code)}: {err.get('errorMessage', resp.text)}"
            except ValueError:
                reason = f"HTTP {resp.status_code}: {resp.text[:200]}"
            return [SendResult(m.key, m.to, False, reason) for m in batch]

        data = resp.json()
        failed: dict[str, str] = {}
        for item in data.get("failedMessageList") or []:
            to = str(item.get("to", ""))
            failed[to] = f"{item.get('statusCode', '')} {item.get('statusMessage', '')}".strip()
        group_id = (data.get("groupInfo") or {}).get("groupId", "")
        return [
            SendResult(m.key, m.to, False, failed[m.to])
            if m.to in failed
            else SendResult(m.key, m.to, True, f"접수 완료 (그룹 {group_id})")
            for m in batch
        ]
