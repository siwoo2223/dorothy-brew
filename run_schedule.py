"""예약 시간에 Windows 작업 스케줄러가 실행하는 파일: python run_schedule.py <예약ID>"""
from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from receivables import scheduler  # noqa: E402
from receivables.runner import run_job  # noqa: E402


def main(argv: list[str]) -> int:
    load_dotenv(ROOT / ".env")
    if len(argv) < 2:
        print("사용법: python run_schedule.py <예약ID>")
        return 2
    entry_id = argv[1]
    entry = scheduler.load(entry_id)
    lines: list[str] = []

    def log(text: str) -> None:
        lines.append(text)
        try:  # 창 출력이 실패해도(글꼴·인코딩 등) 발송과 기록은 계속한다
            print(text, flush=True)
        except Exception:
            pass

    log(f"===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} 예약 실행: {entry.name} ({entry.schedule.describe()}) =====")
    try:
        summary = run_job(entry.job, log=log)
        code = 0 if summary.ok else 1
    except Exception as exc:  # 예약 실행은 화면이 없으므로 오류도 기록에 남긴다
        log(f"오류: {type(exc).__name__}: {exc}")
        code = 2
    scheduler.append_run_log(entry_id, "\n".join(lines) + "\n")
    try:
        print("\n이 창은 30초 뒤에 닫힙니다.")
    except Exception:
        pass
    time.sleep(30)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
