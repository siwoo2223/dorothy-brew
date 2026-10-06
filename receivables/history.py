"""발송 이력 기록 (중복 발송 방지용)."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

DEFAULT_LOG = Path(__file__).resolve().parent.parent / "logs" / "send_log.csv"
FIELDS = ["발송시각", "방식", "고객명", "전화번호", "미수총액", "결과", "상세", "본문"]


def append(rows: list[dict], path: Path = DEFAULT_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDS})


def sent_on(day: dt.date, path: Path = DEFAULT_LOG) -> set[str]:
    """해당 날짜에 실제로 발송 성공한 전화번호 목록."""
    if not path.exists():
        return set()
    prefix = day.isoformat()
    phones = set()
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if (
                row.get("발송시각", "").startswith(prefix)
                and row.get("결과") == "성공"
                and not row.get("방식", "").startswith("미리보기")
            ):
                phones.add(row.get("전화번호", ""))
    return phones
