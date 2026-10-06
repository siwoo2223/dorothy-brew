"""미수금 안내 자동 발송 (Streamlit 화면)

실행: streamlit run app.py
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from receivables import campaign, history
from receivables.loader import DEFAULT_LINE_TEMPLATE, ColumnMap, group_customers, read_table
from receivables.sender import DryRunSender, SolapiSender
from receivables.template import ALIMTALK_MAX_LENGTH, find_variables, format_value

ROOT = Path(__file__).parent
TEMPLATE_DIR = ROOT / "templates"
SAMPLE_FILE = ROOT / "samples" / "미수금_샘플.xlsx"

load_dotenv(ROOT / ".env")
st.set_page_config(page_title="미수금 안내 발송", page_icon="💬", layout="wide")
st.title("💬 고객별 미수금 안내 발송")
st.caption("엑셀 원장을 올리면 고객마다 미수 내역·금액이 자동으로 채워진 안내 메시지를 만들어 보냅니다.")

# ───────────────────────── 사이드바: 발송 설정 ─────────────────────────
with st.sidebar:
    st.header("발송 설정")
    mode = st.radio(
        "발송 방식",
        ["미리보기(실제 발송 안 함)", "카카오 알림톡", "문자(SMS/LMS)"],
        help="처음에는 '미리보기'로 충분히 확인한 뒤 실제 발송으로 바꾸세요.",
    )
    api_key = st.text_input("솔라피 API Key", os.getenv("SOLAPI_API_KEY", ""))
    api_secret = st.text_input("솔라피 API Secret", os.getenv("SOLAPI_API_SECRET", ""), type="password")
    sender_number = st.text_input("발신번호", os.getenv("SENDER_NUMBER", ""))
    pf_id = template_id = ""
    sms_fallback = True
    subject = ""
    if mode == "카카오 알림톡":
        pf_id = st.text_input("발신프로필 ID (pfId)", os.getenv("KAKAO_PF_ID", ""))
        template_id = st.text_input("알림톡 템플릿 ID", help="카카오 검수 승인된 템플릿의 ID")
        sms_fallback = st.checkbox("알림톡 실패 시 문자로 대체 발송", value=True)
        st.info("알림톡은 **카카오 승인을 받은 템플릿과 본문이 완전히 같아야** 발송됩니다. "
                "아래 템플릿 문구를 승인받은 문구와 똑같이 맞춰 주세요.")
    elif mode == "문자(SMS/LMS)":
        subject = st.text_input("문자 제목(LMS)", "미수금 안내")

# ───────────────────────── 1. 엑셀 불러오기 ─────────────────────────
st.subheader("1. 미수금 원장 불러오기")
st.write("거래 1건이 엑셀 1줄인 형식이면 됩니다. 같은 고객의 여러 줄은 자동으로 묶입니다.")
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


c1, c2, c3, c4 = st.columns(4)
with c1:
    name_col = pick("고객명 열", "고객명")
with c2:
    phone_col = pick("전화번호 열", "전화번호")
with c3:
    amount_col = pick("미수금액 열", "미수금")
with c4:
    due_col = pick("납부기한 열", "납부기한", optional=True)

line_template = st.text_input(
    "미수내역 한 줄 서식",
    DEFAULT_LINE_TEMPLATE,
    help="엑셀 열 이름을 #{열이름} 으로 넣으세요. 고객의 미수 건마다 한 줄씩 만들어져 #{미수내역} 에 들어갑니다.",
)

try:
    customers = group_customers(df, ColumnMap(name_col, phone_col, amount_col, due_col), line_template)
except ValueError as exc:
    st.error(str(exc))
    st.stop()

# ───────────────────────── 2. 메시지 템플릿 ─────────────────────────
st.subheader("2. 안내 문구(템플릿)")
template_files = sorted(TEMPLATE_DIR.glob("*.txt"))
choice = st.selectbox("저장된 템플릿", [p.stem for p in template_files])
base_text = (TEMPLATE_DIR / f"{choice}.txt").read_text(encoding="utf-8") if choice else ""

left, right = st.columns([3, 2])
with left:
    template = st.text_area("문구 편집 (#{변수} 자리에 고객별 값이 들어갑니다)", base_text, height=320, key=f"tpl-{choice}")
    save_name = st.text_input("이 문구를 새 이름으로 저장", "")
    if st.button("템플릿 저장") and save_name.strip():
        (TEMPLATE_DIR / f"{save_name.strip()}.txt").write_text(template, encoding="utf-8")
        st.success(f"'{save_name.strip()}' 저장 완료")
with right:
    st.markdown("**쓸 수 있는 변수**")
    auto_vars = ["고객명", "전화번호", "미수총액", "미수건수", "미수내역", "기준일"]
    if due_col:
        auto_vars += ["최초납부기한", "연체일수"]
    st.markdown("자동 계산: " + " ".join(f"`#{{{v}}}`" for v in auto_vars))
    st.markdown("엑셀 열(고객별로 값이 하나인 열): " + " ".join(f"`#{{{c}}}`" for c in cols))
    used = find_variables(template)
    if used:
        st.markdown("이 템플릿에 쓰인 변수: " + ", ".join(used))

# ───────────────────────── 3. 고객별 미리보기 ─────────────────────────
st.subheader("3. 고객별 미리보기")
today = dt.date.today()
drafts = campaign.prepare(customers, template, already_sent=history.sent_on(today))

if not drafts:
    st.warning("미수금이 남아 있는 고객이 없습니다.")
    st.stop()

table = pd.DataFrame(
    {
        "발송": [d.sendable and not d.already_sent_today for d in drafts],
        "고객명": [d.customer.name for d in drafts],
        "전화번호": [d.customer.phone for d in drafts],
        "미수총액": [format_value(d.customer.variables["미수총액"]) for d in drafts],
        "건수": [d.customer.variables["미수건수"] for d in drafts],
        "글자수": [len(d.text) for d in drafts],
        "상태": [
            "⚠️ " + " / ".join(d.problems) if d.problems
            else ("오늘 이미 발송함" if d.already_sent_today else "정상")
            for d in drafts
        ],
    }
)
edited = st.data_editor(
    table,
    use_container_width=True,
    hide_index=True,
    disabled=[c for c in table.columns if c != "발송"],
    key="targets",
)

problem_count = sum(1 for d in drafts if d.problems)
m1, m2, m3 = st.columns(3)
m1.metric("안내 대상 고객", f"{len(drafts)}명")
m2.metric("발송 선택", f"{int(edited['발송'].sum())}명")
m3.metric("확인 필요", f"{problem_count}명")

preview_name = st.selectbox("메시지 전체 보기", [f"{d.customer.name} ({d.customer.phone})" for d in drafts])
preview = drafts[[f"{d.customer.name} ({d.customer.phone})" for d in drafts].index(preview_name)]
p1, p2 = st.columns([2, 3])
with p1:
    st.text_area("받는 사람에게 보이는 내용", preview.text, height=360, disabled=True)
    st.caption(f"{len(preview.text)} / {ALIMTALK_MAX_LENGTH}자")
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

confirm = mode.startswith("미리보기") or st.checkbox(f"{len(selected)}명에게 실제로 발송합니다 (비용이 발생합니다)")
if st.button(f"{len(selected)}명에게 {mode} 발송", type="primary", disabled=not selected or not confirm):
    try:
        if mode.startswith("미리보기"):
            sender = DryRunSender()
        else:
            sender = SolapiSender(
                api_key, api_secret, sender_number,
                mode="alimtalk" if mode == "카카오 알림톡" else "sms",
                pf_id=pf_id, template_id=template_id, sms_fallback=sms_fallback, subject=subject,
            )
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    with st.spinner("발송 중..."):
        results = campaign.send(selected, sender)
    ok = sum(r.ok for r in results)
    (st.success if ok == len(results) else st.warning)(f"성공 {ok}건 / 실패 {len(results) - ok}건")
    st.dataframe(
        pd.DataFrame([{"전화번호": r.to, "결과": "성공" if r.ok else "실패", "상세": r.detail} for r in results]),
        use_container_width=True, hide_index=True,
    )
    st.caption("'접수 완료'는 솔라피가 요청을 받았다는 뜻입니다. 최종 도착 여부는 솔라피 콘솔에서 확인하세요.")

if history.DEFAULT_LOG.exists():
    st.download_button(
        "발송 이력 내려받기 (CSV)",
        history.DEFAULT_LOG.read_bytes(),
        file_name="send_log.csv",
        mime="text/csv",
    )
