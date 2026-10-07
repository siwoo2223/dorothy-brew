"""발송 이력 기록 (중복 발송 방지, 하루 발송 한도 계산용)."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

DEFAULT_LOG = Path(__file__).resolve().parent.parent / "logs" / "send_log.csv"
TEST_KIND = "테스트"
FIELDS = ["발송시각", "구분", "방식", "고객명", "전화번호", "카톡이름", "미수총액", "결과", "상세", "본문"]


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def append(rows: list[dict], path: Path = DEFAULT_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with path.open(newline="", encoding="utf-8-sig") as f:
            header = next(csv.reader(f), [])
        if header != FIELDS:  # 예전 형식 이력 파일이면 새 열 구성으로 다시 쓴다
            old = _read(path)
            with path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows({k: r.get(k, "") for k in FIELDS} for r in old)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDS})


def _sent_rows(day: dt.date, path: Path) -> list[dict]:
    prefix = day.isoformat()
    return [
        r
        for r in _read(path)
        if r.get("발송시각", "").startswith(prefix)
        and r.get("결과") == "성공"
        and not r.get("방식", "").startswith("미리보기")
    ]


def sent_on(day: dt.date, path: Path = DEFAULT_LOG, kind: str | None = None) -> set[str]:
    """해당 날짜에 실제로 발송 성공한 고객 key(이름|전화번호) 목록.

    kind 를 주면 그 구분(미수금 안내, 공지사항 등)으로 보낸 것만 센다. 테스트 발송은 빠진다."""
    return {
        f"{r.get('고객명', '')}|{r.get('전화번호', '')}"
        for r in _sent_rows(day, path)
        if r.get("구분") != TEST_KIND and (kind is None or r.get("구분") == kind)
    }


def count_sent(day: dt.date, method: str, path: Path = DEFAULT_LOG) -> int:
    """해당 날짜에 특정 방식으로 발송 성공한 건수 (테스트 발송 포함: 실제로 나간 메시지라서)."""
    return sum(1 for r in _sent_rows(day, path) if r.get("방식") == method)
