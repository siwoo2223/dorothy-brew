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

from receivables import campaign, history
from receivables.kakao_names import diagnose, extract_names, match_names
from receivables.kakao_pc import KakaoPCSender, Win32KakaoDriver
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
TEMPLATE_DIRS = {KIND_RECEIVABLE: ROOT / "templates" / "미수금", KIND_NOTICE: ROOT / "templates" / "공지"}

load_dotenv(ROOT / ".env")
st.set_page_config(page_title="미수금·공지 발송", page_icon="💬", layout="wide")

# ───────────────────────── 사이드바: 발송 설정 ─────────────────────────
with st.sidebar:
    st.header("메뉴")
    kind = st.radio("메뉴", [KIND_RECEIVABLE, KIND_NOTICE, MENU_NAMES], label_visibility="collapsed")
    is_notice = kind == KIND_NOTICE


def to_excel(frame: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    frame.to_excel(buf, index=False)
    return buf.getvalue()


def names_tool() -> None:
    """PC 카카오톡의 친구/채팅 이름을 읽어 와 엑셀 고객명과 짝을 맞추고 '카톡이름' 열을 채운다."""
    st.title("🗂️ 카톡 이름 정리")
    st.caption("거래처가 내 카톡에 어떤 이름으로 저장돼 있는지 읽어 와서, 엑셀 고객명과 자동으로 짝을 맞춥니다.")

    st.subheader("1. 카톡 이름 가져오기")
    tab = st.radio("어디서 읽을까요?", ["친구 목록", "채팅 목록"], horizontal=True)
    st.caption("PC 카카오톡을 켜고 메인 창에서 해당 탭을 눌러 둔 뒤 버튼을 누르세요. "
               "목록을 끝까지 내리며 읽으니 끝날 때까지 마우스를 움직이지 마세요.")
    if st.button("PC 카카오톡에서 이름 읽어 오기"):
        try:
            with st.spinner("카톡 목록을 읽는 중... (카톡 창이 잠깐 맨 앞에 고정됩니다)"):
                found, method = extract_names("chats" if tab == "채팅 목록" else "friends")
            st.session_state["kakao_names"] = found
            st.info(f"'{method}' 방법으로 읽었습니다. 이름이 아닌 글자가 섞였을 수 있으니 아래 목록을 확인하세요.")
        except RuntimeError as exc:
            st.error(str(exc))
    with st.expander("읽기가 안 될 때: 진단 정보 만들기"):
        st.caption("버튼을 누르면 카카오톡 창 구조를 글로 정리합니다. 대화 내용은 들어가지 않고, 목록에 보이는 이름 일부가 들어갈 수 있습니다. "
                   "내용을 복사해서 개발자에게 보내 주세요.")
        if st.button("진단 정보 만들기"):
            try:
                with st.spinner("카톡 창 구조를 확인하는 중..."):
                    st.code(diagnose(), language=None)
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
    with st.expander("읽어 온 이름 보기"):
        st.dataframe(pd.DataFrame({target_col: names}), use_container_width=True, hide_index=True)
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
    st.write(f"자동으로 짝을 맞춘 결과입니다. **{target_col}** 칸을 눌러 맞는 이름으로 고칠 수 있습니다. "
             "카톡에 없는 고객은 비워 두세요(그러면 고객명으로 찾습니다).")
    if target_col == "채팅방":
        st.caption("채팅 목록에서 읽었으므로 결과를 **'채팅방' 열**에 저장합니다. 이 고객들은 해당 채팅방(단톡방 포함)으로 보내집니다.")
    edited_ = st.data_editor(
        table_,
        use_container_width=True,
        hide_index=True,
        disabled=["고객명", "판정", "다른 후보"],
        column_config={target_col: st.column_config.SelectboxColumn(
            target_col, options=[""] + list(dict.fromkeys(names + list(existing.values()))))},
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

with st.sidebar:

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
    search_tab, gap, daily_cap = "친구 목록", (8, 15), 50
    if is_kakao_pc:
        st.warning("카카오 공식 기능이 아니라서 **계정이 제한될 수 있습니다.** "
                   "간격을 넉넉히 두고 하루 발송 수를 적게 유지하세요.")
        search_tab = st.radio("고객명·카톡이름으로 찾을 곳", ["친구 목록", "채팅 목록"], horizontal=True,
                              help="엑셀의 '채팅방' 열에 값이 있는 고객은 이 설정과 관계없이 채팅 목록에서 그 채팅방을 찾습니다.")
        gap = st.slider("메시지 간격(초)", 3, 60, (8, 15), help="이 범위에서 매번 무작위로 기다립니다.")
        daily_cap = st.number_input("하루 최대 발송 수", 1, 300, 50, help="미수금·공지·테스트를 모두 합친 수")
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

if source == "이름 직접 입력":
    names_text = st.text_area("받는 사람 (한 줄에 한 명, 카톡에 보이는 이름 그대로)", height=150,
                              placeholder="카페하늘\n베이커리온\n오피스커피")
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
template_files = sorted(template_dir.glob("*.txt"))
choice = st.selectbox("저장된 문구", [p.stem for p in template_files])
base_text = (template_dir / f"{choice}.txt").read_text(encoding="utf-8") if choice else ""

left, right = st.columns([3, 2])
with left:
    template = st.text_area("문구 편집 (#{변수} 자리에 고객별 값이 들어갑니다)", base_text, height=320,
                            key=f"tpl-{kind}-{choice}")
    save_name = st.text_input("이 문구를 새 이름으로 저장", "")
    if st.button("문구 저장") and save_name.strip():
        (template_dir / f"{save_name.strip()}.txt").write_text(template, encoding="utf-8")
        st.success(f"'{save_name.strip()}' 저장 완료")
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

# ───────────────────────── 3. 고객별 미리보기 ─────────────────────────
st.subheader("3. 고객별 미리보기")
today = dt.date.today()
drafts = campaign.prepare(customers, template, already_sent=history.sent_on(today, kind=kind),
                          chat_name_col=chat_col, room_col=room_col)
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
        driver = Win32KakaoDriver(search_tab="chats" if search_tab == "채팅 목록" else "friends")
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
    st.dataframe(
        pd.DataFrame([{"고객명": by_key[r.key].customer.name, "결과": "성공" if r.ok else "실패", "상세": r.detail}
                      for r in results]),
        use_container_width=True, hide_index=True,
    )
    if is_test and ok:
        st.info("받은 메시지를 확인해 보고 괜찮으면, 위의 테스트 모드를 끄고 실제로 발송하세요.")
    if not is_kakao_pc and not is_preview:
        st.caption("'접수 완료'는 솔라피가 요청을 받았다는 뜻입니다. 최종 도착 여부는 솔라피 콘솔에서 확인하세요.")

if history.DEFAULT_LOG.exists():
    st.download_button(
        "발송 이력 내려받기 (CSV)",
        history.DEFAULT_LOG.read_bytes(),
        file_name="send_log.csv",
        mime="text/csv",
    )
