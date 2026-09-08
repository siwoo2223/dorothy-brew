"""Minimal JSON-over-HTTPS client (urllib only) with retries and a seam for tests."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

# a transport takes (url, headers, timeout) and returns (status, body bytes)
Transport = Callable[[str, Dict[str, str], float], Tuple[int, bytes]]

USER_AGENT = "dorothy-brew/0.1 (+https://github.com/siwoo2223/dorothy-brew)"
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} from {url}: {body[:200]}")
        self.status = status
        self.body = body
        self.url = url


def urllib_transport(url: str, headers: Dict[str, str], timeout: float) -> Tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:               # 4xx/5xx still carry a body
        return exc.code, exc.read()


class JsonHttp:
    """GET JSON with exponential backoff. Swap ``transport`` to test offline."""

    def __init__(self, transport: Optional[Transport] = None, timeout: float = 10.0,
                 retries: int = 3, backoff: float = 0.5,
                 sleeper: Callable[[float], None] = time.sleep,
                 headers: Optional[Dict[str, str]] = None):
        self.transport = transport or urllib_transport
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.sleeper = sleeper
        self.headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        self.headers.update(headers or {})

    def get(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        clean = {k: str(v) for k, v in (params or {}).items() if v is not None}
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean)}"
        last: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                status, body = self.transport(url, self.headers, self.timeout)
                text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
                if status == 200:
                    return json.loads(text)
                last = HttpError(status, text, url)
                if status not in RETRY_STATUS:
                    raise last
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
                last = exc
            if attempt < self.retries:
                self.sleeper(self.backoff * (2 ** attempt))
        raise last if last else RuntimeError(f"request failed: {url}")
