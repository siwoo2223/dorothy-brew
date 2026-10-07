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

    def open_chat(self, name):
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
