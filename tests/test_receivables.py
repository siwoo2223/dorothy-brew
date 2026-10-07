import datetime as dt
import hashlib
import hmac
import re

import pandas as pd
import pytest

from receivables import campaign, history
from receivables.loader import ColumnMap, group_customers, normalize_phone
from receivables.sender import DryRunSender, OutgoingMessage, SolapiSender
from receivables.template import find_variables, format_value, render

TODAY = dt.date(2026, 10, 6)


@pytest.fixture
def ledger():
    return pd.DataFrame(
        {
            "고객명": ["카페하늘", "카페하늘", "베이커리온", "오피스커피", "스튜디오"],
            "전화번호": ["010-1234-5678", "010-1234-5678", 1098765432, "010-5555-7777", "010-2222-333"],
            "거래일자": pd.to_datetime(["2026-08-03", "2026-08-20", "2026-09-01", "2026-09-05", "2026-09-10"]),
            "품목": ["원두A", "원두B", "블렌드", "콜드브루", "디카페인"],
            "미수금": [180000, 55000, 320000, 0, 76000],
            "납부기한": pd.to_datetime(["2026-09-10", "2026-09-25", "2026-10-01", "2026-10-05", "2026-10-10"]),
            "담당자": ["김", "김", "박", "김", "박"],
        }
    )


def test_render_and_formats():
    r = render("#{이름}님 #{금액}원 #{날짜} #{없음}", {"이름": "홍길동", "금액": 1234567.0, "날짜": dt.date(2026, 1, 2)})
    assert r.text == "홍길동님 1,234,567원 2026-01-02 #{없음}"
    assert r.missing == ["없음"]
    assert find_variables("#{a} #{b} #{a}") == ["a", "b"]
    assert format_value(pd.NaT) == ""
    assert format_value(float("nan")) == ""


def test_normalize_phone():
    assert normalize_phone("010-1234-5678") == "01012345678"
    assert normalize_phone(1012345678) == "01012345678"  # 엑셀이 앞자리 0 을 지운 경우
    assert normalize_phone(1012345678.0) == "01012345678"


def test_group_customers(ledger):
    customers = group_customers(ledger, ColumnMap(), today=TODAY)
    by_name = {c.name: c for c in customers}
    assert set(by_name) == {"카페하늘", "베이커리온", "스튜디오"}  # 미수 0원 고객 제외

    sky = by_name["카페하늘"].variables
    assert sky["미수총액"] == 235000
    assert sky["미수건수"] == 2
    assert sky["미수내역"] == "- 2026-08-03 원두A 180,000원\n- 2026-08-20 원두B 55,000원"
    assert sky["담당자"] == "김"
    assert sky["연체일수"] == 26
    assert "품목" not in sky  # 고객 안에서 값이 여러 개인 열은 변수로 쓰지 않음

    assert by_name["베이커리온"].phone == "01098765432"
    assert by_name["베이커리온"].problems == []
    assert by_name["스튜디오"].problems  # 010 번호인데 10자리


def test_missing_column(ledger):
    with pytest.raises(ValueError, match="미수액"):
        group_customers(ledger, ColumnMap(amount="미수액"))


def test_prepare_and_send(ledger, tmp_path):
    customers = group_customers(ledger, ColumnMap(), today=TODAY)
    drafts = campaign.prepare(customers, "#{고객명}님 #{미수총액}원\n#{미수내역}", already_sent={"베이커리온|01098765432"})
    by_name = {d.customer.name: d for d in drafts}
    assert by_name["카페하늘"].variables == {
        "고객명": "카페하늘",
        "미수총액": "235,000",
        "미수내역": "- 2026-08-03 원두A 180,000원\n- 2026-08-20 원두B 55,000원",
    }
    assert by_name["베이커리온"].already_sent_today
    assert not by_name["스튜디오"].sendable

    log = tmp_path / "log.csv"
    results = campaign.send(drafts, DryRunSender(), log_path=log)
    assert {r.to for r in results} == {"01012345678", "01098765432"}  # 문제 있는 고객은 제외
    # 모의 발송은 중복 발송 판단에 쓰지 않는다
    assert history.sent_on(dt.date.today(), log) == set()


class FakeResponse:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, json, headers, timeout):
        self.calls.append((url, json, headers))
        return self.response


def test_solapi_alimtalk_payload_and_failures():
    session = FakeSession(
        FakeResponse(200, {"groupInfo": {"groupId": "G1"},
                           "failedMessageList": [{"to": "01022223333", "statusCode": "3059", "statusMessage": "변수 불일치"}]})
    )
    sender = SolapiSender("KEY", "SECRET", "0212345678", mode="alimtalk",
                          pf_id="PF", template_id="TP", session=session)
    msgs = [
        OutgoingMessage("a|01011112222", "01011112222", "본문", {"고객명": "가"}),
        OutgoingMessage("b|01022223333", "01022223333", "본문", {"고객명": "나"}),
    ]
    results = sender.send(msgs)
    assert [r.ok for r in results] == [True, False]
    assert "변수 불일치" in results[1].detail

    _, body, headers = session.calls[0]
    first = body["messages"][0]
    assert first["type"] == "ATA"
    assert first["kakaoOptions"] == {"pfId": "PF", "templateId": "TP",
                                     "variables": {"#{고객명}": "가"}, "disableSms": False}

    m = re.match(r"HMAC-SHA256 apiKey=KEY, date=(\S+), salt=(\w+), signature=(\w+)", headers["Authorization"])
    assert m
    date, salt, sig = m.groups()
    assert sig == hmac.new(b"SECRET", (date + salt).encode(), hashlib.sha256).hexdigest()


