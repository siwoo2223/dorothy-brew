"""수집한 체결·호가를 SQLite에 쌓는다.

CSV가 아니라 SQLite인 이유: 며칠만 모아도 수백만 행이 되는데, CSV로는
"2024-03-05 14:00~14:05 사이 체결"을 뽑는 데만 전체를 훑어야 한다.

**빠짐(gap)을 반드시 기록한다.** 재접속하는 동안 놓친 구간을 모르면,
나중에 분석할 때 "그 시각에 체결이 없었다"와 "우리가 못 받았다"를
구분할 수 없다. 그 둘을 섞으면 결론이 조용히 틀린다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .messages import BookDelta, Trade

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    ts        INTEGER NOT NULL,
    trade_id  INTEGER NOT NULL,
    price     REAL    NOT NULL,
    qty       REAL    NOT NULL,
    is_buy    INTEGER NOT NULL,
    PRIMARY KEY (trade_id)
);
CREATE INDEX IF NOT EXISTS trades_ts ON trades (ts);

CREATE TABLE IF NOT EXISTS book (
    ts        INTEGER NOT NULL,
    final_id  INTEGER NOT NULL,
    side      TEXT    NOT NULL,   -- 'bid' | 'ask'
    price     REAL    NOT NULL,
    qty       REAL    NOT NULL    -- 0이면 그 호가 소멸
);
CREATE INDEX IF NOT EXISTS book_ts ON book (ts);

-- 놓친 구간. 이게 없으면 '체결이 없었다'와 '못 받았다'를 구분할 수 없다.
CREATE TABLE IF NOT EXISTS gaps (
    kind      TEXT    NOT NULL,   -- 'trade' | 'book' | 'disconnect'
    from_ts   INTEGER,
    to_ts     INTEGER,
    from_id   INTEGER,
    to_id     INTEGER,
    note      TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Counts:
    trades: int = 0
    book_rows: int = 0
    gaps: int = 0


class Store:
    """수집 저장소. 커밋을 자주 해서 죽어도 데이터가 남게 한다."""

    def __init__(self, path: str | Path, *, commit_every: int = 500) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.executescript(SCHEMA)
        # 수집 중 전원이 나가도 앞부분은 살아남게 한다
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.commit()
        self.commit_every = commit_every
        self.counts = Counts()
        self._pending = 0
        self._last_trade_id: int | None = None
        self._last_final_id: int | None = None

    # -- 쓰기 -------------------------------------------------------------
    def add_trade(self, trade: Trade) -> None:
        """체결 하나. trade_id가 건너뛰면 빠짐으로 기록한다."""
        if self._last_trade_id is not None and trade.trade_id > self._last_trade_id + 1:
            self.add_gap("trade", from_id=self._last_trade_id + 1,
                         to_id=trade.trade_id - 1, to_ts=trade.ts,
                         note=f"체결 {trade.trade_id - self._last_trade_id - 1}건 누락")
        if self._last_trade_id is None or trade.trade_id > self._last_trade_id:
            self._last_trade_id = trade.trade_id

        self.conn.execute(
            "INSERT OR IGNORE INTO trades (ts, trade_id, price, qty, is_buy)"
            " VALUES (?, ?, ?, ?, ?)",
            (trade.ts, trade.trade_id, trade.price, trade.qty, int(trade.is_buy)),
        )
        self.counts.trades += 1
        self._tick()

    def add_book(self, delta: BookDelta) -> None:
        """호가 변경분. 갱신 ID가 이어지지 않으면 빠짐으로 기록한다."""
        if self._last_final_id is not None and delta.first_id > self._last_final_id + 1:
            self.add_gap("book", from_id=self._last_final_id + 1,
                         to_id=delta.first_id - 1, to_ts=delta.ts,
                         note="호가 갱신 끊김 — 스냅샷 재동기화 필요")
        self._last_final_id = delta.final_id

        rows = [(delta.ts, delta.final_id, "bid", lv.price, lv.qty) for lv in delta.bids]
        rows += [(delta.ts, delta.final_id, "ask", lv.price, lv.qty) for lv in delta.asks]
        if rows:
            self.conn.executemany(
                "INSERT INTO book (ts, final_id, side, price, qty) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            self.counts.book_rows += len(rows)
            self._tick()

    def add_gap(self, kind: str, *, from_ts: int | None = None,
                to_ts: int | None = None, from_id: int | None = None,
                to_id: int | None = None, note: str = "") -> None:
        self.conn.execute(
            "INSERT INTO gaps (kind, from_ts, to_ts, from_id, to_id, note)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (kind, from_ts, to_ts, from_id, to_id, note),
        )
        self.counts.gaps += 1
        self._tick()

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._tick()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    # -- 읽기 -------------------------------------------------------------
    def span(self) -> tuple[int | None, int | None]:
        row = self.conn.execute("SELECT MIN(ts), MAX(ts) FROM trades").fetchone()
        return (row[0], row[1]) if row else (None, None)

    def trade_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    def gap_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM gaps").fetchone()[0]

    def summary(self) -> str:
        first, last = self.span()
        lines = [
            f"  파일        {self.path}",
            f"  체결        {self.trade_count():,}건",
            f"  호가 행     {self.conn.execute('SELECT COUNT(*) FROM book').fetchone()[0]:,}",
            f"  빠짐        {self.gap_count():,}건",
        ]
        if first and last:
            hours = (last - first) / 3600_000
            lines.append(f"  구간        {hours:.1f}시간")
        if self.gap_count():
            lines.append("  ⚠ 빠진 구간이 있습니다. 분석할 때 gaps 테이블을 꼭 확인하세요 —")
            lines.append("     '체결이 없었다'와 '못 받았다'는 다릅니다.")
        return "\n".join(lines)

    # -- 살림 -------------------------------------------------------------
    def _tick(self) -> None:
        self._pending += 1
        if self._pending >= self.commit_every:
            self.flush()

    def flush(self) -> None:
        self.conn.commit()
        self._pending = 0

    def close(self) -> None:
        self.flush()
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
