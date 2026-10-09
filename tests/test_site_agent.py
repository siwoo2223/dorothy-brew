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

    def pull(self, info="", busy=False):
        self.busy_flags = getattr(self, "busy_flags", []) + [busy]
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


class FakeInput:
    """마우스·키보드 입력 흉내: tick 이 바뀌면 누군가 입력한 것, idle 은 마지막 입력 뒤 초."""

    def __init__(self, idle=999):
        self.tick, self.idle = 1000, idle

    def touch(self):
        self.tick += 1
        self.idle = 0


def test_waits_while_user_is_using_pc(tmp_path):
    api = FakeAPI([msg(1, "방A")])
    user = FakeInput(idle=5)  # 5초 전에 마우스를 씀
    kakao = FakeKakao({"방A"})
    agent = make_agent(api, kakao, tmp_path, idle_seconds=lambda: user.idle, input_tick=lambda: user.tick, need_idle=60)
    assert agent.tick() == 0 and api.busy_flags == [True]
    assert kakao.sent == [] and api.status() == {1: "pending"}  # 옛 사이트가 넘겨줘도 대기로 돌려줌
    user.idle = 61  # 1분 넘게 손 안 댐
    api.messages = [msg(1, "방A")]
    assert agent.tick() == 1 and api.busy_flags[-1] is False and kakao.sent == [("방A", "본문")]


def test_test_send_goes_out_immediately_while_pc_in_use(tmp_path):
    """테스트 발송은 60초 기다리지 않고 바로 보낸다. 같이 온 고객 발송은 대기로 돌린다."""
    api = FakeAPI([{**msg(1, "방A", "테스트"), "test": True}, msg(2, "방B", "고객")])
    user = FakeInput(idle=2)
    kakao = FakeKakao({"방A", "방B"})
    agent = make_agent(api, kakao, tmp_path, idle_seconds=lambda: user.idle, input_tick=lambda: user.tick, need_idle=60)
    assert agent.tick() == 1
    assert kakao.sent == [("방A", "테스트")] and api.status() == {1: "sent", 2: "pending"}


def test_touching_pc_does_not_stop_test_sends(tmp_path):
    api = FakeAPI([{**msg(i, "방A"), "test": True} for i in (1, 2)])
    user = FakeInput(idle=999)
    kakao = FakeKakao({"방A"})
    agent = Agent(api, lambda s: KakaoPCSender(kakao, sleep=lambda x: None, should_stop=lambda: False,
                                               max_consecutive_failures=99),
                  log=lambda s: None, sleep=lambda s: user.touch(), download_dir=tmp_path,
                  idle_seconds=lambda: user.idle, input_tick=lambda: user.tick, need_idle=60)
    agent.tick()
    assert api.status() == {1: "sent", 2: "sent"}


def test_touching_pc_between_messages_stops_the_rest(tmp_path):
    """메시지 사이 쉬는 시간에 마우스를 움직이면 남은 건은 보내지 않고 대기로 돌린다."""
    api = FakeAPI([msg(1, "방A"), msg(2, "방A"), msg(3, "방A")])
    user = FakeInput(idle=999)
    kakao = FakeKakao({"방A"})
    agent = Agent(api, lambda s: KakaoPCSender(kakao, sleep=lambda x: None, should_stop=lambda: False,
                                               max_consecutive_failures=99),
                  log=lambda s: None, sleep=lambda s: user.touch(), download_dir=tmp_path,
                  idle_seconds=lambda: user.idle, input_tick=lambda: user.tick, need_idle=60)
    agent.tick()
    assert len(kakao.sent) == 1
    assert api.status() == {1: "sent", 2: "pending", 3: "pending"}


def test_own_input_does_not_count_as_busy(tmp_path):
    """한 묶음을 다 보낸 직후에도(도우미 자신의 입력뿐이면) 다음 묶음을 바로 이어서 보낸다."""
    api = FakeAPI([msg(1, "방A")])
    user = FakeInput(idle=999)
    kakao = FakeKakao({"방A"})
    agent = make_agent(api, kakao, tmp_path, idle_seconds=lambda: user.idle, input_tick=lambda: user.tick, need_idle=60)
    agent.tick()
    user.idle = 3  # 방금 도우미가 보냈으니 '입력 후 3초' 이지만 tick 은 도우미가 기억한 그대로
    api.messages = [msg(2, "방A")]
    assert agent.tick() == 1 and api.status() == {1: "sent", 2: "sent"}


