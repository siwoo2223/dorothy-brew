"""명령줄 발송 (자동 실행용)

예시:
  python cli.py samples/미수금_샘플.xlsx templates/미수금/01_미수금_안내.txt            # 미리보기만
  python cli.py 원장.xlsx templates/미수금/01_미수금_안내.txt --mode alimtalk --template-id KA01TP...
  python cli.py 원장.xlsx templates/미수금/01_미수금_안내.txt --mode kakao-pc          # 내 PC 카카오톡으로
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from receivables.loader import DEFAULT_LINE_TEMPLATE
from receivables.runner import KIND_NOTICE, KIND_RECEIVABLE, MODES, Job, run_job


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path(__file__).parent / ".env")
    p = argparse.ArgumentParser(description="고객별 미수금 안내·공지 발송")
    p.add_argument("ledger", help="미수금 원장(또는 명단) 엑셀/CSV 파일")
    p.add_argument("template", help="안내 문구 템플릿 .txt 파일")
    p.add_argument("--kind", choices=["미수금", "공지"], default="미수금", help="보낼 내용 (공지: 명단 전원에게)")
    p.add_argument("--test-to", default="", help="테스트 모드: 고객 대신 이 카톡 이름/번호로 보냄")
    p.add_argument("--test-count", type=int, default=3, help="테스트 모드에서 보낼 건수")
    p.add_argument("--mode", choices=MODES, default="dry-run")
    p.add_argument("--template-id", default="", help="알림톡 템플릿 ID")
    p.add_argument("--no-sms-fallback", action="store_true", help="알림톡 실패 시 문자 대체 안 함")
    p.add_argument("--name-col", default="고객명")
    p.add_argument("--phone-col", default="전화번호")
    p.add_argument("--amount-col", default="미수금")
    p.add_argument("--due-col", default="납부기한")
    p.add_argument("--line", default=DEFAULT_LINE_TEMPLATE, help="미수내역 한 줄 서식")
    p.add_argument("--resend", action="store_true", help="오늘 이미 보낸 고객에게도 다시 발송")
    p.add_argument("--chat-col", default="카톡이름", help="[kakao-pc] 카톡 이름 열 (비면 고객명)")
    p.add_argument("--room-col", default="채팅방", help="[kakao-pc] 채팅방 이름 열 (값이 있으면 그 채팅방으로)")
    p.add_argument("--attach-col", default="첨부파일", help="[kakao-pc] 고객별 첨부파일 경로 열")
    p.add_argument("--search-tab", choices=["friends", "chats"], default="friends", help="[kakao-pc] 찾을 곳")
    p.add_argument("--gap", type=float, nargs=2, default=[8, 15], metavar=("최소", "최대"),
                   help="[kakao-pc] 메시지 간격(초)")
    p.add_argument("--daily-cap", type=int, default=500, help="[kakao-pc] 하루 최대 발송 수")
    args = p.parse_args(argv)

    job = Job(
        kind=KIND_NOTICE if args.kind == "공지" else KIND_RECEIVABLE,
        mode=args.mode,
        template=Path(args.template).read_text(encoding="utf-8"),
        ledger=args.ledger,
        name_col=args.name_col,
        phone_col=args.phone_col,
        amount_col=args.amount_col,
        due_col=args.due_col,
        line_template=args.line,
        chat_col=args.chat_col,
        room_col=args.room_col,
        attach_col=args.attach_col,
        search_tab=args.search_tab,
        gap=tuple(args.gap),
        daily_cap=args.daily_cap,
        template_id=args.template_id,
        sms_fallback=not args.no_sms_fallback,
        test_to=args.test_to,
        test_count=args.test_count,
        resend=args.resend,
    )
    summary = run_job(job, log=lambda s: print(s, flush=True))
    if summary.message:
        return 2
    return 0 if summary.ok else 1


if __name__ == "__main__":
    sys.exit(main())
