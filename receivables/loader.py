"""엑셀/CSV 미수금 원장을 읽어 고객별 안내 데이터로 묶는다.

엑셀은 '거래 한 건 = 한 줄' 형식이다. 같은 고객(이름+전화번호)의 여러 줄을 하나로 묶어
고객마다 미수총액, 미수건수, 미수내역(줄 단위 목록) 같은 변수를 자동으로 만든다.
"""
from __future__ import annotations

import datetime as dt
import io
import re
from dataclasses import dataclass, field

import pandas as pd

from .template import format_value, render

# 내역 한 줄의 기본 서식. 엑셀 열 이름을 #{열이름} 으로 쓸 수 있다.
DEFAULT_LINE_TEMPLATE = "- #{거래일자} #{품목} #{미수금}원"

PHONE_PATTERN = re.compile(r"^(010\d{8}|01[16789]\d{7,8})$")


@dataclass
class ColumnMap:
    name: str = "고객명"
    phone: str | None = "전화번호"
    amount: str = "미수금"
    due_date: str | None = "납부기한"


@dataclass
class Customer:
    name: str
    phone: str
    rows: pd.DataFrame
    variables: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.name}|{self.phone}"


def read_table(data: bytes | str, filename: str = "") -> pd.DataFrame:
    """엑셀(.xlsx/.xls) 또는 CSV 를 DataFrame 으로 읽는다."""
    lower = (filename or (data if isinstance(data, str) else "")).lower()
    source = io.BytesIO(data) if isinstance(data, bytes) else data
    if lower.endswith(".csv"):
        try:
            df = pd.read_csv(source)
        except UnicodeDecodeError:
            source = io.BytesIO(data) if isinstance(data, bytes) else data
            df = pd.read_csv(source, encoding="cp949")
    else:
        df = pd.read_excel(source)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")
    return df


def normalize_phone(value) -> str:
    text = format_value(value).replace(",", "")
    digits = re.sub(r"\D", "", text)
    # 엑셀이 숫자로 읽어 맨 앞 0 이 빠진 경우 (1012345678 → 01012345678)
    if len(digits) in (9, 10) and digits.startswith("1"):
        digits = "0" + digits
    return digits


def to_number(value) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return float(text) if text else 0.0
    except ValueError:
        return 0.0


def _to_date(value) -> dt.date | None:
    if value is None:
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


def group_customers(
    df: pd.DataFrame,
    columns: ColumnMap,
    line_template: str = DEFAULT_LINE_TEMPLATE,
    today: dt.date | None = None,
    include_zero: bool = False,
    require_phone: bool = True,
) -> list[Customer]:
    """거래 단위 행을 고객 단위로 묶고 안내용 변수를 만든다."""
    today = today or dt.date.today()
    required = [columns.name, columns.amount] + ([columns.phone] if require_phone else [])
    for col in required:
        if col not in df.columns:
            raise ValueError(f"엑셀에 '{col}' 열이 없습니다. 열 이름을 확인하거나 열 지정을 바꿔 주세요.")
    due_col = columns.due_date if columns.due_date in df.columns else None

    work = df.copy()
    work["_이름"] = work[columns.name].map(format_value)
    if columns.phone and columns.phone in df.columns:
        work["_전화"] = work[columns.phone].map(normalize_phone)
    else:
        work["_전화"] = ""
    work["_금액"] = work[columns.amount].map(to_number)
    work = work[work["_이름"] != ""]

    customers: list[Customer] = []
    for (name, phone), rows in work.groupby(["_이름", "_전화"], sort=False):
        open_rows = rows[rows["_금액"] != 0]
        total = open_rows["_금액"].sum()
        if total <= 0 and not include_zero:
            continue

        variables: dict = {}
        # 고객 안에서 값이 하나뿐인 열(담당자, 계좌번호 등)은 그대로 변수로 쓸 수 있다.
        for col in df.columns:
            uniq = rows[col].dropna().unique()
            if len(uniq) == 1:
                variables[col] = uniq[0]

        lines = []
        for _, row in open_rows.iterrows():
            row_values = {c: row[c] for c in df.columns}
            lines.append(render(line_template, row_values).text)

        variables.update(
            {
                "고객명": name,
                "전화번호": phone,
                "미수총액": int(total) if float(total).is_integer() else total,
                "미수건수": len(open_rows),
                "미수내역": "\n".join(lines),
                "기준일": today,
            }
        )

        if due_col:
            dates = [d for d in open_rows[due_col].map(_to_date) if d]
            if dates:
                earliest = min(dates)
                variables["최초납부기한"] = earliest
                variables["연체일수"] = max((today - earliest).days, 0)

        problems = []
        if require_phone and not PHONE_PATTERN.match(phone):
            problems.append(f"휴대폰 번호 형식 오류({phone or '빈 값'})")

        customers.append(
            Customer(name=name, phone=phone, rows=open_rows, variables=variables, problems=problems)
        )
    return customers
