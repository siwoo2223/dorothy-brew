"""거래소 메시지 → 우리 자료구조.

여기가 이 모듈에서 유일하게 까다로운 부분이고, 네트워크 없이 전부 테스트된다.
거래소마다 필드 이름이 다르고, 숫자를 문자열로 준다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Trade:
    """체결 하나.

    aggressor가 핵심이다. 어느 쪽이 시장가로 들이받았는지가 슬리피지의 방향이다.
    """

    ts: int             # 체결 시각 (epoch ms)
    price: float
    qty: float
    is_buy: bool        # True면 매수가 공격 (테이커가 샀다)
    trade_id: int       # 빠짐 감지용

    @property
    def notional(self) -> float:
        return self.price * self.qty


@dataclass(frozen=True)
class BookLevel:
    price: float
    qty: float          # 0이면 그 호가가 사라졌다는 뜻


@dataclass(frozen=True)
class BookDelta:
    """호가창 변경분."""

    ts: int
    first_id: int       # 이 갱신이 담당하는 구간 시작
    final_id: int       # 구간 끝. 앞 메시지의 final_id + 1 이어야 연속이다
    bids: list[BookLevel]
    asks: list[BookLevel]


class ParseError(ValueError):
    """메시지를 알아볼 수 없다. 조용히 넘기면 데이터에 구멍이 생긴다."""


def _num(value: object, field: str) -> float:
    try:
        return float(value)          # 거래소가 숫자를 문자열로 준다
    except (TypeError, ValueError) as exc:
        raise ParseError(f"{field}를 숫자로 읽을 수 없습니다: {value!r}") from exc


def parse_binance_trade(payload: dict) -> Trade:
    """바이낸스 aggTrade.

    m 필드는 "매수자가 메이커인가"다. **뒤집어야 공격자 방향이 된다.**
    여기서 부호를 잘못 잡으면 슬리피지 방향이 통째로 반대가 된다.
    """
    for field in ("T", "p", "q", "m", "a"):
        if field not in payload:
            raise ParseError(f"aggTrade에 {field} 필드가 없습니다: {sorted(payload)}")
    return Trade(
        ts=int(payload["T"]),
        price=_num(payload["p"], "p"),
        qty=_num(payload["q"], "q"),
        is_buy=not bool(payload["m"]),
        trade_id=int(payload["a"]),
    )


def parse_binance_depth(payload: dict) -> BookDelta:
    """바이낸스 depthUpdate."""
    for field in ("E", "U", "u", "b", "a"):
        if field not in payload:
            raise ParseError(f"depthUpdate에 {field} 필드가 없습니다: {sorted(payload)}")

    def levels(rows: object, label: str) -> list[BookLevel]:
        if not isinstance(rows, list):
            raise ParseError(f"{label}가 목록이 아닙니다: {type(rows).__name__}")
        out = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                raise ParseError(f"{label} 항목이 [가격, 수량] 형태가 아닙니다: {row!r}")
            out.append(BookLevel(_num(row[0], label), _num(row[1], label)))
        return out

    return BookDelta(
        ts=int(payload["E"]),
        first_id=int(payload["U"]),
        final_id=int(payload["u"]),
        bids=levels(payload["b"], "bids"),
        asks=levels(payload["a"], "asks"),
    )


def unwrap(message: dict) -> tuple[str, dict]:
    """결합 스트림 봉투를 벗긴다.

    /stream?streams=a/b 로 붙으면 {"stream": ..., "data": {...}}로 온다.
    단일 스트림이면 봉투 없이 바로 온다. 둘 다 받아야 한다.
    """
    if "stream" in message and "data" in message:
        data = message["data"]
        if not isinstance(data, dict):
            raise ParseError(f"data가 사전이 아닙니다: {type(data).__name__}")
        return str(message["stream"]), data
    event = message.get("e")
    if not event:
        raise ParseError(f"어떤 스트림인지 알 수 없습니다: {sorted(message)[:8]}")
    return str(event), message
