"""명령줄 발송 (예약 작업/자동 실행용)

예시:
  python cli.py samples/미수금_샘플.xlsx templates/미수금/01_미수금_안내.txt            # 미리보기만
  python cli.py 원장.xlsx templates/미수금/01_미수금_안내.txt --mode alimtalk --template-id KA01TP...
  python cli.py 원장.xlsx templates/미수금/01_미수금_안내.txt --mode kakao-pc          # 내 PC 카카오톡으로
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from receivables import campaign, history
from receivables.loader import DEFAULT_LINE_TEMPLATE, ColumnMap, group_customers, load_recipients, read_table
from receivables.kakao_pc import KakaoPCSender, Win32KakaoDriver
from receivables.sender import DryRunSender, SolapiSender


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path(__file__).parent / ".env")
    p = argparse.ArgumentParser(description="고객별 미수금 안내 발송")
    p.add_argument("ledger", help="미수금 원장 엑셀/CSV 파일")
    p.add_argument("template", help="안내 문구 템플릿 .txt 파일")
    p.add_argument("--kind", choices=["미수금", "공지"], default="미수금", help="보낼 내용 (공지: 명단 전원에게)")
    p.add_argument("--test-to", default="", help="테스트 모드: 고객 대신 이 카톡 이름/번호로 보냄")
    p.add_argument("--test-count", type=int, default=3, help="테스트 모드에서 보낼 건수")
    p.add_argument("--mode", choices=["dry-run", "kakao-pc", "alimtalk", "sms"], default="dry-run")
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
    p.add_argument("--search-tab", choices=["friends", "chats"], default="friends", help="[kakao-pc] 찾을 곳")
    p.add_argument("--gap", type=float, nargs=2, default=[8, 15], metavar=("최소", "최대"),
                   help="[kakao-pc] 메시지 간격(초)")
    p.add_argument("--daily-cap", type=int, default=50, help="[kakao-pc] 하루 최대 발송 수")
    args = p.parse_args(argv)

    df = read_table(args.ledger)
    kakao_pc = args.mode == "kakao-pc"
    kind = "공지사항" if args.kind == "공지" else "미수금 안내"
    if args.kind == "공지":
        customers = load_recipients(df, args.name_col, args.phone_col, require_phone=not kakao_pc)
    else:
        customers = group_customers(
            df, ColumnMap(args.name_col, args.phone_col, args.amount_col, args.due_col), args.line,
            require_phone=not kakao_pc,
        )
    template = Path(args.template).read_text(encoding="utf-8")
    already = set() if args.resend else history.sent_on(dt.date.today(), kind=kind)
    drafts = campaign.prepare(
        customers, template, already_sent=already, chat_name_col=args.chat_col if args.chat_col in df.columns else None,
        room_col=args.room_col if args.room_col in df.columns else None,
    )

    targets = []
    for d in drafts:
        if d.problems:
            print(f"[건너뜀] {d.customer.name}: {' / '.join(d.problems)}")
        elif d.already_sent_today:
            print(f"[건너뜀] {d.customer.name}: 오늘 이미 발송함")
        else:
            targets.append(d)

    if args.test_to:
        targets = targets[: args.test_count]
        print(f"[테스트 모드] {len(targets)}건을 고객 대신 '{args.test_to}' 에게 보냅니다.")

    if kakao_pc:
        remaining = max(args.daily_cap - history.count_sent(dt.date.today(), KakaoPCSender.label), 0)
        if len(targets) > remaining:
            print(f"[한도] 오늘 남은 발송 수 {remaining}건만 보냅니다.")
            targets = targets[:remaining]

    if args.mode == "dry-run":
        sender = DryRunSender()
        for d in targets:
            print(f"\n===== {d.customer.name} ({d.customer.phone}) =====\n{d.text}")
    else:
        try:
            if kakao_pc:
                sender = KakaoPCSender(Win32KakaoDriver(search_tab=args.search_tab), min_interval=args.gap[0],
                                       max_interval=args.gap[1])
            else:
                sender = SolapiSender(
                    os.getenv("SOLAPI_API_KEY", ""),
                    os.getenv("SOLAPI_API_SECRET", ""),
                    os.getenv("SENDER_NUMBER", ""),
                    mode=args.mode,
                    pf_id=os.getenv("KAKAO_PF_ID", ""),
                    template_id=args.template_id,
                    sms_fallback=not args.no_sms_fallback,
                )
        except (ValueError, RuntimeError) as exc:
            print(f"오류: {exc}")
            return 2

    def progress(done, total, r):
        if kakao_pc:
            print(f"  [{done}/{total}] {'성공' if r.ok else '실패'} {r.detail}", flush=True)

    results = campaign.send(targets, sender, on_result=progress, kind=kind, test_to=args.test_to or None)
    ok = sum(r.ok for r in results)
    print(f"\n{sender.label}: 성공 {ok}건 / 실패 {len(results) - ok}건 / 건너뜀 {len(drafts) - len(targets)}건")
    for r in results:
        if not r.ok:
            print(f"  실패 {r.to}: {r.detail}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
