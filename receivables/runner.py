"""발송 작업 한 건을 처음부터 끝까지 실행한다 (명령줄 실행·예약 실행에서 같이 쓴다).

화면(app.py)에서 고른 설정을 Job 으로 담아 두면, 나중에(예약 시간에) 엑셀을 다시 읽어
그때 기준의 고객·미수금으로 메시지를 만들어 보낸다.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from . import campaign, history
from .loader import DEFAULT_LINE_TEMPLATE, ColumnMap, group_customers, load_recipients, read_table

KIND_RECEIVABLE = "미수금 안내"
KIND_NOTICE = "공지사항"
MODES = ("dry-run", "kakao-pc", "alimtalk", "sms")


@dataclass
class Job:
    kind: str = KIND_RECEIVABLE
    mode: str = "dry-run"
    template: str = ""
    ledger: str = ""  # 엑셀/CSV 경로 (예약 시간에 다시 읽음)
    names: list[dict] = field(default_factory=list)  # 엑셀 대신 직접 입력한 명단 [{"고객명":..,"전화번호":..,"채팅방":..}]
    name_col: str = "고객명"
    phone_col: str | None = "전화번호"
    amount_col: str | None = "미수금"
    due_col: str | None = "납부기한"
    line_template: str = DEFAULT_LINE_TEMPLATE
    chat_col: str | None = "카톡이름"
    room_col: str | None = "채팅방"
    attach_col: str | None = None
    attachments: list[str] = field(default_factory=list)
    # PC 카카오톡
    search_tab: str = "friends"
    find_mode: str = "ocr"  # 검색 결과에서 방 고르기: "ocr" | "keyboard"
    gap: tuple[float, float] = (8, 15)
    daily_cap: int = 500
    # 솔라피(알림톡·문자) — 키는 .env 에서 읽는다
    template_id: str = ""
    sms_fallback: bool = True
    subject: str = ""
    # 테스트 모드
    test_to: str = ""
    test_tab: str = ""
    test_count: int = 3
    resend: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["gap"] = list(self.gap)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        if "gap" in known:
            known["gap"] = tuple(known["gap"])
        return cls(**known)


@dataclass
class RunSummary:
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    warnings: int = 0
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.failed == 0 and not self.message


def _table(job: Job) -> pd.DataFrame:
    if job.names:
        return pd.DataFrame(job.names)
    if not job.ledger or not Path(job.ledger).is_file():
        raise ValueError(f"엑셀 파일을 찾을 수 없습니다: {job.ledger}")
    return read_table(job.ledger)


def make_sender(job: Job):
    """job.mode 에 맞는 발송기. 실패하면 ValueError/RuntimeError."""
    from .sender import DryRunSender, SolapiSender

    if job.mode == "dry-run":
        return DryRunSender()
    if job.mode == "kakao-pc":
        from .kakao_pc import KakaoPCSender, Win32KakaoDriver

        return KakaoPCSender(Win32KakaoDriver(search_tab=job.search_tab, find_mode=job.find_mode),
                             min_interval=job.gap[0], max_interval=job.gap[1])
    return SolapiSender(
        os.getenv("SOLAPI_API_KEY", ""),
        os.getenv("SOLAPI_API_SECRET", ""),
        os.getenv("SENDER_NUMBER", ""),
        mode="alimtalk" if job.mode == "alimtalk" else "sms",
        pf_id=os.getenv("KAKAO_PF_ID", ""),
        template_id=job.template_id,
        sms_fallback=job.sms_fallback,
        subject=job.subject,
    )


def run_job(
    job: Job,
    log: Callable[[str], None] = print,
    sender=None,
    log_path: Path = history.DEFAULT_LOG,
    today: dt.date | None = None,
) -> RunSummary:
    """엑셀을 읽어 메시지를 만들고 보낸다. sender 를 주면 그것으로 보낸다(테스트용)."""
    today = today or dt.date.today()
    kakao_pc = job.mode == "kakao-pc"
    try:
        df = _table(job)
        cols = set(df.columns)
        pick = lambda c: c if c and c in cols else None  # noqa: E731 - 엑셀에 없는 열은 쓰지 않음
        if job.kind == KIND_NOTICE:
            customers = load_recipients(df, job.name_col, pick(job.phone_col), today=today,
                                        require_phone=not kakao_pc)
        else:
            customers = group_customers(
                df, ColumnMap(job.name_col, pick(job.phone_col), job.amount_col, pick(job.due_col)),
                job.line_template, today=today, require_phone=not kakao_pc,
            )
    except ValueError as exc:
        log(f"오류: {exc}")
        return RunSummary(message=str(exc))

    already = set() if job.resend else history.sent_on(today, log_path, kind=job.kind)
    drafts = campaign.prepare(
        customers, job.template, already_sent=already,
        chat_name_col=pick(job.chat_col), room_col=pick(job.room_col),
        attachments=job.attachments, attach_col=pick(job.attach_col),
    )
    if kakao_pc and job.search_tab == "chats":
        for d in drafts:
            d.search_tab = "chats"

    targets = []
    for d in drafts:
        if d.problems:
            log(f"[건너뜀] {d.customer.name}: {' / '.join(d.problems)}")
        elif d.already_sent_today and not job.test_to:
            log(f"[건너뜀] {d.customer.name}: 오늘 이미 보냄")
        else:
            targets.append(d)

    if job.test_to:
        targets = targets[: job.test_count]
        log(f"[테스트 모드] {len(targets)}건을 고객 대신 '{job.test_to}' 에게 보냅니다.")

    if kakao_pc:
        from .kakao_pc import KakaoPCSender

        remaining = max(job.daily_cap - history.count_sent(today, KakaoPCSender.label, log_path), 0)
        if len(targets) > remaining:
            log(f"[한도] 오늘 남은 발송 수 {remaining}건만 보냅니다.")
            targets = targets[:remaining]

    if not targets:
        log("보낼 대상이 없습니다.")
        return RunSummary(skipped=len(drafts))

    from .control import clear_stop

    clear_stop()  # 지난번 '발송 중지' 요청이 남아 있지 않게
    if sender is None:
        try:
            sender = make_sender(job)
        except (ValueError, RuntimeError) as exc:
            log(f"오류: {exc}")
            return RunSummary(skipped=len(drafts), message=str(exc))

    if job.mode == "dry-run":
        for d in targets:
            log(f"\n===== {d.customer.name} ({d.destination if kakao_pc else d.customer.phone}) =====\n{d.text}")

    def progress(done, total, r):
        log(f"  [{done}/{total}] {'성공' if r.ok else '실패'} {r.detail}")

    results = campaign.send(targets, sender, log_path=log_path, on_result=progress, kind=job.kind,
                            test_to=job.test_to or None, test_tab=job.test_tab)
    summary = RunSummary(
        sent=sum(r.ok for r in results),
        failed=sum(not r.ok for r in results),
        skipped=len(drafts) - len(targets),
        warnings=sum("⚠️" in r.detail for r in results),
    )
    log(f"\n{sender.label}: 성공 {summary.sent}건 / 실패 {summary.failed}건 / 건너뜀 {summary.skipped}건")
    return summary
