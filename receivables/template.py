"""#{변수} 형식 템플릿 치환과 값 서식 처리."""
from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field

VAR_PATTERN = re.compile(r"#\{([^{}]+)\}")

# 알림톡 본문 최대 길이 (공백·줄바꿈 포함)
ALIMTALK_MAX_LENGTH = 1000


def find_variables(template: str) -> list[str]:
    """템플릿에 등장하는 변수 이름을 순서대로(중복 없이) 돌려준다."""
    seen: dict[str, None] = {}
    for name in VAR_PATTERN.findall(template):
        seen.setdefault(name.strip(), None)
    return list(seen)


def format_value(value) -> str:
    """엑셀에서 읽은 값을 메시지에 넣기 좋은 문자열로 바꾼다."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if value != value:  # pandas.NaT 등 '빈 값'
        return ""
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, dt.datetime):
        if value.time() == dt.time(0, 0):
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, dt.date):
        return value.strftime("%Y-%m-%d")
    # pandas.Timestamp 는 datetime 의 하위 클래스라 위에서 처리된다.
    if hasattr(value, "item"):  # numpy 스칼라
        return format_value(value.item())
    return str(value).strip()


@dataclass
class RenderResult:
    text: str
    missing: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.missing


def render(template: str, values: dict) -> RenderResult:
    """템플릿의 #{변수}를 values 로 채운다. 값이 없는 변수는 missing 에 담는다."""
    missing: list[str] = []
    used: dict[str, str] = {}

    def replace(match: re.Match) -> str:
        name = match.group(1).strip()
        if name not in values:
            if name not in missing:
                missing.append(name)
            return match.group(0)
        text = format_value(values[name])
        if text == "" and name not in missing:
            missing.append(name)
        used[name] = text
        return text

    text = VAR_PATTERN.sub(replace, template)
    return RenderResult(text=text, missing=missing, variables=used)
