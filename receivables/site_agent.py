"""사무실 PC 발송 도우미: kflogistics 관리자 페이지(카톡 발송)와 PC 카카오톡을 이어 준다.

관리자 페이지에서 만든 발송(미수금 안내·공지·입고 안내·예약)은 사이트의 kakao_messages 에 쌓인다.
이 도우미가 약 20초마다 사이트(backend/api/kakao_agent.php)에 물어 보낼 메시지를 가져와
PC 카카오톡으로 보내고, 결과(완료/실패/다시 대기)를 바로 알려 준다.
관리자 페이지의 '카톡 이름 읽기' 요청도 여기서 처리한다.
"""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from .sender import OutgoingMessage, SendResult

ROOT = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = ROOT / "attachments" / "site"
API_PATH = "/backend/api/kakao_agent.php"
NOT_FOUND_PREFIX = "검색 결과에"  # 아무 방도 안 연 안전한 실패 (연속 실패로 세지 않음)


class SiteError(RuntimeError):
    pass


@dataclass
class SiteMessage:
    id: int
    customer: str
    room: str
    tab: str
    text: str
    attachments: list[dict]
    test: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "SiteMessage":
        return cls(int(d["id"]), str(d.get("customer", "")), str(d.get("room", "")),
                   "friends" if d.get("tab") == "friends" else "chats", str(d.get("text", "")),
                   list(d.get("attachments") or []), bool(d.get("test")))


class SiteAPI:
    def __init__(self, base_url: str, token: str, session: requests.Session | None = None, timeout: float = 60):
        base_url = (base_url or "").strip().rstrip("/")
        if not base_url or not token:
            raise ValueError("사이트 주소와 연결 키가 필요합니다.")
        if not re.match(r"^https?://", base_url):
            base_url = "https://" + base_url
        self.base = base_url
        self.token = token.strip()
        self.session = session or requests.Session()
        self.timeout = timeout

    def _call(self, method: str, **kw) -> requests.Response:
        try:
            r = self.session.request(method, self.base + API_PATH, headers={"X-KF-Token": self.token},
                                     timeout=self.timeout, **kw)
        except requests.RequestException as exc:
            raise SiteError(f"사이트에 연결하지 못했습니다: {exc}") from exc
        if r.status_code == 403:
            raise SiteError("연결 키가 맞지 않습니다. 관리자 > 카톡 발송 > 설정 의 연결 키를 다시 넣어 주세요.")
        if r.status_code == 404 and "NOT_FOUND" not in r.text:
            raise SiteError("사이트에서 카톡 발송 기능을 찾지 못했습니다. 사이트 주소를 확인하세요.")
        if r.status_code >= 400:
            raise SiteError(f"사이트 오류 {r.status_code}: {r.text[:200]}")
        return r

    def _post(self, payload: dict) -> dict:
        r = self._call("POST", json=payload)
        try:
            return r.json()
        except ValueError as exc:
            raise SiteError("사이트 응답을 읽지 못했습니다: " + r.text[:200]) from exc

    def pull(self, info: str = "", busy: bool = False) -> dict:
        # busy=True: 사람이 PC 를 쓰는 중 - 사이트는 메시지를 넘기지 않고 '사용 중'으로만 표시
        return self._post({"action": "pull", "info": info, "busy": busy})

    def stop_requested(self) -> bool:
        try:
            return bool(self._post({"action": "status"}).get("stop"))
        except SiteError:
            return False  # 잠깐 끊겨도 발송은 이어간다

    def report(self, results: list[dict]) -> None:
        if results:
            self._post({"action": "report", "results": results})

    def send_names(self, tab: str, exact: bool, names: list[str], error: str = "") -> None:
        self._post({"action": "names", "tab": tab, "exact": exact, "names": names, "error": error})

    def download(self, att: dict, folder: Path) -> Path:
        name = re.sub(r'[\\/:*?"<>|]', "_", str(att.get("name") or att.get("file") or "file"))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        if "file" in att:  # 관리자 화면에서 올린 첨부: 연결 키로 받는다
            r = self._call("GET", params={"action": "file", "file": att["file"]})
        else:
            url = str(att.get("url", ""))
            if url.startswith("/"):
                url = self.base + url
            try:
                r = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                raise SiteError(f"사진을 받지 못했습니다: {exc}") from exc
            if r.status_code >= 400:
                raise SiteError(f"사진을 받지 못했습니다({r.status_code}): {url}")
        path.write_bytes(r.content)
        return path


