"""매매일지 레코드 적재.

노션 "매매 기록" 데이터베이스에서 CSV로 내보낸 파일을 읽는다.
(노션: 데이터베이스 우측 상단 ··· → 내보내기 → CSV)

백테스트의 `models.Trade`와 별개의 타입을 쓴다.
실제 매매에는 레버리지·증거금·실수 태그·진입 근거처럼
백테스트에는 없는 정보가 들어 있고, 그게 분석의 핵심이기 때문이다.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

# 노션 CSV 헤더 → 내부 필드. 영문 헤더도 함께 받는다.
_ALIASES: dict[str, str] = {
    "회차": "index", "no": "index", "index": "index",
    "날짜": "traded_on", "date": "traded_on",
    "심볼": "symbol", "symbol": "symbol",
    "방향": "side", "side": "side",
    "레버리지": "leverage", "leverage": "leverage",
    "진입가": "entry_price", "entry": "entry_price",
    "청산가": "exit_price", "exit": "exit_price",
    "시작금액": "margin", "margin": "margin",
    "손익": "pnl", "pnl": "pnl",
    "손절액": "planned_risk", "risk": "planned_risk",
    "결과": "outcome", "result": "outcome",
    "실수 태그": "tags", "tags": "tags",
    "진입 근거": "rationale", "rationale": "rationale",
    "반성일지": "review", "review": "review",
}


@dataclass
class JournalTrade:
    index: int = 0
    traded_on: date | None = None
    symbol: str = ""
    side: str = ""              # 롱 / 숏
    leverage: float = 0.0
    entry_price: float = 0.0
    exit_price: float = 0.0
    margin: float = 0.0         # 시작금액(증거금)
    pnl: float = 0.0
    planned_risk: float = 0.0   # 손절액. 0이면 미기록
    outcome: str = ""
    tags: list[str] = field(default_factory=list)
    rationale: str = ""
    review: str = ""

    @property
    def is_win(self) -> bool:
        return self.pnl > 0

    @property
    def return_pct(self) -> float:
        """증거금 대비 수익률. 노션의 수익률(%) 계산식과 같다."""
        return self.pnl / self.margin * 100 if self.margin else 0.0

    @property
    def r_multiple(self) -> float | None:
        """손절액 대비 몇 배를 벌었나(R). 손절액이 없으면 계산 불가.

        R은 매매 성과를 비교하는 가장 정직한 단위다.
        금액은 계좌 크기에 따라 달라지지만 R은 그렇지 않다.
        """
        if self.planned_risk <= 0:
            return None
        return self.pnl / self.planned_risk

    @property
    def price_move_pct(self) -> float:
        """레버리지를 뺀 순수 가격 변동률. 판단이 맞았는지를 보는 값."""
        if not self.entry_price:
            return 0.0
        raw = (self.exit_price - self.entry_price) / self.entry_price * 100
        return raw if self.side == "롱" else -raw

    @property
    def setup(self) -> str:
        """진입 근거의 `[셋업이름]` 접두사.

        매매일지 스킬이 `[유동성스윕] 전일 저가 쓸고 반등` 형식으로 저장한다.
        이 접두사가 있어야 셋업별로 묶어 기대값을 낼 수 있다.
        자유 서술만 있으면 그룹화가 불가능하다 — 그래서 형식을 강제한다.
        """
        match = re.match(r"\s*\[([^\]]{1,20})\]", self.rationale or "")
        if match:
            return match.group(1).strip()
        return "(미분류)" if self.rationale else "(없음)"

    @property
    def has_setup(self) -> bool:
        return not self.setup.startswith("(")

    @property
    def weekday(self) -> str:
        if self.traded_on is None:
            return "?"
        return ["월", "화", "수", "목", "금", "토", "일"][self.traded_on.weekday()]


def _parse_float(value: str | float | None) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").replace("$", "").replace("%", "").strip()
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_tags(value: str | list | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if text.startswith("["):          # 노션 SQL 결과는 JSON 배열로 온다
        try:
            return [str(v).strip() for v in json.loads(text) if str(v).strip()]
        except json.JSONDecodeError:
            pass
    return [t.strip() for t in text.split(",") if t.strip()]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y년 %m월 %d일"):
        try:
            return datetime.strptime(text[: len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def from_rows(rows: list[dict]) -> list[JournalTrade]:
    """dict 목록을 JournalTrade로 변환한다. 알 수 없는 컬럼은 무시한다."""
    trades: list[JournalTrade] = []
    for row in rows:
        mapped: dict = {}
        for key, value in row.items():
            field_name = _ALIASES.get(str(key).strip().lower()) or _ALIASES.get(str(key).strip())
            if field_name is None:
                continue
            if field_name in ("index",):
                mapped[field_name] = int(_parse_float(value))
            elif field_name in ("leverage", "entry_price", "exit_price", "margin", "pnl", "planned_risk"):
                mapped[field_name] = _parse_float(value)
            elif field_name == "tags":
                mapped[field_name] = _parse_tags(value)
            elif field_name == "traded_on":
                mapped[field_name] = _parse_date(value)
            else:
                mapped[field_name] = str(value or "").strip()
        if mapped.get("margin") or mapped.get("pnl"):
            trades.append(JournalTrade(**mapped))

    trades.sort(key=lambda t: (t.traded_on or date.min, t.index))
    return trades


def load_csv(path: str | Path) -> list[JournalTrade]:
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return from_rows(list(csv.DictReader(f)))


def load_json(path: str | Path) -> list[JournalTrade]:
    """노션 SQL 조회 결과(JSON)를 그대로 읽는다."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("results", data) if isinstance(data, dict) else data
    normalised = []
    for row in rows:
        item = dict(row)
        # 노션 확장 키를 평범한 이름으로 되돌린다
        for key in list(item):
            if key.startswith("date:") and key.endswith(":start"):
                item["날짜"] = item.pop(key)
        normalised.append(item)
    return from_rows(normalised)