def test_site_setting_zero_disables_idle_wait(tmp_path):
    api = FakeAPI([msg(1, "방A")])
    api.pull_settings = {"idle_seconds": "0"}
    orig = api.pull
    api.pull = lambda info="", busy=False: {**orig(info, busy), "settings": {"gap_min": "3", "gap_max": "3", "idle_seconds": "0"}}
    user = FakeInput(idle=1)
    kakao = FakeKakao({"방A"})
    agent = make_agent(api, kakao, tmp_path, idle_seconds=lambda: user.idle, input_tick=lambda: user.tick, need_idle=60)
    assert agent.tick() == 1 and kakao.sent


# ───────────── 사진: 사이트 '전체 복사'처럼 이어 붙여 그림으로 보내기 ─────────────
def _jpg(path, color, size=(300, 200)):
    from PIL import Image

    Image.new("RGB", size, color).save(path, "JPEG")
    return str(path)


def test_merge_photos_stacks_vertically_in_groups(tmp_path):
    from PIL import Image

    from receivables.photo_merge import PER_IMAGE, is_image, merge_photos

    photos = [_jpg(tmp_path / f"{i}.jpg", "red", (300, 200)) for i in range(PER_IMAGE + 1)]
    out = merge_photos(photos, tmp_path / "m")
    assert len(out) == 2 and all(is_image(p) for p in out)
    assert Image.open(out[0]).size == (300, 200 * PER_IMAGE + 14 * (PER_IMAGE - 1))
    html = tmp_path / "x.jpg"
    html.write_text("<html>로그인</html>")
    assert not is_image(html)


def test_photos_go_as_one_merged_image_and_other_files_as_files(tmp_path):
    class ImageKakao(FakeKakao):
        images = []

        def send_image(self, chat, path):
            self.images.append((chat, Path(path).name))

    kakao = ImageKakao({"방A"})
    pdf = tmp_path / "명세서.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    from receivables.sender import OutgoingMessage

    m = OutgoingMessage("1", "방A", "글", {}, chat_name="방A",
                        attachments=[_jpg(tmp_path / "a.jpg", "red"), _jpg(tmp_path / "b.jpg", "blue"), str(pdf)])
    r = KakaoPCSender(kakao, sleep=lambda s: None, should_stop=lambda: False).send([m])[0]
    assert r.ok and "사진 2장" in r.detail and "첨부 1개" in r.detail
    assert kakao.images == [("방A", "입고사진_1.jpg")]
    assert kakao.files == [("방A", ["명세서.pdf"])]


def test_drive_photo_falls_back_when_thumbnail_gives_web_page(tmp_path):
    from receivables.site_agent import SiteAPI

    jpg = Path(_jpg(tmp_path / "real.jpg", "green")).read_bytes()

    class Resp:
        def __init__(self, status, content):
            self.status_code, self.content, self.text = status, content, ""

    class Session:
        def __init__(self, ok_host):
            self.ok_host, self.urls = ok_host, []

        def get(self, url, timeout=None):
            self.urls.append(url)
            return Resp(200, jpg if self.ok_host and self.ok_host in url else b"<html>login</html>")

    s = Session("lh3.googleusercontent.com")
    api = SiteAPI("https://kf.example", "k", session=s)
    p = api.download({"name": "a.jpg", "url": "https://drive.google.com/thumbnail?id=ABC_1-x&sz=w2000"}, tmp_path / "d")
    assert p.read_bytes() == jpg and s.urls[1] == "https://lh3.googleusercontent.com/d/ABC_1-x=w2000"

    api = SiteAPI("https://kf.example", "k", session=Session(None))
    try:
        api.download({"name": "b.jpg", "url": "https://drive.google.com/thumbnail?id=ZZZ&sz=w2000"}, tmp_path / "d")
        raise AssertionError("실패해야 함")
    except SiteError as exc:
        assert "웹페이지" in str(exc)
    assert not (tmp_path / "d" / "b.jpg").exists()


def test_same_title_ignores_dash_and_space_differences():
    from receivables.kakao_pc import same_title

    assert same_title("KF – 짱구분식", "KF - 짱구분식")
    assert same_title("KF  -  유니 ", "KF - 유니")
    assert same_title("KF - 양승태", "KF - 양승태")
    assert not same_title("KF - 유니2", "KF - 유니")
    assert not same_title("671 KF - 짱구분식", "KF - 짱구분식")


def test_same_title_ignores_spaces_around_dash():
    from receivables.kakao_pc import same_title

    assert same_title("KF-유진애견샵", "KF - 유진애견샵")
    assert not same_title("KF-유진애견샵2", "KF - 유진애견샵")


def test_search_queries_try_core_name():
    from receivables.kakao_pc import search_queries

    assert search_queries("KF - 유진애견샵") == ["KF - 유진애견샵", "유진애견샵", "KF-유진애견샵"]
    assert search_queries("유니") == ["유니"]