def test_solapi_http_error_marks_all_failed():
    session = FakeSession(FakeResponse(401, {"errorCode": "InvalidAPIKey", "errorMessage": "잘못된 키"}))
    sender = SolapiSender("K", "S", "0212345678", mode="sms", session=session)
    results = sender.send([OutgoingMessage("a", "01011112222", "안녕하세요", {})])
    assert not results[0].ok
    assert "InvalidAPIKey" in results[0].detail
    assert session.calls[0][1]["messages"][0] == {"to": "01011112222", "from": "0212345678", "text": "안녕하세요"}


def test_alimtalk_requires_ids():
    with pytest.raises(ValueError):
        SolapiSender("K", "S", "0212345678", mode="alimtalk")


# ───────────── PC 카카오톡 발송 ─────────────
from receivables.kakao_pc import ChatNotFound, KakaoPCSender


class FakeKakao:
    """친구 목록에 있는 이름만 채팅방이 열리는 가짜 카카오톡."""

    def __init__(self, friends):
        self.friends = set(friends)
        self.sent = []
        self.closed = []

    def open_chat(self, name, tab=None):
        self.tabs = getattr(self, "tabs", []) + [tab]
        if name not in self.friends:
            raise ChatNotFound(f"'{name}' 채팅방을 찾지 못했습니다")
        return name

    def send_text(self, chat, text):
        self.sent.append((chat, text))

    def close_chat(self, chat):
        self.closed.append(chat)


def test_kakao_pc_send_uses_chat_name_column(ledger, tmp_path):
    ledger["카톡이름"] = ["하늘 사장님", "하늘 사장님", None, None, "스튜디오"]
    customers = group_customers(ledger, ColumnMap(), today=TODAY, require_phone=False)
    assert all(not c.problems for c in customers)  # 카톡 발송은 전화번호 형식을 따지지 않음
    drafts = campaign.prepare(customers, "#{고객명}님 미수금 #{미수총액}원", chat_name_col="카톡이름")
    assert [d.chat_name for d in drafts] == ["하늘 사장님", "베이커리온", "스튜디오"]  # 비면 고객명

    kakao = FakeKakao({"하늘 사장님", "스튜디오"})
    waits, progress = [], []
    sender = KakaoPCSender(kakao, min_interval=8, max_interval=8, sleep=waits.append)
    log = tmp_path / "log.csv"
    results = campaign.send(drafts, sender, log_path=log, on_result=lambda i, n, r: progress.append((i, n, r.ok)))

    assert [r.ok for r in results] == [True, False, True]
    assert kakao.sent == [("하늘 사장님", "카페하늘님 미수금 235,000원"), ("스튜디오", "스튜디오님 미수금 76,000원")]
    assert kakao.closed == ["하늘 사장님", "스튜디오"]
    assert waits == [8, 8]  # 메시지 사이에만 대기
    assert progress == [(1, 3, True), (2, 3, False), (3, 3, True)]
    # 결과가 나올 때마다 기록되어 중복 발송/한도 계산에 쓰인다
    assert history.sent_on(dt.date.today(), log) == {"카페하늘|01012345678", "스튜디오|0102222333"}
    assert history.count_sent(dt.date.today(), "PC카카오톡", log) == 2


def test_kakao_pc_stops_after_consecutive_failures():
    kakao = FakeKakao(set())
    sender = KakaoPCSender(kakao, max_consecutive_failures=2, sleep=lambda s: None)
    msgs = [OutgoingMessage(str(i), "", "본문", {}, chat_name=f"고객{i}") for i in range(4)]
    results = sender.send(msgs)
    assert not any(r.ok for r in results)
    assert "중단" in results[2].detail and "중단" in results[3].detail


def test_history_upgrades_old_header(tmp_path):
    log = tmp_path / "log.csv"
    log.write_text("발송시각,방식,고객명,전화번호,미수총액,결과,상세,본문\n"
                   f"{dt.date.today()} 09:00:00,문자,가,010,1,성공,,x\n", encoding="utf-8-sig")
    history.append([{"발송시각": f"{dt.date.today()} 10:00:00", "방식": "PC카카오톡", "고객명": "나",
                     "결과": "성공"}], log)
    assert history.sent_on(dt.date.today(), log) == {"가|010", "나|"}


# ───────────── 공지사항 / 테스트 모드 ─────────────
from receivables.loader import load_recipients


