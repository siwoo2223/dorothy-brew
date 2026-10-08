"""미수금 안내 · 공지사항 자동 발송 (Streamlit 화면)

실행: streamlit run app.py
"""
from __future__ import annotations

import datetime as dt
import io
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import sys

# 프로그램이 켜진 채로 새 버전 파일을 덮어써도 옛 코드가 메모리에 남지 않도록, 매번 새로 읽는다
for _module in [m for m in sys.modules if m == "receivables" or m.startswith("receivables.")]:
    del sys.modules[_module]

from receivables import campaign, control, envfile, history, inbound, scheduler, templates_store
from receivables.runner import KIND_INBOUND, Job
from receivables.runner import run_job as runner_run_job
from receivables.kakao_names import (
    diagnose,
    extract_names,
    extract_names_exact,
    install_korean_ocr,
    match_names,
)
from receivables.kakao_pc import KakaoPCSender, Win32KakaoDriver, check_send
from receivables.loader import (
    DEFAULT_LINE_TEMPLATE,
    ColumnMap,
    group_customers,
    load_recipients,
    read_table,
)
from receivables.sender import DryRunSender, SolapiSender
from receivables.template import ALIMTALK_MAX_LENGTH, find_variables, format_value

ROOT = Path(__file__).parent
SAMPLE_FILE = ROOT / "samples" / "미수금_샘플.xlsx"
KIND_RECEIVABLE = "미수금 안내"
KIND_NOTICE = "공지사항"
MENU_NAMES = "카톡 이름 정리"
MENU_INBOUND = "입고 알림 (사이트)"
MODE_KEYS = {"미리보기(실제 발송 안 함)": "dry-run", "PC 카카오톡(내 계정)": "kakao-pc",
             "카카오 알림톡": "alimtalk", "문자(SMS/LMS)": "sms"}
TEMPLATE_DIRS = {KIND_RECEIVABLE: ROOT / "templates" / "미수금", KIND_NOTICE: ROOT / "templates" / "공지"}

load_dotenv(ROOT / ".env")
st.set_page_config(page_title="미수금·공지 발송", page_icon="💬", layout="wide")

# ───────────────────────── 사이드바: 발송 설정 ─────────────────────────
with st.sidebar:
    st.header("메뉴")
    kind = st.radio("메뉴", [KIND_RECEIVABLE, KIND_NOTICE, MENU_INBOUND, MENU_NAMES], label_visibility="collapsed")
    is_notice = kind == KIND_NOTICE


