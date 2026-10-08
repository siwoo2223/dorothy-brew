"""입고 알림(사이트 연동) 테스트: 워드프레스 플러그인 대신 같은 규칙으로 동작하는 가짜 사이트를 쓴다."""
import datetime as dt
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from receivables import envfile, inbound
from receivables.kakao_pc import ChatNotFound, KakaoPCSender
from receivables.runner import KIND_INBOUND, Job, run_job

TODAY = dt.date(2026, 10, 8)


class Resp:
    def __init__(self, status, data=None, content=b""):
        self.status_code = status
        self._data = data
        self.content = content
        self.text = json.dumps(data, ensure_ascii=False) if data is not None else ""

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


class FakeSite:
    """kf-inbound-notify 플러그인의 /ping /pending /claim /report /photo 를 흉내 낸다."""

    KEY = "secret"

    def __init__(self):
        self.customers = {
            1: {"name": "세부마트", "room": "KF - 세부마트 [CEBU]", "tab": "chats"},
            2: {"name": "마닐라푸드", "room": "KF-마닐라푸드", "tab": "chats"},
            3: {"name": "방없음", "room": "", "tab": "chats"},
        }
        self.rows = {}
        self.calls = []
        self.fail_report = False

    def add(self, cid, items, photos=(), day="2026-10-08"):
        i = len(self.rows) + 1
        self.rows[i] = {"customer_id": cid, "items": items, "photos": list(photos), "status": "pending",
                        "note": "", "received_on": day}
        return i

    def _payload(self, i):
        r, c = self.rows[i], self.customers[self.rows[i]["customer_id"]]
        return {"id": i, "customer": {"id": r["customer_id"], **c}, "received_on": r["received_on"],
                "items": r["items"], "photos": [{"id": p, "filename": f"p{p}.jpg"} for p in r["photos"]]}

    def _list(self, status):
        return {"template": "#{고객명} 입고 #{건수}건\n#{입고목록}", "line": "- #{입고일} #{내용}",
                "entries": [self._payload(i) for i, r in self.rows.items() if r["status"] == status]}

    def request(self, method, url, headers=None, timeout=None, json=None):
        route = parse_qs(urlparse(url).query)["rest_route"][0].removeprefix("/kf-inbound/v1")
        self.calls.append((method, route))
        if headers.get("X-KF-Key") != self.KEY:
            return Resp(403, {"code": "kf_forbidden"})
        if route == "/ping":
            return Resp(200, {"ok": True, "site": "KF", "pending": sum(r["status"] == "pending" for r in self.rows.values())})
        if route == "/pending":
            return Resp(200, self._list("pending"))
        if route == "/claim":
            for r in self.rows.values():
                if r["status"] == "pending":
                    if not self.customers[r["customer_id"]]["room"]:
                        r.update(status="failed", note="방 미지정")
                    else:
                        r["status"] = "sending"
            return Resp(200, self._list("sending"))
        if route == "/report":
            if self.fail_report:
                return Resp(500, {"code": "down"})
            for x in json["results"]:
                r = self.rows[x["id"]]
                if r["status"] == "sending":
                    r.update(status=x["status"], note=x["note"])
            return Resp(200, {"ok": True})
        if route.startswith("/photo/"):
            return Resp(200, content=b"JPEG" + route.encode())
        return Resp(404, {"code": "rest_no_route"})

    def status(self):
        return {i: r["status"] for i, r in self.rows.items()}


class FakeKakao:
    def __init__(self, rooms):
        self.rooms = set(rooms)
        self.sent, self.files = [], []

    def open_chat(self, name, tab=None):
        if name not in self.rooms:
            raise ChatNotFound(f"검색 결과에 '{name}' 방이 없습니다")
        return name

    def send_text(self, chat, text):
        self.sent.append((chat, text))

    def send_files(self, chat, files):
        self.files.append((chat, [Path(f).name.split("_", 1)[1] for f in files]))

    def close_chat(self, chat):
        pass


@pytest.fixture
def site():
    return FakeSite()


def _run(site, tmp_path, kakao, **job):
    client = inbound.SiteClient("kflogistics.example", FakeSite.KEY, session=site)
    sender = KakaoPCSender(kakao, sleep=lambda s: None, should_stop=lambda: False)
    lines = []
    summary = inbound.run_job(Job(kind=KIND_INBOUND, mode="kakao-pc", **job), log=lines.append, sender=sender,
                              log_path=tmp_path / "log.csv", today=TODAY, client=client,
                              download_dir=tmp_path / "dl", unreported_path=tmp_path / "unreported.json")
    return summary, lines


def test_groups_per_room_and_reports_results(site, tmp_path):
    a = site.add(1, "냉동 삼겹살 20박스", photos=[11, 12], day="2026-10-07")
    b = site.add(1, "냉동 닭 10박스", photos=[13])
    c = site.add(2, "소스류 3박스")
    d = site.add(3, "방 없는 고객")
    kakao = FakeKakao({"KF - 세부마트 [CEBU]", "KF-마닐라푸드"})

    summary, lines = _run(site, tmp_path, kakao)

    assert (summary.sent, summary.failed) == (2, 0)
    assert kakao.sent[0] == ("KF - 세부마트 [CEBU]", "세부마트 입고 2건\n- 10/7 냉동 삼겹살 20박스\n- 10/8 냉동 닭 10박스")
    assert kakao.files[0] == ("KF - 세부마트 [CEBU]", ["p11.jpg", "p12.jpg", "p13.jpg"])
    assert (tmp_path / "dl" / "2026-10-08" / "11_p11.jpg").read_bytes() == b"JPEG/photo/11"
    assert site.status() == {a: "sent", b: "sent", c: "sent", d: "failed"}
    # 두 번째 실행: 이미 보낸 건은 다시 가져오지 않는다
    summary, lines = _run(site, tmp_path, kakao)
    assert summary.sent == 0 and "보낼 입고가 없습니다" in lines[-1]