class Reporter:
    """결과를 바로 알리고, 인터넷이 잠깐 끊겨 못 알린 것은 모아 두었다가 다음에 다시 보낸다."""

    def __init__(self, api: SiteAPI, log: Callable[[str], None]):
        self.api = api
        self.log = log
        self.backlog: list[dict] = []

    def add(self, msg_id: int, status: str, note: str = "") -> None:
        self.backlog.append({"id": msg_id, "status": status, "note": note})
        self.flush()

    def flush(self) -> None:
        if not self.backlog:
            return
        try:
            self.api.report(self.backlog)
            self.backlog = []
        except SiteError as exc:
            self.log(f"  (결과를 사이트에 알리지 못함 - 다음에 다시 시도: {exc})")


def status_for(r: SendResult) -> str:
    return "sent" if r.ok else "failed"


class Agent:
    """사이트에서 가져온 메시지를 한 건씩 보낸다. sender 는 KakaoPCSender 와 같은 send() 를 가진 것."""

    def __init__(self, api: SiteAPI, make_sender: Callable[[dict], object], log: Callable[[str], None] = print,
                 sleep: Callable[[float], None] = time.sleep, local_stop: Callable[[], bool] = lambda: False,
                 download_dir: Path = DOWNLOAD_DIR, max_failures: int = 3, extract_names=None,
                 idle_seconds: Callable[[], float] = lambda: 1e9, input_tick: Callable[[], int] = lambda: 0,
                 need_idle: float = 60):
        self.api = api
        self.make_sender = make_sender
        self.log = log
        self.sleep = sleep
        self.local_stop = local_stop
        self.download_dir = download_dir
        self.max_failures = max_failures
        self.reporter = Reporter(api, log)
        self.extract_names = extract_names
        self.cooldown_until = 0.0
        # 2026-10-08 요청 - "도우미기능으로 진행을 해보자": 사장님이 쓰는 PC 에서 같이 돌기 때문에
        # 마우스·키보드를 need_idle 초 동안 안 쓸 때만 보내고, 보내는 중에 손을 대면 바로 멈춘다.
        self.idle_seconds = idle_seconds
        self.input_tick = input_tick
        self.need_idle = need_idle  # 사이트 설정(idle_seconds)이 오면 그 값으로 바뀐다. 0 이면 끔
        self.own_tick = 0           # 도우미가 마지막으로 키보드·마우스를 쓴 시각(이보다 뒤 입력 = 사람)
        self.waiting_logged = False

    def user_busy(self) -> bool:
        if self.need_idle <= 0:
            return False
        if self.own_tick and self.input_tick() == self.own_tick:
            return False  # 마지막 입력이 도우미 자신의 것(방금 보낸 묶음) - 사람은 손대지 않았음
        return self.idle_seconds() < self.need_idle

    def user_touched(self) -> bool:
        """도우미가 마지막으로 보낸 뒤 사람이 마우스·키보드를 썼는지"""
        return self.need_idle > 0 and self.input_tick() != self.own_tick

    def _wait(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and not self.local_stop() and not self.user_touched():
            self.sleep(min(0.5, max(end - time.time(), 0)))
            if self.sleep is not time.sleep:  # 테스트용 가짜 대기
                return

    def tick(self, info: str = "") -> int:
        """한 번 확인해서 보낼 것을 보낸다. 반환: 처리한 메시지 수."""
        self.reporter.flush()
        busy = self.user_busy()
        data = self.api.pull(info, busy=busy)
        settings = data.get("settings") or {}
        if str(settings.get("idle_seconds", "")).strip().isdigit():
            self.need_idle = float(settings["idle_seconds"])
            busy = self.user_busy()
        messages = [SiteMessage.from_dict(m) for m in data.get("messages") or []]
        req = data.get("names_request")
        if busy:
            for m in messages:  # 옛 사이트는 busy 를 모르고 넘겨줄 수 있다 - 그대로 대기로 돌려준다
                self.reporter.add(m.id, "pending")
            if (messages or req) and not self.waiting_logged:
                self.log(f"PC 사용 중 - 마우스·키보드를 {int(self.need_idle)}초 동안 안 쓰면 보내기 시작합니다.")
                self.waiting_logged = True
            return 0
        self.waiting_logged = False
        if req and self.extract_names:
            self._names(req)
            self.own_tick = self.input_tick()
        if not messages:
            return 0
        if time.time() < self.cooldown_until:
            for m in messages:
                self.reporter.add(m.id, "pending")
            return 0
        gap_min = float(settings.get("gap_min") or 8)
        gap_max = max(float(settings.get("gap_max") or 15), gap_min)
        sender = self.make_sender(settings)
        self.log(f"보낼 메시지 {len(messages)}건을 가져왔습니다.")
        failures = 0
        self.own_tick = self.input_tick()
        for i, m in enumerate(messages):
            if i > 0 and self.user_touched():
                self.log("🖱 PC 사용 감지 - 멈춥니다. 손을 떼면 남은 메시지를 이어서 보냅니다.")
                for rest in messages[i:]:
                    self.reporter.add(rest.id, "pending")
                break
            if self.local_stop() or self.api.stop_requested():
                self.log("⏹ 발송 중지 - 남은 메시지는 사이트에서 대기로 돌아갑니다.")
                for rest in messages[i:]:
                    self.reporter.add(rest.id, "pending")
                break
            if failures >= self.max_failures:
                self.log(f"❌ 연속 {failures}건 실패 - 카카오톡 창 상태를 확인해 주세요. 5분 뒤 다시 시도합니다.")
                for rest in messages[i:]:
                    self.reporter.add(rest.id, "pending")
                self.cooldown_until = time.time() + 300
                break
            result = self._send(sender, m)
            self.own_tick = self.input_tick()  # 방금 도우미가 누른 키·클릭까지는 사람 입력이 아님
            if result.ok or result.detail.startswith(NOT_FOUND_PREFIX):
                failures = 0 if result.ok else failures
            else:
                failures += 1
            if i < len(messages) - 1:
                self._wait(random.uniform(gap_min, gap_max))
        return len(messages)

    def _send(self, sender, m: SiteMessage) -> SendResult:
        label = f"{'[테스트] ' if m.test else ''}{m.customer} → {m.room}"
        try:
            files = [str(self.api.download(a, self.download_dir / str(m.id))) for a in m.attachments]
        except SiteError as exc:
            self.log(f"  ❌ {label}: 첨부 받기 실패 - {exc}")
            self.reporter.add(m.id, "failed", f"첨부(사진·파일)를 받지 못함: {exc}")
            return SendResult(str(m.id), m.room, False, str(exc))
        out = OutgoingMessage(key=str(m.id), to=m.room, text=m.text, variables={}, chat_name=m.room,
                              search_tab=m.tab, attachments=files)
        try:
            result = sender.send([out])[0]
        except Exception as exc:  # 예상 못 한 오류도 그 건만 실패로 남기고 계속
            result = SendResult(str(m.id), m.room, False, f"발송 오류: {exc}")
        if not result.ok and ("발송 중지됨" in result.detail or "발송 중단" in result.detail):
            self.reporter.add(m.id, "pending")
        else:
            self.reporter.add(m.id, status_for(result), result.detail)
        self.log(f"  {'✅' if result.ok else '❌'} {label}: {result.detail}")
        return result

    def _names(self, req: dict) -> None:
        tab = "friends" if req.get("tab") == "friends" else "chats"
        exact = bool(req.get("exact"))
        self.log(f"카톡 이름 읽기 요청 ({'친구' if tab == 'friends' else '채팅'} 목록, {'정확하게' if exact else '빠르게'}) - "
                 "카톡 창이 자동으로 움직이니 건드리지 마세요.")
        try:
            names = self.extract_names(tab, exact, self.log)
            self.api.send_names(tab, exact, names)
            self.log(f"  이름 {len(names)}개를 사이트에 올렸습니다.")
        except Exception as exc:
            self.log(f"  이름 읽기 실패: {exc}")
            try:
                self.api.send_names(tab, exact, [], error=str(exc))
            except SiteError:
                pass


def windows_extract_names(tab: str, exact: bool, log: Callable[[str], None]) -> list[str]:
    from .kakao_names import extract_names, extract_names_exact

    if exact:
        def progress(count: int, name: str, ok: bool) -> None:
            if count % 20 == 0:
                log(f"  {count}개 읽는 중… {name}")

        return [n for n, _ in extract_names_exact(tab, on_progress=progress)]
    names, _method = extract_names(tab)
    return names


def windows_sender(settings: dict):
    from .kakao_pc import KakaoPCSender, Win32KakaoDriver

    driver = Win32KakaoDriver(search_tab="chats", find_mode=settings.get("find_mode") or "ocr")
    # 메시지 사이 간격은 Agent 가 두므로 여기서는 쉬지 않는다
    return KakaoPCSender(driver, min_interval=0, max_interval=0, max_consecutive_failures=99)
