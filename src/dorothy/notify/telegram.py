"""텔레그램 알림. 설정이 없으면 조용히 로그로만 남긴다.

알림이 실패해도 매매는 계속되어야 하므로 모든 예외를 삼킨다.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, token: str = "", chat_id: str = "", *, enabled: bool = True) -> None:
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled and bool(token and chat_id)
        if enabled and not self.enabled:
            log.info("텔레그램 미설정 — 알림은 로그로만 출력합니다.")

    def send(self, text: str) -> None:
        log.info("[알림] %s", text)
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = json.dumps({"chat_id": self.chat_id, "text": text}).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            log.warning("텔레그램 전송 실패(무시): %s", exc)