def test_load_recipients_includes_paid_customers(ledger):
    people = load_recipients(ledger, today=TODAY)
    assert [p.name for p in people] == ["카페하늘", "베이커리온", "오피스커피", "스튜디오"]  # 완납 고객도 포함, 중복 제거
    assert people[0].variables["담당자"] == "김"
    drafts = campaign.prepare(people, "#{고객명}님, 10월 9일은 휴무입니다.")
    assert drafts[2].text == "오피스커피님, 10월 9일은 휴무입니다."


def test_test_mode_redirects_and_is_not_counted_as_sent(ledger, tmp_path):
    customers = group_customers(ledger, ColumnMap(), today=TODAY, require_phone=False)
    drafts = campaign.prepare(customers, "#{고객명}님 #{미수총액}원")
    kakao = FakeKakao({"사장님"})
    log = tmp_path / "log.csv"
    results = campaign.send(drafts[:2], KakaoPCSender(kakao, sleep=lambda s: None), log_path=log, test_to="사장님")
    assert all(r.ok for r in results)
    assert [chat for chat, _ in kakao.sent] == ["사장님", "사장님"]  # 고객이 아니라 나에게
    assert kakao.sent[0][1] == "[테스트 · 원래 받는 사람: 카페하늘]\n카페하늘님 235,000원"
    assert history.sent_on(dt.date.today(), log) == set()  # 테스트는 '이미 보냄'으로 치지 않음
    assert history.count_sent(dt.date.today(), "PC카카오톡", log) == 2  # 하루 한도에는 포함


def test_sent_on_is_per_kind(tmp_path):
    log = tmp_path / "log.csv"
    history.append([{"발송시각": f"{dt.date.today()} 10:00:00", "구분": "미수금 안내", "방식": "PC카카오톡",
                     "고객명": "가", "전화번호": "", "결과": "성공"}], log)
    assert history.sent_on(dt.date.today(), log, kind="미수금 안내") == {"가|"}
    assert history.sent_on(dt.date.today(), log, kind="공지사항") == set()


# ───────────── 카톡 이름 짝맞추기 ─────────────
from receivables.kakao_names import clean_name, match_names


def test_match_names():
    kakao = ["카페하늘 김사장", "베이커리 온", "박지민", "오피스커피(강남점)", "스튜디오카페 대표님"]
    m = {x.customer: x for x in match_names(["카페하늘", "베이커리온", "오피스커피", "스튜디오카페", "없는가게"], kakao)}
    assert m["카페하늘"].kakao_name == "카페하늘 김사장" and m["카페하늘"].level == "비슷함"
    assert m["베이커리온"].kakao_name == "베이커리 온" and m["베이커리온"].level == "일치"  # 띄어쓰기 무시
    assert m["오피스커피"].kakao_name == "오피스커피(강남점)"  # 괄호 무시
    assert m["스튜디오카페"].kakao_name == "스튜디오카페 대표님"  # 호칭 무시
    assert m["없는가게"].kakao_name == "" and m["없는가게"].level == "없음"


def test_clean_name():
    assert clean_name("  홍길동\n오늘도 화이팅 ") == "홍길동"
    assert clean_name("") == ""


def test_chat_room_column_overrides_friend_name(ledger, tmp_path):
    ledger["카톡이름"] = ["하늘 사장님", "하늘 사장님", None, None, None]
    ledger["채팅방"] = [None, None, "베이커리온 납품방", None, None]
    customers = group_customers(ledger, ColumnMap(), today=TODAY, require_phone=False)
    drafts = campaign.prepare(customers, "#{고객명}님", chat_name_col="카톡이름", room_col="채팅방")
    assert [(d.chat_name, d.search_tab) for d in drafts] == [
        ("하늘 사장님", ""), ("베이커리온 납품방", "chats"), ("스튜디오", "")
    ]
    assert drafts[1].destination == "💬 베이커리온 납품방"

    kakao = FakeKakao({"하늘 사장님", "베이커리온 납품방", "스튜디오"})
    results = campaign.send(drafts, KakaoPCSender(kakao, sleep=lambda s: None), log_path=tmp_path / "l.csv")
    assert all(r.ok for r in results)
    assert kakao.tabs == [None, "chats", None]  # 채팅방만 채팅 목록에서 찾음
    assert results[1].detail == "채팅방 '베이커리온 납품방'에게 전송"


def test_filter_ocr_lines_keeps_names_only():
    from receivables.kakao_names import filter_ocr_lines

    lines = ["친구", "Jung-woong", "어제 10월 6일", "선물하기", "즐겨찾는 친구 7", "사장님 노력", "항상 조심을",
             "KF물류", "01064450246", "기나글로벌 15", "오전 11:43", "300+", "1개의 채팅방", "29",
             "본건 딜레이 공문 전달 드립니다 확인 부탁드리며 일정 공유드리겠습니다"]
    assert filter_ocr_lines(lines) == ["Jung-woong", "사장님 노력", "항상 조심을", "KF물류", "기나글로벌"]
