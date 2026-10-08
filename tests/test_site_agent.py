"""사무실 PC 발송 도우미(site_agent) 테스트: 사이트 대신 가짜 API, 카톡 대신 가짜 카카오톡."""
from pathlib import Path

from receivables.kakao_pc import ChatNotFound, KakaoPCSender
from receivables.site_agent import Agent, SiteError


class FakeKakao:
    def __init__(self, rooms, broken=False):
        self.rooms, self.broken = set(rooms), broken
        self.sent, self.files = [], []

    def open_chat(self, name, tab=None):
        if self.broken:
            raise RuntimeError("카카오톡 창을 찾을 수 없습니다")
        if name not in self.rooms:
            raise ChatNotFound(f"검색 결과에 '{name}' 방이 없습니다")
        return name

    def send_text(self, chat, text):
        self.sent.append((chat, text))

    def send_files(self, chat, files):
        self.files.append((chat, [Path(f).name for f in files]))

    def close_chat(self, chat):
        pass


class FakeAPI:
    def __init__(self, messages, stop_after=None, names_request=None):
        self.messages = messages
        self.reports = []
        self.stop_after = stop_after
        self.names_request = names_request
        self.names = None
        self.fail_report = False

    def pull(self, info=""):
        msgs, self.messages = self.messages, []
        return {"ok": True, "settings": {"gap_min": "3", "gap_max": "3", "find_mode": "ocr"},
                "messages": msgs, "names_request": self.names_request}

    def stop_requested(self):
        sent = sum(1 for r in self.reports if r["status"] == "sent")
        return self.stop_after is not None and sent >= self.stop_after

    def report(self, results):
        if self.fail_report:
            raise SiteError("끊김")
        self.reports.extend(results)

    def download(self, att, folder):
        if att.get("url") == "bad":
            raise SiteError("404")
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / att["name"]
        p.write_bytes(b"x")
        return p

    def send_names(self, tab, exact, names, error=""):
        self.names = (tab, exact, names, error)

    def status(self):
        return {r["id"]: r["status"] for r in self.reports}


def msg(i, room, text="본문", atts=()):
    return {"id": i, "customer": f"고객{i}", "room": room, "tab": "chats", "text": text, "attachments": list(atts)}


def make_agent(api, kakao, tmp_path, **kw):
    return Agent(api, lambda s: KakaoPCSender(kakao, sleep=lambda x: None, should_stop=lambda: False,
                                              max_consecutive_failures=99),
                 log=lambda s: None, sleep=lambda s: None, download_dir=tmp_path, **kw)


def test_sends_and_reports_each_message(tmp_path):
    api = FakeAPI([msg(1, "방A", atts=[{"name": "p1.jpg", "url": "/x.jpg"}]), msg(2, "없는방"), msg(3, "방B")])
    kakao = FakeKakao({"방A", "방B"})
    assert make_agent(api, kakao, tmp_path).tick() == 3
    assert api.status() == {1: "sent", 2: "failed", 3: "sent"}
    assert kakao.files == [("방A", ["p1.jpg"])]
    assert "검색 결과에" in [r for r in api.reports if r["id"] == 2][0]["note"]


def test_stop_returns_rest_to_pending(tmp_path):
    api = FakeAPI([msg(1, "방A"), msg(2, "방A"), msg(3, "방A")], stop_after=1)
    make_agent(api, FakeKakao({"방A"}), tmp_path).tick()
    assert api.status() == {1: "sent", 2: "pending", 3: "pending"}


def test_kakao_broken_stops_after_three_and_keeps_rest(tmp_path):
    api = FakeAPI([msg(i, "방A") for i in range(1, 6)])
    agent = make_agent(api, FakeKakao({"방A"}, broken=True), tmp_path)
    agent.tick()
    assert api.status() == {1: "failed", 2: "failed", 3: "failed", 4: "pending", 5: "pending"}
    # 잠시 쉬는 동안 가져온 건은 보내지 않고 대기로 돌려준다
    api.messages = [msg(6, "방A")]
    agent.tick()
    assert api.status()[6] == "pending"


def test_attachment_download_failure_fails_only_that_message(tmp_path):
    api = FakeAPI([msg(1, "방A", atts=[{"name": "a.jpg", "url": "bad"}]), msg(2, "방A")])
    kakao = FakeKakao({"방A"})
    make_agent(api, kakao, tmp_path).tick()
    assert api.status() == {1: "failed", 2: "sent"} and len(kakao.sent) == 1


def test_report_backlog_is_retried(tmp_path):
    api = FakeAPI([msg(1, "방A")])
    api.fail_report = True
    agent = make_agent(api, FakeKakao({"방A"}), tmp_path)
    agent.tick()
    assert api.reports == [] and agent.reporter.backlog[0]["status"] == "sent"
    api.fail_report = False
    agent.tick()
    assert api.status() == {1: "sent"}


def test_names_request_is_handled(tmp_path):
    api = FakeAPI([], names_request={"tab": "chats", "exact": True})
    agent = make_agent(api, FakeKakao(set()), tmp_path, extract_names=lambda tab, exact, log: ["송장방", "HARRY"])
    agent.tick()
    assert api.names == ("chats", True, ["송장방", "HARRY"], "")