def to_excel(frame: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    frame.to_excel(buf, index=False)
    return buf.getvalue()


def schedule_list(entries: list) -> None:
    """예약 목록: 다음/마지막 실행, 실행 기록, 등록·삭제."""
    if not entries:
        return
    st.markdown(f"**예약 목록 ({len(entries)}개)**")
    for e in entries:
        status = scheduler.task_status(e.id)
        mode_label = {v: k for k, v in MODE_KEYS.items()}.get(e.job.mode, e.job.mode)
        c1, c2, c3 = st.columns([5, 2, 1])
        with c1:
            st.markdown(f"**{e.name}** · {e.schedule.describe()} · {e.job.kind} · {mode_label}"
                        + (f" · 🧪 테스트({e.job.test_to})" if e.job.test_to else ""))
            source_label = ("사이트 입고" if e.job.kind == KIND_INBOUND
                            else f"엑셀: {Path(e.job.ledger).name}" if e.job.ledger else "직접 입력 명단")
            st.caption(
                (f"다음 실행: {status['next'] or '없음'} · 마지막 실행: {status['last'] or '아직 없음'}" if status
                 else "⚠️ Windows 작업 스케줄러에 등록돼 있지 않습니다")
                + f" · {source_label}"
            )
        with c2:
            log_file = e.folder / "실행기록.txt"
            if log_file.exists():
                with st.popover("실행 기록"):
                    st.code(log_file.read_text(encoding="utf-8")[-4000:], language=None)
        with c3:
            if not status and st.button("등록하기", key=f"reg-sched-{e.id}", type="primary"):
                try:
                    if e.schedule.repeat == "once":
                        e.schedule.validate()
                    scheduler.register(e)
                    st.rerun()
                except Exception as exc:
                    st.error(f"등록 실패: {exc}")
            if st.button("삭제", key=f"del-sched-{e.id}"):
                scheduler.remove(e.id)
                st.rerun()


def stop_button() -> None:
    if st.button("⏹ 발송 중지", use_container_width=True,
                 help="지금 보내고 있는 1건을 마치고 멈춥니다(예약 발송 포함). 키보드 ESC 를 1초 정도 누르고 있어도 멈춥니다."):
        control.request_stop()
        st.warning("발송 중지를 요청했습니다. 지금 보내는 1건을 마치고 멈춥니다.")


def inbound_tool() -> None:
    """kflogistics 사이트(워드프레스)에 등록된 입고를 가져와 고객 카톡 방으로 사진과 함께 보낸다."""
    with st.sidebar:
        stop_button()
    st.title("📦 입고 알림 (kflogistics 사이트 연동)")
    st.caption("사이트 [입고 알림] 메뉴에서 직원이 입고(사진·내용)를 등록해 두면, 정해진 시간에 이 PC 카카오톡으로 "
               "고객(카톡 방)별로 모아서 보냅니다. 결과(완료·실패)는 사이트 입고 목록에 표시됩니다.")

    st.subheader("1. 사이트 연결")
    st.caption("사이트 관리자 화면 > 입고 알림 > 문구·연결 설정 에 있는 '사이트 주소'와 '연결 키'를 넣으세요.")
    c1, c2 = st.columns(2)
    site_url = c1.text_input("사이트 주소", os.getenv("KF_SITE_URL", ""), placeholder="https://kflogistics.co.kr")
    site_key = c2.text_input("연결 키", os.getenv("KF_SITE_KEY", ""), type="password")
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("저장", use_container_width=True):
        envfile.set_values(ROOT / ".env", {"KF_SITE_URL": site_url.strip(), "KF_SITE_KEY": site_key.strip()})
        st.success("저장했습니다. 예약 발송도 이 값을 씁니다.")
    if b2.button("연결 확인", use_container_width=True):
        try:
            info = inbound.SiteClient(site_url, site_key).ping()
            st.success(f"연결됐습니다: {info.get('site', '')} · 발송 대기 {info.get('pending', 0)}건")
        except (ValueError, inbound.SiteError) as exc:
            st.error(str(exc))

    st.subheader("2. 대기 중인 입고 미리보기")
    if st.button("사이트에서 불러오기"):
        try:
            entries, tmpl, line = inbound.SiteClient(site_url, site_key).pending()
            st.session_state["inbound_preview"] = (entries, tmpl, line)
        except (ValueError, inbound.SiteError) as exc:
            st.error(str(exc))
    if "inbound_preview" in st.session_state:
        entries, tmpl, line = st.session_state["inbound_preview"]
        if not entries:
            st.info("발송 대기 중인 입고가 없습니다.")
        groups = inbound.group_entries(entries)
        no_room = [g for g in groups if not g.room]
        if no_room:
            st.warning("카톡 방 이름이 없는 고객은 보내지 않고 '실패'로 표시됩니다: "
                       + ", ".join(g.customer for g in no_room) + " → 사이트 [고객·채팅방]에서 방 이름을 넣어 주세요.")
        for g in groups:
            if g.room:
                with st.expander(f"💬 {g.room} · 입고 {len(g.entries)}건 · 사진 {g.photo_count}장"):
                    st.text(inbound.build_text(g, tmpl, line, dt.date.today()))

    st.subheader("3. 발송 설정")
    st.warning("카카오 공식 기능이 아니라서 **계정이 제한될 수 있습니다.** 간격을 넉넉히 두세요. "
               "발송하는 동안 이 PC 의 마우스·키보드를 쓰지 마세요.")
    s1, s2 = st.columns(2)
    find_label = s1.radio("방 찾는 방식", ["글자 인식으로 고르기", "차례로 열어 확인 (글자 인식 없음)"], key="in_find")
    gap = s2.slider("메시지 간격(초)", 3, 60, (8, 15), key="in_gap")
    daily_cap = s2.number_input("하루 최대 발송 수", 1, 500, 500, key="in_cap", help="미수금·공지·입고·테스트를 모두 합친 수")
    is_test = st.toggle("🧪 테스트 모드 (고객 대신 나에게 보내기)", value=True, key="in_test",
                        help="입고 건은 '대기'로 그대로 남고, 메시지만 아래 테스트 받을 곳으로 갑니다.")
    test_to, test_tab, test_count = "", "", 3
    if is_test:
        t1, t2, t3 = st.columns([3, 2, 1])
        test_to = t1.text_input("테스트 받을 곳 (카톡 이름 또는 채팅방 이름)", key="in_test_to").strip()
        test_tab = "chats" if t2.radio("찾을 곳", ["친구 목록", "채팅 목록"], horizontal=True, key="in_test_tab") == "채팅 목록" else "friends"
        test_count = t3.number_input("건수", 1, 50, 3, key="in_test_count")

    def make_job(mode: str) -> Job:
        return Job(kind=KIND_INBOUND, mode=mode,
                   find_mode="keyboard" if find_label.startswith("차례로") else "ocr",
                   gap=tuple(gap), daily_cap=int(daily_cap),
                   test_to=test_to if is_test else "", test_tab=test_tab, test_count=int(test_count))

    st.subheader("4. 지금 보내기")
    blocked = is_test and not test_to
    if blocked:
        st.caption("테스트 받을 곳을 입력하거나 테스트 모드를 끄세요.")
    if st.button("📨 " + ("테스트로 보내기" if is_test else "대기 중인 입고 지금 모두 보내기"), type="primary", disabled=blocked):
        lines: list[str] = []
        box = st.empty()

        def log(text: str) -> None:
            lines.append(text)
            box.code("\n".join(lines[-40:]), language=None)

        summary = runner_run_job(make_job("kakao-pc"), log=log)
        (st.success if summary.ok else st.warning)(
            ("[테스트] " if is_test else "") + f"성공 {summary.sent}건 / 실패 {summary.failed}건"
            + (f" · {summary.message}" if summary.message else ""))
        st.session_state.pop("inbound_preview", None)

    st.subheader("5. ⏰ 자동 발송 (예: 매일 17시)")
    st.caption("정한 시간에 그때까지 등록된 입고를 고객별로 모아 보냅니다. 그 뒤에 등록한 입고는 다음 발송 때 갑니다. "
               "**PC 는 켜져 있고 로그인(화면 잠금 해제) 상태**, PC 카카오톡도 로그인돼 있어야 합니다.")
    r1, r2, r3 = st.columns([2, 1, 1])
    days = r1.multiselect("요일", list(range(7)), default=list(range(6)), format_func=lambda i: scheduler.WEEKDAYS[i],
                          key="in_days")
    hour = r2.selectbox("시", list(range(24)), index=17, key="in_hour",
                        format_func=lambda h: f"{'오전' if h < 12 else '오후'} {h % 12 or 12}시")
    minute = r3.selectbox("분", list(range(0, 60, 5)), key="in_min", format_func=lambda m: f"{m:02d}분")
    if is_test:
        st.info(f"지금은 테스트 모드라 예약해도 **{test_to or '(테스트 받을 곳)'}** 에게만 갑니다. 실제 고객에게 보내려면 테스트 모드를 끄고 예약하세요.")
    if st.button("⏰ 자동 발송 예약", disabled=blocked or not days):
        try:
            if not (site_url and site_key):
                raise ValueError("1번에서 사이트 주소와 연결 키를 넣고 '저장'을 먼저 눌러 주세요.")
            if os.getenv("KF_SITE_URL", "") != site_url.strip() or os.getenv("KF_SITE_KEY", "") != site_key.strip():
                envfile.set_values(ROOT / ".env", {"KF_SITE_URL": site_url.strip(), "KF_SITE_KEY": site_key.strip()})
            at = dt.datetime.combine(dt.date.today(), dt.time(hour, minute)).isoformat(timespec="minutes")
            sch = scheduler.Schedule("daily" if len(days) == 7 else "weekly", at, sorted(days))
            sch.validate()
            entry = scheduler.Entry(scheduler.new_id(), f"입고 알림 {hour:02d}:{minute:02d}", sch, make_job("kakao-pc"))
            scheduler.save(entry)
            try:
                scheduler.register(entry)
                st.success(f"예약했습니다: {sch.describe()}")
            except Exception as exc:
                st.warning(f"설정은 저장했지만 Windows 작업 스케줄러 등록에 실패했습니다: {exc}")
        except ValueError as exc:
            st.error(str(exc))
    schedule_list([e for e in scheduler.list_entries() if e.job.kind == KIND_INBOUND])


def names_tool() -> None:
    """PC 카카오톡의 친구/채팅 이름을 읽어 와 엑셀 고객명과 짝을 맞추고 '카톡이름' 열을 채운다."""
    st.title("🗂️ 카톡 이름 정리")
    st.caption("거래처가 내 카톡에 어떤 이름으로 저장돼 있는지 읽어 와서, 엑셀 고객명과 자동으로 짝을 맞춥니다.")

    st.subheader("1. 카톡 이름 가져오기")
    tab = st.radio("어디서 읽을까요?", ["친구 목록", "채팅 목록"], horizontal=True)
    st.caption("PC 카카오톡을 켜고 메인 창에서 해당 탭을 눌러 둔 뒤 버튼을 누르세요. "
               "목록을 끝까지 내리며 읽으니 끝날 때까지 마우스를 움직이지 마세요.")
    exact_mode = st.toggle(
        "🎯 정확하게 읽기 (추천 · 채팅방을 하나씩 열어 창 제목으로 이름 확인)",
        value=True,
        help="목록에서 첫 방을 선택하고 Enter(열기) → 창 제목 읽기 → 닫기 → ↓(다음 방) 를 반복합니다. "
             "글자 인식을 쓰지 않아 이름이 정확하고 한국어 글자 인식 설치도 필요 없습니다. "
             "대신 방 하나에 1~2초 걸리고, 안 읽은 메시지가 '읽음' 처리됩니다. 끄면 화면 글자 인식으로 빠르게 읽습니다(글자가 틀릴 수 있음).",
    )
    if exact_mode:
        st.warning("채팅방을 열기 때문에 **안 읽은 메시지가 '읽음' 처리**됩니다(상대방 쪽 숫자 1이 사라짐). "
                   "읽는 동안 마우스가 자동으로 움직이니 **끝날 때까지 키보드·마우스를 건드리지 마세요.**")
    if st.button("PC 카카오톡에서 이름 읽어 오기"):
        try:
            target = "chats" if tab == "채팅 목록" else "friends"
            if exact_mode:
                status = st.empty()

                def progress(count: int, name: str, exact: bool) -> None:
                    status.info(f"{count}개 확인 중… {'✅' if exact else '⚠️'} {name}")

                pairs = extract_names_exact(target, on_progress=progress)
                st.session_state["kakao_names"] = [n for n, _ in pairs]
                unverified = [n for n, ok in pairs if not ok]
                status.success(f"{len(pairs)}개를 정확하게 읽었습니다."
                               + (f" 그중 {len(unverified)}개는 창이 열리지 않아 글자 인식 결과입니다: "
                                  + ", ".join(unverified[:10]) if unverified else ""))
            else:
                with st.spinner("카톡 목록을 읽는 중... (카톡 창이 잠깐 맨 앞에 고정됩니다)"):
                    found, method = extract_names(target)
                st.session_state["kakao_names"] = found
                st.info(f"'{method}' 방법으로 읽었습니다. 글자가 틀린 이름이 있을 수 있으니 아래 표에서 확인하세요. "
                        "정확한 이름이 필요하면 위의 '정확하게 읽기'를 켜고 다시 읽으세요.")
        except RuntimeError as exc:
            st.error(str(exc))
            if "한국어 글자 인식" in str(exc):
                st.session_state["need_ocr"] = True
    if st.session_state.get("need_ocr"):
        st.warning("카톡 이름을 읽으려면 Windows **한국어 글자 인식** 기능이 필요합니다(무료, 한 번만 설치). "
                   "아래 버튼을 누르면 '이 앱이 디바이스를 변경하도록 허용하시겠어요?' 창이 뜨는데 **예**를 누르세요. "
                   "파란 창에서 설치가 끝나면(1~5분) 아무 키나 눌러 닫고, 검은 창과 이 화면을 껐다가 실행.bat 으로 다시 켠 뒤 시도하세요.")
        if st.button("한국어 글자 인식 설치 (관리자 권한)"):
            try:
                install_korean_ocr()
                st.info("설치 창을 열었습니다. 파란 PowerShell 창에서 진행 상황을 확인하세요.")
            except RuntimeError as exc:
                st.error(str(exc))
    with st.expander("읽기가 안 될 때: 진단 정보 만들기"):
        st.caption("버튼을 누르면 카카오톡 창 구조를 글로 정리합니다. 대화 내용은 들어가지 않고, 목록에 보이는 이름 일부가 들어갈 수 있습니다. "
                   "내용을 복사해서 개발자에게 보내 주세요.")
        open_test = st.checkbox("첫 채팅방을 열어 보는 시험도 하기 (그 방은 '읽음' 처리됨)", value=True)
        if st.button("진단 정보 만들기"):
            try:
                with st.spinner("카톡 창 구조를 확인하는 중..."):
                    st.code(diagnose(open_test=open_test), language=None)
            except RuntimeError as exc:
                st.error(str(exc))
    pasted = st.text_area("또는 이름을 직접 붙여 넣기 (한 줄에 하나)", height=100)
    if pasted.strip():
        st.session_state["kakao_names"] = [n.strip() for n in pasted.splitlines() if n.strip()]

    target_col = "채팅방" if tab == "채팅 목록" else "카톡이름"
    names = st.session_state.get("kakao_names", [])
    if not names:
        st.stop()
    st.success(f"카톡 이름 {len(names)}개")
    st.caption("글자 인식은 가끔 글자를 틀리게 읽습니다(예: 기나글로벌 → 기나글로별). 발송은 **카톡에 보이는 이름과 정확히 같아야** 되므로, "
               "아래 표에서 틀린 글자는 칸을 눌러 고치고, 이름이 아닌 줄은 왼쪽 체크 후 휴지통으로 지우세요. 맨 아래 줄에서 추가도 됩니다.")
    names_df = st.data_editor(
        pd.DataFrame({target_col: names}),
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        height=min(38 + 35 * len(names), 420),
        key=f"names-list-{len(names)}-{hash(tuple(names))}",
    )
    names = list(dict.fromkeys(format_value(n) for n in names_df[target_col] if format_value(n)))
    st.download_button("이름 목록 엑셀로 받기", to_excel(pd.DataFrame({target_col: names})),
                       file_name=f"{target_col}목록.xlsx")

    st.subheader("2. 엑셀 고객명과 짝맞추기")
    upload = st.file_uploader("고객 엑셀(미수금 원장 또는 명단)", type=["xlsx", "xls", "csv"], key="names-upload")
    if upload is not None:
        frame = read_table(upload.getvalue(), upload.name)
    elif st.checkbox("샘플 데이터로 해보기", value=False) and SAMPLE_FILE.exists():
        frame = read_table(str(SAMPLE_FILE))
    else:
        st.stop()
    cols_ = list(frame.columns)
    name_c = st.selectbox("고객명 열", cols_, index=cols_.index("고객명") if "고객명" in cols_ else 0)
    customers_ = list(dict.fromkeys(format_value(v) for v in frame[name_c] if format_value(v)))
    existing = {}
    if target_col in frame.columns:
        for n, k in zip(frame[name_c], frame[target_col]):
            if format_value(k):
                existing[format_value(n)] = format_value(k)

    matches = match_names(customers_, names)
    level_icon = {"일치": "✅ 일치", "비슷함": "🟡 비슷함", "확인 필요": "🟠 확인 필요", "없음": "❌ 못 찾음"}
    table_ = pd.DataFrame({
        "고객명": [m.customer for m in matches],
        target_col: [existing.get(m.customer, m.kakao_name) for m in matches],
        "판정": ["📝 기존 값" if m.customer in existing else level_icon[m.level] for m in matches],
        "다른 후보": [", ".join(c for c in m.candidates if c != m.kakao_name) for m in matches],
    })
    st.write(f"자동으로 짝을 맞춘 결과입니다. **{target_col}** 칸을 눌러 직접 고쳐 쓸 수 있습니다(다른 후보를 참고하세요). "
             "카톡에 없는 고객은 비워 두세요(그러면 고객명으로 찾습니다).")
    if target_col == "채팅방":
        st.caption("채팅 목록에서 읽었으므로 결과를 **'채팅방' 열**에 저장합니다. 이 고객들은 해당 채팅방(단톡방 포함)으로 보내집니다.")
    edited_ = st.data_editor(
        table_,
        use_container_width=True,
        hide_index=True,
        disabled=["고객명", "판정", "다른 후보"],
        column_config={target_col: st.column_config.TextColumn(target_col, help="카톡에 보이는 이름 그대로")},
        key="names-editor",
    )
    counts = table_["판정"].value_counts()
    st.caption(" · ".join(f"{k} {v}명" for k, v in counts.items()))

    mapping = {r["고객명"]: r[target_col] or "" for _, r in edited_.iterrows()}
    out = frame.copy()
    out[target_col] = [mapping.get(format_value(n), "") for n in frame[name_c]]
    st.subheader("3. 저장")
    st.download_button(f"'{target_col}' 열을 채운 엑셀 받기", to_excel(out), type="primary",
                       file_name=(Path(upload.name).stem if upload is not None else "고객") + f"_{target_col}.xlsx")
    st.caption("받은 엑셀을 미수금 안내·공지사항 발송에 그대로 올리면 됩니다.")


if kind == MENU_NAMES:
    names_tool()
    st.stop()
if kind == MENU_INBOUND:
    inbound_tool()
    st.stop()

with st.sidebar:
    stop_button()
    st.header("발송 설정")
    mode = st.radio(
        "발송 방식",
        ["미리보기(실제 발송 안 함)", "PC 카카오톡(내 계정)", "카카오 알림톡", "문자(SMS/LMS)"],
        help="처음에는 '미리보기'로 충분히 확인한 뒤 실제 발송으로 바꾸세요.",
    )
    is_preview = mode.startswith("미리보기")
    is_kakao_pc = mode.startswith("PC 카카오톡")
    api_key = api_secret = sender_number = ""
    pf_id = template_id = ""
    sms_fallback = True
    subject = ""
    search_tab, gap, daily_cap = "친구 목록", (8, 15), 500
    find_mode = "ocr"
    if is_kakao_pc:
        st.warning("카카오 공식 기능이 아니라서 **계정이 제한될 수 있습니다.** "
                   "간격을 넉넉히 두고 하루 발송 수를 적게 유지하세요.")
        find_label = st.radio(
            "방 찾는 방식",
            ["글자 인식으로 고르기", "차례로 열어 확인 (글자 인식 없음)"],
            help="검색 결과에서 맞는 방을 고르는 방법입니다.\n\n"
                 "• 글자 인식으로 고르기: 결과 목록을 읽어 가장 비슷한 방만 엽니다. 다른 방을 거의 열지 않지만, 글자를 잘못 읽으면 못 찾을 수 있습니다.\n\n"
                 "• 차례로 열어 확인: 결과를 위에서부터 열어 창 제목이 같은지 확인합니다(최대 5줄). 확실하지만, 위에 있는 다른 방이 읽음 처리될 수 있습니다.",
        )
        find_mode = "keyboard" if find_label.startswith("차례로") else "ocr"
        search_tab = st.radio("고객명·카톡이름으로 찾을 곳", ["친구 목록", "채팅 목록"], horizontal=True,
                              help="엑셀의 '채팅방' 열에 값이 있는 고객은 이 설정과 관계없이 채팅 목록에서 그 채팅방을 찾습니다.")
        gap = st.slider("메시지 간격(초)", 3, 60, (8, 15), help="이 범위에서 매번 무작위로 기다립니다.")
        daily_cap = st.number_input("하루 최대 발송 수", 1, 500, 500, help="미수금·공지·테스트를 모두 합친 수")
        st.caption("발송하는 동안 카카오톡 창을 닫거나 로그아웃하지 마세요.")
    elif not is_preview:
        api_key = st.text_input("솔라피 API Key", os.getenv("SOLAPI_API_KEY", ""))
        api_secret = st.text_input("솔라피 API Secret", os.getenv("SOLAPI_API_SECRET", ""), type="password")
        sender_number = st.text_input("발신번호", os.getenv("SENDER_NUMBER", ""))
    if mode == "카카오 알림톡":
        pf_id = st.text_input("발신프로필 ID (pfId)", os.getenv("KAKAO_PF_ID", ""))
        template_id = st.text_input("알림톡 템플릿 ID", help="카카오 검수 승인된 템플릿의 ID")
        sms_fallback = st.checkbox("알림톡 실패 시 문자로 대체 발송", value=True)
        st.info("알림톡은 **카카오 승인을 받은 템플릿과 본문이 완전히 같아야** 발송됩니다.")
    elif mode == "문자(SMS/LMS)":
        subject = st.text_input("문자 제목(LMS)", kind)

st.title("💬 " + ("고객 공지사항 발송" if is_notice else "고객별 미수금 안내 발송"))
st.caption(
    "명단을 불러와 같은 공지를 고객마다 이름을 넣어 보냅니다."
    if is_notice
    else "엑셀 원장을 올리면 고객마다 미수 내역·금액이 자동으로 채워진 안내 메시지를 만들어 보냅니다."
)

# ───────────────────────── 1. 받는 사람 불러오기 ─────────────────────────
st.subheader("1. " + ("받는 사람 명단" if is_notice else "미수금 원장 불러오기"))
source = "엑셀 파일"
if is_notice:
    source = st.radio("명단 가져오기", ["엑셀 파일", "이름 직접 입력"], horizontal=True)
    if source == "엑셀 파일":
        st.write("고객명이 들어 있는 엑셀이면 아무거나 됩니다(미수금 원장도 가능). 같은 고객은 한 번만 보냅니다.")
else:
    st.write("거래 1건이 엑셀 1줄인 형식이면 됩니다. 같은 고객의 여러 줄은 자동으로 묶입니다.")

upload = None
if source == "이름 직접 입력":
    names_text = st.text_area("받는 사람 (한 줄에 한 명, 카톡에 보이는 이름 그대로)", height=150,
                              placeholder="KF - OKGUCHON(서명교)[ANGELES]\n송장방\nHARRY")
    phones_hint = "" if is_kakao_pc else " 문자/알림톡은 '이름,010-1234-5678' 처럼 번호도 적어 주세요."
    st.caption("PC 카카오톡은 이름만 있으면 됩니다." + phones_hint)
    typed_as_room = is_kakao_pc and st.radio(
        "입력한 이름은", ["카톡 친구 이름", "채팅방 이름(단톡방 포함)"], horizontal=True) != "카톡 친구 이름"
    rows = []
    for line in names_text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if parts and parts[0]:
            row = {"고객명": parts[0], "전화번호": parts[1] if len(parts) > 1 else ""}
            if typed_as_room:
                row["채팅방"] = parts[0]
            rows.append(row)
    if not rows:
        st.stop()
    df = pd.DataFrame(rows)
else:
    upload = st.file_uploader("엑셀(.xlsx) 또는 CSV", type=["xlsx", "xls", "csv"])
    use_sample = st.checkbox("샘플 데이터로 해보기", value=upload is None)
    if upload is not None:
        df = read_table(upload.getvalue(), upload.name)
    elif use_sample and SAMPLE_FILE.exists():
        df = read_table(str(SAMPLE_FILE))
    else:
        st.stop()
    with st.expander(f"원본 데이터 보기 ({len(df)}줄)"):
        st.dataframe(df, use_container_width=True)

cols = list(df.columns)


def pick(label: str, default: str, optional: bool = False):
    options = (["(사용 안 함)"] if optional else []) + cols
    index = options.index(default) if default in options else 0
    value = st.selectbox(label, options, index=index)
    return None if value == "(사용 안 함)" else value


require_phone = not (is_kakao_pc or is_preview and source == "이름 직접 입력")
amount_col = due_col = chat_col = room_col = None
line_template = DEFAULT_LINE_TEMPLATE
if source == "엑셀 파일":
    c = st.columns((2 if is_notice else 4) + (2 if is_kakao_pc else 0))
    with c[0]:
        name_col = pick("고객명 열", "고객명")
    with c[1]:
        phone_col = pick("전화번호 열", "전화번호", optional=not require_phone)
    i = 2
    if not is_notice:
        with c[2]:
            amount_col = pick("미수금액 열", "미수금")
        with c[3]:
            due_col = pick("납부기한 열", "납부기한", optional=True)
        i = 4
    if is_kakao_pc:
        with c[i]:
            chat_col = pick("카톡이름 열", "카톡이름", optional=True)
        with c[i + 1]:
            room_col = pick("채팅방 열", "채팅방", optional=True)
        st.caption("PC 카카오톡에서 받는 곳을 찾는 순서: **① '채팅방' 열에 값이 있으면 그 채팅방(단톡방 포함)** → "
                   "② '카톡이름' 열의 친구 이름 → ③ 고객명. 이름은 카톡에 보이는 그대로 적어 주세요.")
    if not is_notice:
        line_template = st.text_input(
            "미수내역 한 줄 서식",
            DEFAULT_LINE_TEMPLATE,
            help="엑셀 열 이름을 #{열이름} 으로 넣으세요. 고객의 미수 건마다 한 줄씩 만들어져 #{미수내역} 에 들어갑니다.",
        )
else:
    name_col, phone_col = "고객명", "전화번호"
    room_col = "채팅방" if "채팅방" in df.columns else None

try:
    if is_notice:
        customers = load_recipients(df, name_col, phone_col, require_phone=require_phone)
    else:
        customers = group_customers(
            df, ColumnMap(name_col, phone_col, amount_col, due_col), line_template, require_phone=require_phone
        )
except ValueError as exc:
    st.error(str(exc))
    st.stop()

# ───────────────────────── 2. 메시지 템플릿 ─────────────────────────
st.subheader("2. " + ("공지 문구" if is_notice else "안내 문구(템플릿)"))
template_dir = TEMPLATE_DIRS[kind]
template_dir.mkdir(parents=True, exist_ok=True)
NEW_TEMPLATE = "➕ 새 문구 (빈 칸에서 시작)"
choice_key = f"tpl-choice-{kind}"
if f"{choice_key}-pending" in st.session_state:  # 저장·삭제 직후 선택할 문구
    st.session_state[choice_key] = st.session_state.pop(f"{choice_key}-pending")
names_ = templates_store.list_templates(template_dir)
if st.session_state.get(choice_key) not in names_ + [NEW_TEMPLATE]:
    st.session_state.pop(choice_key, None)
choice = st.selectbox(f"저장된 문구 ({len(names_)}개)", names_ + [NEW_TEMPLATE], key=choice_key)
is_new = choice == NEW_TEMPLATE
base_text = "" if is_new else templates_store.load_template(template_dir, choice)
editor_key = f"tpl-{kind}-{choice}"

left, right = st.columns([3, 2])
with left:
    template = st.text_area("문구 편집 (#{변수} 자리에 고객별 값이 들어갑니다)", base_text, height=320, key=editor_key)
    if msg := st.session_state.pop("tpl-flash", None):
        st.toast(msg, icon="✅")
    changed = template != base_text
    if changed and not is_new:
        st.caption("✏️ 고친 내용이 아직 저장되지 않았습니다.")

    def _after_save(name: str, message: str) -> None:
        st.session_state.pop(f"tpl-{kind}-{name}", None)  # 편집칸을 저장된 내용으로 다시 읽게
        st.session_state.pop(editor_key, None)
        st.session_state[f"{choice_key}-pending"] = name
        st.session_state["tpl-flash"] = message
        st.rerun()

    b1, b2 = st.columns(2)
    with b1:
        if st.button("💾 덮어쓰기 저장", disabled=is_new or not changed, use_container_width=True,
                     help="지금 고른 문구를 고친 내용으로 바꿉니다."):
            templates_store.save_template(template_dir, choice, template, overwrite=True)
            _after_save(choice, f"'{choice}' 저장 완료")
    with b2:
        with st.popover("➕ 새 이름으로 저장", use_container_width=True):
            new_name = st.text_input("새 문구 이름", placeholder="예: 10월 휴무 안내", key=f"tpl-newname-{kind}")
            if st.button("저장", key=f"tpl-save-new-{kind}", disabled=not template.strip()):
                try:
                    saved = templates_store.save_template(template_dir, new_name, template)
                    _after_save(saved, f"'{saved}' 문구를 새로 저장했습니다.")
                except ValueError as exc:
                    st.error(str(exc))
    if not is_new:
        with st.popover("🗑️ 이 문구 삭제"):
            st.write(f"'{choice}' 문구를 지울까요? 지우면 되돌릴 수 없습니다.")
            if st.button("네, 삭제합니다", key=f"tpl-del-{kind}-{choice}", type="primary"):
                templates_store.delete_template(template_dir, choice)
                st.session_state[f"{choice_key}-pending"] = NEW_TEMPLATE
                st.session_state["tpl-flash"] = f"'{choice}' 문구를 삭제했습니다."
                st.rerun()
with right:
    st.markdown("**쓸 수 있는 변수**")
    auto_vars = ["고객명", "기준일"] if is_notice else ["고객명", "전화번호", "미수총액", "미수건수", "미수내역", "기준일"]
    if due_col:
        auto_vars += ["최초납부기한", "연체일수"]
    st.markdown("자동: " + " ".join(f"`#{{{v}}}`" for v in auto_vars))
    if source == "엑셀 파일":
        st.markdown("엑셀 열(고객별로 값이 하나인 열): " + " ".join(f"`#{{{c}}}`" for c in cols))
    used = find_variables(template)
    if used:
        st.markdown("이 문구에 쓰인 변수: " + ", ".join(used))

# 첨부: PC 카카오톡에서만 보낼 수 있다 (미리보기에서는 확인용으로 보여 준다)
attachments: list[str] = []
attach_col = None
if is_kakao_pc or is_preview:
    st.markdown("##### 📎 함께 보낼 사진·파일 (PC 카카오톡 전용, 선택)")
    a1, a2 = st.columns([3, 2])
    with a1:
        uploaded_files = st.file_uploader(
            "모든 받는 사람에게 같이 보낼 사진·파일 (여러 개 가능)", accept_multiple_files=True, key=f"att-{kind}"
        )
        if uploaded_files:
            att_dir = ROOT / "attachments" / dt.date.today().isoformat()
            att_dir.mkdir(parents=True, exist_ok=True)
            for f in uploaded_files:
                path = att_dir / Path(f.name).name
                path.write_bytes(f.getvalue())
                attachments.append(str(path))
            st.caption("문구를 보낸 뒤 " + ", ".join(Path(a).name for a in attachments) + " 을(를) 이어서 보냅니다.")
    with a2:
        if source == "엑셀 파일":
            attach_col = pick("고객별 첨부파일 열", "첨부파일", optional=True)
            st.caption("고객마다 다른 파일(예: 거래명세서)을 보낼 때, 엑셀에 파일 경로를 적은 열. "
                       "예) C:\\명세서\\마닐라푸드.pdf  (여러 개면 ; 로 구분)")
    if is_kakao_pc and (attachments or attach_col):
        st.warning("첨부를 보낼 때는 채팅방 창이 맨 앞으로 나오고 붙여넣기·Enter 가 자동으로 눌립니다. "
                   "**발송이 끝날 때까지 키보드·마우스를 건드리지 마세요.**")
    if is_preview and (attachments or attach_col):
        st.caption("※ 첨부는 'PC 카카오톡' 방식으로 보낼 때만 실제로 전송됩니다.")

# ───────────────────────── 3. 고객별 미리보기 ─────────────────────────
st.subheader("3. 고객별 미리보기")
today = dt.date.today()
drafts = campaign.prepare(customers, template, already_sent=history.sent_on(today, kind=kind),
                          chat_name_col=chat_col, room_col=room_col,
                          attachments=attachments, attach_col=attach_col)
if is_kakao_pc and search_tab == "채팅 목록":  # 기본 찾는 곳이 채팅 목록이면 모두 채팅방으로 표시·발송
    for d in drafts:
        d.search_tab = "chats"

if not drafts:
    st.warning("보낼 고객이 없습니다." if is_notice else "미수금이 남아 있는 고객이 없습니다.")
    st.stop()

columns = {
    "발송": [d.sendable and not d.already_sent_today for d in drafts],
    "고객명": [d.customer.name for d in drafts],
    ("받는 곳 (👤친구 · 💬채팅방)" if is_kakao_pc else "전화번호"): [
        d.destination if is_kakao_pc else d.customer.phone for d in drafts
    ],
}
if not is_notice:
    columns["미수총액"] = [format_value(d.customer.variables["미수총액"]) for d in drafts]
    columns["건수"] = [d.customer.variables["미수건수"] for d in drafts]
columns["글자수"] = [len(d.text) for d in drafts]
if any(d.attachments for d in drafts):
    columns["첨부"] = [f"📎 {len(d.attachments)}" if d.attachments else "" for d in drafts]
columns["상태"] = [
    "⚠️ " + " / ".join(d.problems) if d.problems else (f"오늘 이미 {kind} 보냄" if d.already_sent_today else "정상")
    for d in drafts
]
table = pd.DataFrame(columns)
edited = st.data_editor(
    table,
    use_container_width=True,
    hide_index=True,
    disabled=[c for c in table.columns if c != "발송"],
    key=f"targets-{kind}",
)

m1, m2, m3 = st.columns(3)
m1.metric("받는 사람", f"{len(drafts)}명")
m2.metric("발송 선택", f"{int(edited['발송'].sum())}명")
m3.metric("확인 필요", f"{sum(1 for d in drafts if d.problems)}명")

labels = [f"{d.customer.name} ({d.destination if is_kakao_pc else d.customer.phone})" for d in drafts]
preview_name = st.selectbox("메시지 전체 보기", labels)
preview = drafts[labels.index(preview_name)]
p1, p2 = st.columns([2, 3])
with p1:
    st.text_area("받는 사람에게 보이는 내용", preview.text, height=360, disabled=True)
    st.caption(f"{len(preview.text)} / {ALIMTALK_MAX_LENGTH}자")
    if preview.attachments:
        st.markdown("**📎 이어서 보낼 파일**  \n" + "  \n".join(f"- {Path(a).name}" for a in preview.attachments))
if not is_notice:
    with p2:
        st.markdown("**이 고객의 미수 건**")
        st.dataframe(preview.customer.rows.drop(columns=[c for c in preview.customer.rows.columns if c.startswith("_")]),
                     use_container_width=True, hide_index=True)

# ───────────────────────── 4. 발송 ─────────────────────────
st.subheader("4. 발송")
selected = [d for d, flag in zip(drafts, edited["발송"]) if flag and d.sendable]
skipped = [d for d, flag in zip(drafts, edited["발송"]) if flag and not d.sendable]
if skipped:
    st.warning(f"문제가 있는 {len(skipped)}명은 선택해도 발송되지 않습니다: " + ", ".join(d.customer.name for d in skipped))

# 테스트 모드: 고객 대신 나에게 보내 본다
test_to = None
test_tab = ""
test_count = 3
if not is_preview:
    test_mode = st.toggle("🧪 테스트 모드 — 고객에게 보내지 않고 **나에게** 보내 보기", value=True,
                          help="선택한 고객들의 메시지를 전부 아래 '나'에게 보냅니다. 각 메시지 맨 위에 원래 받을 고객 이름이 붙습니다.")
    if test_mode:
        t1, t2 = st.columns([2, 1])
        with t1:
            if is_kakao_pc:
                test_to = st.text_input("테스트 받을 곳 (본인·직원 카톡 이름 또는 테스트용 채팅방 이름)").strip()
                if st.radio("테스트 받을 곳은", ["카톡 친구 이름", "채팅방 이름"], horizontal=True) == "채팅방 이름":
                    test_tab = "chats"
            else:
                test_to = "".join(ch for ch in st.text_input("테스트 받을 휴대폰 번호") if ch.isdigit())
        with t2:
            test_count = st.number_input("몇 건만 보내 볼까요?", 1, 20, min(3, max(len(selected), 1)))
        selected = selected[: int(test_count)]
        if is_kakao_pc and test_to:
            if st.button("🔍 발송 단계 점검 (테스트 받을 곳에 점검 메시지 1건)",
                         help="검색칸 찾기 → 채팅방 열기 → 입력칸 찾기 → 전송 확인을 차례로 하며 어디서 멈추는지 보여 줍니다."):
                try:
                    with st.spinner("점검 중..."):
                        steps = check_send(test_to, test_tab or ("chats" if search_tab == "채팅 목록" else "friends"),
                                           find_mode=find_mode)
                    st.code("\n".join(steps), language=None)
                except RuntimeError as exc:
                    st.error(str(exc))
        if not test_to:
            st.info("테스트 받을 " + ("카톡 이름" if is_kakao_pc else "휴대폰 번호") + "을 입력해 주세요.")
        else:
            st.success(f"테스트 모드: 고객 {len(selected)}명의 메시지를 **{test_to}** 에게 보냅니다. 고객에게는 가지 않습니다.")

if is_kakao_pc:
    sent_today = history.count_sent(today, KakaoPCSender.label)
    remaining = max(int(daily_cap) - sent_today, 0)
    st.info(f"오늘 PC 카카오톡 발송: {sent_today}건 / 한도 {int(daily_cap)}건 (남은 {remaining}건)")
    if len(selected) > remaining:
        st.warning(f"하루 한도 때문에 앞에서부터 {remaining}명만 발송합니다.")
        selected = selected[:remaining]


def make_sender():
    if is_preview:
        return DryRunSender()
    if is_kakao_pc:
        driver = Win32KakaoDriver(search_tab="chats" if search_tab == "채팅 목록" else "friends", find_mode=find_mode)
        return KakaoPCSender(driver, min_interval=gap[0], max_interval=gap[1])
    return SolapiSender(
        api_key, api_secret, sender_number,
        mode="alimtalk" if mode == "카카오 알림톡" else "sms",
        pf_id=pf_id, template_id=template_id, sms_fallback=sms_fallback, subject=subject,
    )


is_test = test_to is not None
waiting_for_test_target = is_test and not test_to
if is_preview or is_test:
    confirm = True
    button_label = f"{len(selected)}건 " + ("미리보기 발송" if is_preview else "테스트 발송 (나에게)")
else:
    confirm = st.checkbox(
        f"⚠️ 실제 고객 {len(selected)}명에게 {kind}을(를) 보냅니다" + ("" if is_kakao_pc else " (비용이 발생합니다)")
    )
    button_label = f"{len(selected)}명에게 {mode} 실제 발송"

if st.button(button_label, type="primary", disabled=not selected or not confirm or waiting_for_test_target):
    try:
        sender = make_sender()
    except (ValueError, RuntimeError) as exc:
        st.error(str(exc))
        st.stop()

    control.clear_stop()
    if is_kakao_pc:
        st.info("멈추려면 왼쪽 **⏹ 발송 중지** 버튼을 누르거나, 키보드 **ESC** 를 1초 정도 누르고 계세요. 지금 보내는 1건을 마치고 멈춥니다.")
    bar = st.progress(0.0, text="발송 준비 중...")

    def on_result(done: int, total: int, r) -> None:
        bar.progress(done / total, text=f"{done}/{total} {'✅' if r.ok else '❌'} {r.detail}")

    results = campaign.send(selected, sender, on_result=on_result, kind=kind, test_to=test_to or None,
                            test_tab=test_tab)
    ok = sum(r.ok for r in results)
    (st.success if ok == len(results) else st.warning)(
        ("[테스트] " if is_test else "") + f"성공 {ok}건 / 실패 {len(results) - ok}건"
    )
    by_key = {d.customer.key: d for d in selected}
    failed = [r for r in results if not r.ok and "발송 중지됨" not in r.detail and "발송 중단" not in r.detail]
    if failed:
        with st.expander(f"❌ 실패 {len(failed)}건 상세 보기 (잘리지 않은 전체 내용)", expanded=True):
            st.code("\n".join(f"{by_key[r.key].customer.name} → {r.detail}" for r in failed), language=None)
    unverified = [r for r in results if "전송 확인 필요" in r.detail]
    if unverified:
        st.warning(f"{len(unverified)}건은 Enter 는 눌렀지만 전송됐는지 확인하지 못했습니다(중복을 막으려고 다시 보내지 않았습니다). "
                   "해당 채팅방에서 직접 확인해 주세요.")
    attach_warnings = sum("⚠️ 첨부 실패" in r.detail for r in results)
    if attach_warnings:
        st.warning(f"{attach_warnings}건은 글은 보냈지만 첨부를 보내지 못했습니다. 아래 상세를 확인하고 해당 고객에게만 파일을 따로 보내 주세요.")
    by_key = {d.customer.key: d for d in selected}
    st.dataframe(
        pd.DataFrame([{"고객명": by_key[r.key].customer.name, "결과": "성공" if r.ok else "실패", "상세": r.detail}
                      for r in results]),
        use_container_width=True, hide_index=True,
    )
    if is_test and ok:
        st.info("받은 메시지를 확인해 보고 괜찮으면, 위의 테스트 모드를 끄고 실제로 발송하세요.")
    if not is_kakao_pc and not is_preview:
        st.caption("'접수 완료'는 솔라피가 요청을 받았다는 뜻입니다. 최종 도착 여부는 솔라피 콘솔에서 확인하세요.")

# ───────────────────────── 5. 예약 발송 ─────────────────────────
st.subheader("5. ⏰ 예약 발송")
st.caption("지금 화면의 설정(문구·열 지정·발송 방식·첨부·테스트 모드)을 저장해 두고, 정해진 시간에 Windows 가 자동으로 보냅니다. "
           "브라우저 화면은 꺼져 있어도 되지만 **PC 는 켜져 있고 로그인(화면 잠금 해제) 상태**여야 하며, PC 카카오톡도 로그인돼 있어야 합니다.")
with st.expander("새 예약 만들기", expanded=False):
    sched_name = st.text_input("예약 이름", f"{kind} {dt.date.today():%m월}")
    repeat_label = st.radio("반복", ["한 번", "매일", "매주", "매월"], horizontal=True)
    repeat = {"한 번": "once", "매일": "daily", "매주": "weekly", "매월": "monthly"}[repeat_label]
    r1, r2 = st.columns(2)
    weekdays_sel: list[int] = []
    month_day = 1
    with r1:
        if repeat == "once":
            run_date = st.date_input("날짜", dt.date.today() + dt.timedelta(days=1))
        elif repeat == "weekly":
            weekdays_sel = st.multiselect("요일", list(range(7)), default=[0], format_func=lambda i: scheduler.WEEKDAYS[i])
        elif repeat == "monthly":
            month_day = st.number_input("매월 며칠", 1, 31, 25)
    with r2:
        h_col, m_col = st.columns(2)
        hour = h_col.selectbox("시", list(range(24)), index=9, format_func=lambda h: f"{'오전' if h < 12 else '오후'} {h % 12 or 12}시")
        minute = m_col.selectbox("분", list(range(0, 60, 5)), format_func=lambda m: f"{m:02d}분")
        run_time = dt.time(hour, minute)
    if repeat == "monthly" and month_day > 28:
        st.caption(f"{month_day}일이 없는 달에는 보내지 않습니다.")

    ledger_mode = "사본"
    ledger_path = ""
    if source == "엑셀 파일":
        ledger_mode = st.radio(
            "엑셀은 어떻게 할까요?",
            ["지금 올린 엑셀을 저장해서 사용", "이 경로의 엑셀을 예약 시간에 다시 읽기 (매번 최신 원장)"],
            help="반복 예약이라면 원장 파일을 늘 같은 위치에 덮어써 두고 두 번째를 고르세요. 예약 시간 기준의 미수금으로 보냅니다.",
        )
        if ledger_mode.startswith("이 경로"):
            ledger_path = st.text_input("엑셀 파일 경로", placeholder=r"예) C:\미수금\원장.xlsx").strip().strip('"')

    st.caption("예약 발송은 3번 표의 체크와 관계없이, 예약 시간에 **보낼 수 있는 모든 고객**에게 보냅니다"
               "(문제가 있거나 그날 이미 보낸 고객은 자동 제외)."
               + (f" 지금은 테스트 모드라 **{test_to}** 에게 {int(test_count)}건만 갑니다." if test_to else ""))
    if mode in ("카카오 알림톡", "문자(SMS/LMS)") and not os.getenv("SOLAPI_API_KEY"):
        st.warning("알림톡·문자 예약은 .env 파일의 솔라피 키를 씁니다. .env 에 키를 먼저 저장해 주세요.")

    if st.button("⏰ 예약 저장", type="primary", disabled=not template.strip() or (test_to == "")):
        try:
            at = dt.datetime.combine(run_date if repeat == "once" else dt.date.today(), run_time)
            sch = scheduler.Schedule(repeat, at.isoformat(timespec="minutes"), weekdays_sel, int(month_day))
            sch.validate()
            if source == "엑셀 파일" and ledger_mode.startswith("이 경로") and not Path(ledger_path).is_file():
                raise ValueError(f"엑셀 파일을 찾을 수 없습니다: {ledger_path or '(경로 없음)'}")
            job = Job(
                kind=kind, mode=MODE_KEYS[mode], template=template,
                ledger=ledger_path if ledger_path else (str(SAMPLE_FILE) if source == "엑셀 파일" and upload is None else ""),
                names=df.to_dict("records") if source == "이름 직접 입력" else [],
                name_col=name_col, phone_col=phone_col, amount_col=amount_col, due_col=due_col,
                line_template=line_template, chat_col=chat_col, room_col=room_col, attach_col=attach_col,
                attachments=attachments,
                search_tab="chats" if search_tab == "채팅 목록" else "friends", find_mode=find_mode,
                gap=tuple(gap), daily_cap=int(daily_cap),
                template_id=template_id, sms_fallback=sms_fallback, subject=subject,
                test_to=test_to or "", test_tab=test_tab, test_count=int(test_count),
            )
            entry = scheduler.Entry(scheduler.new_id(), sched_name.strip() or kind, sch, job)
            snapshot = upload.getvalue() if (upload is not None and not ledger_path) else None
            scheduler.save(entry, ledger_bytes=snapshot, ledger_name=upload.name if upload is not None else "원장.xlsx")
            try:
                scheduler.register(entry)
                st.success(f"예약했습니다: {entry.name} — {sch.describe()}")
            except Exception as exc:
                st.warning(f"설정은 저장했지만 Windows 작업 스케줄러 등록에 실패했습니다: {exc}")
        except ValueError as exc:
            st.error(str(exc))

schedule_list([e for e in scheduler.list_entries() if e.job.kind != KIND_INBOUND])

if history.DEFAULT_LOG.exists():
    st.download_button(
        "발송 이력 내려받기 (CSV)",
        history.DEFAULT_LOG.read_bytes(),
        file_name="send_log.csv",
        mime="text/csv",
    )
