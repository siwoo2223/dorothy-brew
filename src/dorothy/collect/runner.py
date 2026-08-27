"""거래소 WebSocket에 붙어 저장소로 흘려보낸다.

**통신은 최대한 얇게 두었다.** 이 파일에서 하는 일은 붙고, 받고, 넘기고,
끊기면 다시 붙는 것뿐이다. 판단이 들어가는 부분(파싱·빠짐 감지·저장)은
messages.py와 store.py에 있고 거기는 네트워크 없이 전부 테스트된다.

⚠ 이 파일만은 실제 거래소로 검증하지 못했다. 처음에 --probe로 몇 초
   받아보고 눈으로 확인하라.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from .messages import ParseError, parse_binance_depth, parse_binance_trade, unwrap
from .store import Store

log = logging.getLogger(__name__)

ENDPOINTS = {
    # 현물과 선물은 스트림 주소가 다르다. 선물을 쓰려면 fstream이어야 한다.
    "binance-futures": "wss://fstream.binance.com/stream?streams=",
    "binance-spot": "wss://stream.binance.com:9443/stream?streams=",
}


@dataclass
class CollectSpec:
    venue: str = "binance-futures"
    symbol: str = "btcusdt"
    trades: bool = True
    book: bool = True
    book_speed: str = "100ms"       # 100ms | 250ms | 500ms
    max_seconds: float | None = None
    reconnect_max: float = 60.0

    def streams(self) -> list[str]:
        symbol = self.symbol.lower()
        names = []
        if self.trades:
            names.append(f"{symbol}@aggTrade")
        if self.book:
            names.append(f"{symbol}@depth@{self.book_speed}")
        if not names:
            raise ValueError("trades와 book 중 최소 하나는 켜야 합니다.")
        return names

    def url(self) -> str:
        if self.venue not in ENDPOINTS:
            raise ValueError(
                f"지원하지 않는 거래소: {self.venue} (가능: {sorted(ENDPOINTS)})"
            )
        return ENDPOINTS[self.venue] + "/".join(self.streams())


def handle(raw: str, store: Store) -> str | None:
    """메시지 한 줄을 처리한다. 어떤 종류였는지 돌려준다.

    **네트워크 없이 테스트되는 지점이 여기다.** 수집기의 정확성은
    사실상 이 함수가 전부다.
    """
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError(f"JSON이 아닙니다: {raw[:80]!r}") from exc
    if not isinstance(message, dict):
        raise ParseError(f"사전이 아닙니다: {type(message).__name__}")

    # 구독 확인·핑 같은 제어 메시지는 버린다. 이걸 오류로 취급하면
    # 재접속할 때마다 수집기가 죽는다 (붙자마자 오는 응답이라 반드시 온다).
    if "result" in message or "id" in message and "e" not in message:
        return None

    name, payload = unwrap(message)
    event = payload.get("e", name)

    if "aggTrade" in str(event) or "aggTrade" in name:
        store.add_trade(parse_binance_trade(payload))
        return "trade"
    if "depthUpdate" in str(event) or "depth" in name:
        store.add_book(parse_binance_depth(payload))
        return "book"
    return None          # 구독 확인 응답 등 — 버려도 되는 것들


def run(spec: CollectSpec, store: Store, *, probe: bool = False) -> None:
    """붙어서 받는다. 끊기면 지수 백오프로 재접속한다.

    재접속 사이의 공백은 **반드시 gaps에 기록한다.** 안 그러면 나중에
    '그 시각엔 조용했다'로 잘못 읽는다.
    """
    try:
        from websockets.sync.client import connect
    except ImportError as exc:      # pragma: no cover - 선택 의존성
        raise ImportError(
            "수집기에는 websockets가 필요합니다: pip install -r requirements-collect.txt"
        ) from exc

    url = spec.url()
    store.set_meta("venue", spec.venue)
    store.set_meta("symbol", spec.symbol)
    store.set_meta("streams", ",".join(spec.streams()))

    started = time.time()
    backoff = 1.0
    seen = {"trade": 0, "book": 0}

    while True:
        if spec.max_seconds and time.time() - started >= spec.max_seconds:
            break
        disconnected_at: int | None = None
        try:
            log.info("접속: %s", url)
            with connect(url, open_timeout=20, close_timeout=5) as ws:
                backoff = 1.0
                while True:
                    if spec.max_seconds and time.time() - started >= spec.max_seconds:
                        break
                    raw = ws.recv(timeout=30)
                    kind = handle(raw, store)
                    if kind:
                        seen[kind] += 1
                    if probe and seen["trade"] + seen["book"] >= 20:
                        print(f"  체결 {seen['trade']}건, 호가 {seen['book']}건 수신")
                        print("  마지막 메시지:", raw[:200])
                        return
        except KeyboardInterrupt:
            log.info("중단 요청 — 저장하고 종료합니다")
            break
        except Exception as exc:            # 재접속으로 살려야 한다
            disconnected_at = int(time.time() * 1000)
            log.warning("끊김 (%s: %s) — %.0f초 뒤 재접속", type(exc).__name__, exc, backoff)
            store.flush()
            time.sleep(backoff)
            backoff = min(backoff * 2, spec.reconnect_max)
        finally:
            if disconnected_at is not None:
                store.add_gap("disconnect", from_ts=disconnected_at,
                              to_ts=int(time.time() * 1000),
                              note="재접속 중 공백")
    store.flush()
