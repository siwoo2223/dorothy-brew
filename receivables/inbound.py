"""입고 알림: 워드프레스(kflogistics 사이트)에 등록된 입고를 가져와 PC 카카오톡으로 보낸다.

사이트에서 직원이 입고(사진·내용)를 등록하면 '대기'로 쌓이고, 예약 시간(예: 매일 17시)에
이 프로그램이 대기 건을 '발송 중'으로 가져와 고객(카톡 방)별로 묶어 글 + 사진으로 보낸 뒤
결과(완료/실패/다시 대기)를 사이트에 알려 준다.

사이트 쪽은 wordpress/kf-inbound-notify 플러그인. 연결 정보는 .env 의 KF_SITE_URL, KF_SITE_KEY.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

from . import history
from .sender import OutgoingMessage, SendResult
from .template import render

KIND_INBOUND = "입고 안내"
ROOT = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = ROOT / "attachments" / "입고"
UNREPORTED = ROOT / "logs" / "inbound_unreported.json"
NAMESPACE = "/kf-inbound/v1"

DEFAULT_TEMPLATE = (
    "[KF로지스틱 입고 안내]\n#{고객명} 고객님, 안녕하세요.\n아래 화물이 입고되었습니다.\n\n#{입고목록}\n\n"
    "입고 사진 함께 보내드립니다.\n문의사항은 언제든 연락 주세요. 감사합니다."
)
DEFAULT_LINE = "▶ #{입고일} #{내용}"

# 보내지 못한 이유가 '아직 안 보냄'이면 사이트에서 다시 대기로 돌려 다음 발송 때 보낸다
_NOT_SENT = ("발송 중지됨", "발송 중단")


class SiteError(RuntimeError):
    pass


@dataclass
class InboundEntry:
    id: int
    customer: str
    room: str
    tab: str
    received_on: str
    items: str
    photos: list[dict] = field(default_factory=list)  # [{"id":.., "filename":..}]

    @classmethod
    def from_dict(cls, d: dict) -> "InboundEntry":
        c = d.get("customer") or {}
        return cls(
            id=int(d["id"]),
            customer=str(c.get("name", "")),
            room=str(c.get("room", "")),
            tab="friends" if c.get("tab") == "friends" else "chats",
            received_on=str(d.get("received_on", "")),
            items=str(d.get("items", "")),
            photos=list(d.get("photos") or []),
        )


@dataclass
class Group:
    """같은 카톡 방으로 갈 입고 묶음 (메시지 1건)."""

    room: str
    tab: str
    entries: list[InboundEntry]

    @property
    def key(self) -> str:
        return f"입고|{self.tab}|{self.room}"

    @property
    def customer(self) -> str:
        names = list(dict.fromkeys(e.customer for e in self.entries))
        return ", ".join(names)

    @property
    def photo_count(self) -> int:
        return sum(len(e.photos) for e in self.entries)


# ───────────────────────── 사이트 연결 ─────────────────────────
class SiteClient:
    def __init__(self, site_url: str, key: str, session: requests.Session | None = None, timeout: float = 30):
        site_url = (site_url or "").strip()
        if not site_url or not key:
            raise ValueError("사이트 주소와 연결 키를 먼저 넣어 주세요 ([입고 알림] > 사이트 연결).")
        if not re.match(r"^https?://", site_url):
            site_url = "https://" + site_url
        self.base = site_url.rstrip("/")
        self.key = key.strip()
        self.session = session or requests.Session()
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "SiteClient":
        return cls(os.getenv("KF_SITE_URL", ""), os.getenv("KF_SITE_KEY", ""))

    def url(self, route: str) -> str:
        # '?rest_route=' 형식은 워드프레스 고유주소(퍼머링크) 설정과 관계없이 항상 된다
        return f"{self.base}/?rest_route={NAMESPACE}{route}"

    def _call(self, method: str, route: str, **kw) -> requests.Response:
        try:
            r = self.session.request(method, self.url(route), headers={"X-KF-Key": self.key},
                                     timeout=self.timeout, **kw)
        except requests.RequestException as exc:
            raise SiteError(f"사이트에 연결하지 못했습니다: {exc}") from exc
        if r.status_code == 403:
            raise SiteError("연결 키가 맞지 않습니다. 사이트 [입고 알림 > 문구·연결 설정]의 키를 다시 복사해 넣어 주세요.")
        if r.status_code == 404:
            raise SiteError("사이트에서 입고 알림 플러그인을 찾지 못했습니다. 주소가 맞는지, 플러그인이 '활성화'됐는지 확인하세요.")
        if r.status_code >= 400:
            raise SiteError(f"사이트 오류 {r.status_code}: {r.text[:200]}")
        return r

    def _json(self, method: str, route: str, **kw) -> dict:
        r = self._call(method, route, **kw)
        try:
            return r.json()
        except ValueError as exc:
            raise SiteError("사이트 응답을 읽지 못했습니다(보안 플러그인이 막고 있을 수 있습니다): "
                            + r.text[:200]) from exc

    def ping(self) -> dict:
        return self._json("GET", "/ping")

    def pending(self) -> tuple[list[InboundEntry], str, str]:
        """상태를 바꾸지 않고 대기 중인 입고를 본다 (미리보기용)."""
        return self._entries(self._json("GET", "/pending"))

    def claim(self) -> tuple[list[InboundEntry], str, str]:
        """대기 중인 입고를 '발송 중'으로 가져온다. 가져간 건은 다른 곳에서 다시 보내지 않는다."""
        return self._entries(self._json("POST", "/claim"))

    @staticmethod
    def _entries(data: dict) -> tuple[list[InboundEntry], str, str]:
        entries = [InboundEntry.from_dict(d) for d in data.get("entries") or []]
        return entries, data.get("template") or DEFAULT_TEMPLATE, data.get("line") or DEFAULT_LINE

    def report(self, results: list[dict]) -> None:
        """[{"id":1, "status":"sent"|"failed"|"pending", "note":"..."}]"""
        if results:
            self._json("POST", "/report", json={"results": results})

    def download(self, photo: dict, folder: Path) -> Path:
        pid = int(photo["id"])
        name = re.sub(r'[\\/:*?"<>|]', "_", str(photo.get("filename") or f"{pid}.jpg"))
        path = folder / f"{pid}_{name}"
        if not path.exists() or path.stat().st_size == 0:
            r = self._call("GET", f"/photo/{pid}")
            folder.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
        return path


# ───────────────────────── 메시지 만들기 ─────────────────────────
def group_entries(entries: list[InboundEntry]) -> list[Group]:
    groups: dict[tuple[str, str], Group] = {}
    for e in entries:
        k = (e.tab, e.room)
        groups.setdefault(k, Group(e.room, e.tab, [])).entries.append(e)
    return list(groups.values())


def _md(value: str) -> str:
    try:
        d = dt.date.fromisoformat(value)
        return f"{d.month}/{d.day}"
    except ValueError:
        return value


def build_text(group: Group, template: str, line: str, today: dt.date) -> str:
    lines = []
    for e in group.entries:
        values = {"입고일": _md(e.received_on), "내용": e.items.strip(), "사진수": len(e.photos)}
        lines.append(render(line, values).text.rstrip())
    values = {
        "고객명": group.customer,
        "입고목록": "\n".join(lines),
        "건수": len(group.entries),
        "사진수": group.photo_count,
        "날짜": f"{today.month}/{today.day}",
    }
    return render(template, values).text.strip()


# ───────────────────────── 결과 알리기 (실패하면 다음에 다시) ─────────────────────────
def _load_unreported(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_unreported(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    elif path.exists():
        path.unlink()


class Reporter:
    """결과를 한 건씩 바로 사이트에 알리고, 인터넷 문제로 못 알린 것은 파일에 두었다가 다음에 보낸다."""

    def __init__(self, client: SiteClient, log: Callable[[str], None], path: Path = UNREPORTED):
        self.client = client
        self.log = log
        self.path = path
        self.backlog = _load_unreported(path)

    def flush(self) -> None:
        if not self.backlog:
            return
        try:
            self.client.report(self.backlog)
            self.backlog = []
        except SiteError as exc:
            self.log(f"[사이트] 결과 알림 실패(다음에 다시 시도): {exc}")
        _save_unreported(self.backlog, self.path)

    def add(self, rows: list[dict]) -> None:
        self.backlog.extend(rows)
        self.flush()


def _status_for(r: SendResult) -> tuple[str, str]:
    if r.ok:
        return "sent", r.detail
    if any(s in r.detail for s in _NOT_SENT):
        return "pending", ""
    return "failed", r.detail


# ───────────────────────── 실행 ─────────────────────────
def run_job(job, log: Callable[[str], None] = print, sender=None, log_path: Path = history.DEFAULT_LOG,
            today: dt.date | None = None, client: SiteClient | None = None,
            download_dir: Path = DOWNLOAD_DIR, unreported_path: Path = UNREPORTED):
    """사이트의 대기 입고를 가져와 보낸다. job 은 runner.Job (발송 방식·간격·한도·테스트 설정)."""
    from .runner import RunSummary, make_sender

    today = today or dt.date.today()
    try:
        client = client or SiteClient.from_env()
    except ValueError as exc:
        log(f"오류: {exc}")
        return RunSummary(message=str(exc))
    reporter = Reporter(client, log, unreported_path)
    reporter.flush()

    preview = job.mode == "dry-run"
    testing = bool(job.test_to)
    try:
        # 미리보기는 상태를 바꾸지 않고, 실제 발송(테스트 포함)은 '발송 중'으로 가져온다
        entries, template, line = client.pending() if preview else client.claim()
    except SiteError as exc:
        log(f"오류: {exc}")
        return RunSummary(message=str(exc))
    if not entries:
        log("사이트에 보낼 입고가 없습니다.")
        return RunSummary()

    groups = group_entries(entries)
    log(f"사이트에서 입고 {len(entries)}건을 가져왔습니다 → 카톡 방 {len(groups)}곳")

    def release(gs: list[Group], note: str = "") -> None:
        if not preview and gs:
            reporter.add([{"id": e.id, "status": "pending", "note": note} for g in gs for e in g.entries])

    targets = groups
    if testing:
        release(groups)  # 테스트는 실제 고객 발송이 아니므로 모두 그대로 다시 대기로
        targets = groups[: job.test_count]
        log(f"[테스트 모드] {len(targets)}곳 분량을 고객 대신 '{job.test_to}' 에게 보냅니다. "
            "입고 건은 그대로 '대기'로 남아 실제 발송 때 다시 보내집니다.")
    if job.mode == "kakao-pc":
        from .kakao_pc import KakaoPCSender

        remaining = max(job.daily_cap - history.count_sent(today, KakaoPCSender.label, log_path), 0)
        if len(targets) > remaining:
            log(f"[한도] 오늘 남은 발송 수 {remaining}건만 보냅니다. 나머지는 다음 발송 때 보냅니다.")
            if not testing:
                release(targets[remaining:])
            targets = targets[:remaining]

    messages: list[OutgoingMessage] = []
    by_key: dict[str, tuple[Group, str]] = {}
    skipped = 0
    folder = download_dir / today.isoformat()
    for g in targets:
        text = build_text(g, template, line, today)
        try:
            files = [str(client.download(p, folder)) for e in g.entries for p in e.photos]
        except SiteError as exc:
            log(f"[건너뜀] {g.customer}: 사진 내려받기 실패 — {exc}")
            if not testing:
                reporter.add([{"id": e.id, "status": "failed", "note": f"사진 내려받기 실패: {exc}"} for e in g.entries])
            skipped += 1
            continue
        if testing:
            to, tab = job.test_to, job.test_tab
            text = f"[테스트 · 원래 받는 방: {g.room}]\n{text}"
        else:
            to, tab = g.room, g.tab
        messages.append(OutgoingMessage(key=g.key, to=to, text=text, variables={}, chat_name=to,
                                        search_tab=tab, attachments=files))
        by_key[g.key] = (g, text)

    if not messages:
        log("보낼 대상이 없습니다.")
        return RunSummary(skipped=len(groups))

    if preview:
        for m in messages:
            g, text = by_key[m.key]
            log(f"\n===== {g.customer} → {g.room} (사진 {len(m.attachments)}장) =====\n{text}")

    if sender is None:
        from .control import clear_stop

        clear_stop()
        try:
            sender = make_sender(job)
        except (ValueError, RuntimeError) as exc:
            log(f"오류: {exc}")
            if not testing:
                release([by_key[m.key][0] for m in messages])
            return RunSummary(skipped=len(groups), message=str(exc))

    reported: set[str] = set()

    def on_result(done: int, total: int, r: SendResult) -> None:
        g, text = by_key[r.key]
        reported.add(r.key)
        log(f"  [{done}/{total}] {'성공' if r.ok else '실패'} {g.customer}: {r.detail}")
        history.append([{
            "발송시각": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "구분": history.TEST_KIND if testing else KIND_INBOUND,
            "방식": sender.label,
            "고객명": g.customer,
            "카톡이름": r.to,
            "결과": "성공" if r.ok else "실패",
            "상세": r.detail,
            "본문": text,
        }], log_path)
        if not testing and not preview:
            status, note = _status_for(r)
            reporter.add([{"id": e.id, "status": status, "note": note} for e in g.entries])

    try:
        results = sender.send(messages, on_result=on_result)
    except Exception as exc:  # 예상 못 한 오류: 아직 안 보낸 건은 다시 대기로 돌려 다음에 보내게
        log(f"오류: {type(exc).__name__}: {exc}")
        if not testing:
            release([by_key[m.key][0] for m in messages if m.key not in reported])
        return RunSummary(sent=len(reported), skipped=len(groups), message=str(exc))
    summary = RunSummary(
        sent=sum(r.ok for r in results),
        failed=sum(not r.ok for r in results),
        skipped=skipped + len(groups) - len(targets),
        warnings=sum("⚠️" in r.detail for r in results),
    )
    log(f"\n{sender.label}: 성공 {summary.sent}건 / 실패 {summary.failed}건 / 건너뜀 {summary.skipped}건")
    return summary