def test_not_found_is_failed_and_stop_returns_to_pending(site, tmp_path):
    a = site.add(1, "A")
    b = site.add(2, "B")
    kakao = FakeKakao({"KF-마닐라푸드"})  # 세부마트 방은 못 찾음
    _run(site, tmp_path, kakao)
    assert site.status() == {a: "failed", b: "sent"}
    assert "검색 결과에" in site.rows[a]["note"]

    # 발송 중지: 아직 안 보낸 건은 '대기'로 돌아가 다음에 보낸다
    site2 = FakeSite()
    x, y = site2.add(1, "X"), site2.add(2, "Y")
    client = inbound.SiteClient("https://kf.example", FakeSite.KEY, session=site2)
    kakao = FakeKakao({"KF - 세부마트 [CEBU]", "KF-마닐라푸드"})
    sender = KakaoPCSender(kakao, sleep=lambda s: None, should_stop=lambda: len(kakao.sent) >= 1)
    inbound.run_job(Job(kind=KIND_INBOUND, mode="kakao-pc"), log=lambda s: None, sender=sender,
                    log_path=tmp_path / "log2.csv", today=TODAY, client=client,
                    download_dir=tmp_path / "dl", unreported_path=tmp_path / "u2.json")
    assert site2.status() == {x: "sent", y: "pending"}


def test_test_mode_sends_to_me_and_keeps_entries_pending(site, tmp_path):
    a = site.add(1, "A", photos=[5])
    b = site.add(2, "B")
    kakao = FakeKakao({"나"})
    summary, _ = _run(site, tmp_path, kakao, test_to="나", test_count=1)
    assert summary.sent == 1
    assert kakao.sent == [("나", "[테스트 · 원래 받는 방: KF - 세부마트 [CEBU]]\n세부마트 입고 1건\n- 10/8 A")]
    assert kakao.files == [("나", ["p5.jpg"])]
    assert site.status() == {a: "pending", b: "pending"}


def test_preview_does_not_claim(site, tmp_path):
    a = site.add(1, "A")
    client = inbound.SiteClient("https://kf.example", FakeSite.KEY, session=site)
    lines = []
    s = inbound.run_job(Job(kind=KIND_INBOUND, mode="dry-run"), log=lines.append, log_path=tmp_path / "l.csv",
                        today=TODAY, client=client, download_dir=tmp_path / "dl", unreported_path=tmp_path / "u.json")
    assert s.sent == 1 and site.status() == {a: "pending"}
    assert ("POST", "/claim") not in site.calls and any("세부마트 입고 1건" in ln for ln in lines)


def test_report_failure_is_kept_and_retried(site, tmp_path):
    a = site.add(2, "B")
    site.fail_report = True
    _run(site, tmp_path, FakeKakao({"KF-마닐라푸드"}))
    assert site.status() == {a: "sending"}
    assert json.loads((tmp_path / "unreported.json").read_text(encoding="utf-8"))[0]["status"] == "sent"
    site.fail_report = False
    _run(site, tmp_path, FakeKakao(set()))  # 다음 실행 때 먼저 밀린 결과를 알린다
    assert site.status() == {a: "sent"} and not (tmp_path / "unreported.json").exists()


def test_daily_cap_returns_rest_to_pending(site, tmp_path):
    a, b = site.add(1, "A"), site.add(2, "B")
    _run(site, tmp_path, FakeKakao({"KF - 세부마트 [CEBU]", "KF-마닐라푸드"}), daily_cap=1)
    assert site.status() == {a: "sent", b: "pending"}


def test_wrong_key_and_missing_settings(site, tmp_path, monkeypatch):
    client = inbound.SiteClient("https://kf.example", "wrong", session=site)
    with pytest.raises(inbound.SiteError, match="연결 키"):
        client.ping()
    monkeypatch.delenv("KF_SITE_URL", raising=False)
    monkeypatch.delenv("KF_SITE_KEY", raising=False)
    s = run_job(Job(kind=KIND_INBOUND, mode="kakao-pc"), log=lambda x: None, log_path=tmp_path / "l.csv")
    assert not s.ok and "사이트 주소" in s.message


def test_url_works_without_pretty_permalinks():
    c = inbound.SiteClient("kflogistics.co.kr/", "k")
    assert c.url("/claim") == "https://kflogistics.co.kr/?rest_route=/kf-inbound/v1/claim"


def test_envfile_updates_in_place(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text("# 주석\nSOLAPI_API_KEY=abc\nKF_SITE_URL=old\n", encoding="utf-8")
    monkeypatch.setattr(envfile.os, "environ", {})
    envfile.set_values(p, {"KF_SITE_URL": "https://new", "KF_SITE_KEY": "k"})
    assert p.read_text(encoding="utf-8") == "# 주석\nSOLAPI_API_KEY=abc\nKF_SITE_URL=https://new\nKF_SITE_KEY=k\n"
