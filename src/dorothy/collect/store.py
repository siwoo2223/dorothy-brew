"""수집한 체결·호가를 SQLite에 쌓는다.

CSV가 아니라 SQLite인 이유: 며칠만 모아도 수백만 행이 되는데, CSV로는
"2024-03-05 14:00~14:05 사이 체결"을 뽑는 데만 전체를 훑어야 한다.

**빠짐(gap)을 반드시 기록한다.** 재접속하는 동안 놓친 구간을 모르면,
나중에 분석할 때 "그 시각에 체결이 없었다"와 "우리가 못 받았다"를
구분할 수 없다. 그 둘을 섞으면 결론이 조용히 틀린다.

**용량이 이 모듈의 진짜 제약이다.** 실측 81바이트/행이고,
depth@100ms는 초당 10메시지다. 메시지당 갱신 레벨이 20개면
하루 1.4GB, 3개월이면 126GB가 된다. 몇 달을 모으려면 줄여야 한다:

    near_pct     중간가에서 먼 호가를 버린다 (0.005 = ±0.5%)
    book_speed   100ms 대신 500ms를 쓰면 5분의 1

near_pct는 **기준가가 있어야 동작한다.** 마지막 체결가를 쓰고,
체결이 아직 없으면 전부 저장한다 — 기준 없이 버리면 무엇을 버렸는지
알 수 없게 된다.
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
    book_dropped: int = 0     # near_pct로 버린 호가 행 수


class Store:
    """수집 저장소. 커밋을 자주 해서 죽어도 데이터가 남게 한다."""

    def __init__(
        self,
        path: str | Path,
        *,
        commit_every: int = 500,
        near_pct: float | None = None,
    ) -> None:
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
        if near_pct is not None and near_pct <= 0:
            raise ValueError("near_pct는 0보다 커야 합니다 (전부 저장하려면 None).")
        self.near_pct = near_pct
        self._reference: float | None = None    # 마지막 체결가 — 먼 호가 판정 기준
        # 버린 행 수는 **파일에 남겨야 한다.** 세션 카운터로만 두면 몇 달 뒤
        # 데이터를 열었을 때 "호가가 원래 이만큼이었다"와 "우리가 버렸다"를
        # 구분할 수 없다. gaps 테이블을 두는 이유와 같다.
        self._dropped_base = int(self.get_meta("book_dropped") or 0)
        self._dropped_dirty = False
        if near_pct is not None:
            self.set_meta("near_pct", repr(near_pct))

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
        self._reference = trade.price
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
        if self.near_pct is not None and self._reference:
            band = self._reference * self.near_pct
            kept = [r for r in rows if abs(r[3] - self._reference) <= band]
            dropped = len(rows) - len(kept)
            if dropped:
                self.counts.book_dropped += dropped
                self._dropped_dirty = True
            rows = kept
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
        dropped = self.total_dropped()
        band = self.near_pct if self.near_pct is not None else self.get_meta("near_pct")
        if dropped or band:
            note = f" (±{float(band):.2%} 밖)" if band else ""
            lines.append(f"  먼 호가 버림 {dropped:,}행{note}")
        lines.append(f"  파일 크기   {self.disk_bytes() / 1e6:,.1f} MB")
        if first and last:
            hours = (last - first) / 3600_000
            lines.append(f"  구간        {hours:.1f}시간")
        if self.gap_count():
            lines.append("  ⚠ 빠진 구간이 있습니다. 분석할 때 gaps 테이블을 꼭 확인하세요 —")
            lines.append("     '체결이 없었다'와 '못 받았다'는 다릅니다.")
        return "\n".join(lines)

    def disk_bytes(self) -> int:
        """WAL까지 합친 실제 사용량. 몇 달 모으려면 이걸 봐야 한다."""
        total = 0
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(self.path) + suffix)
            if f.exists():
                total += f.stat().st_size
        return total

    def growth_estimate(self) -> str:
        """현재 속도가 유지되면 얼마나 커지는가."""
        first, last = self.span()
        size = self.disk_bytes()
        if not first or not last or last <= first or size <= 0:
            return "  증가율    아직 판단할 만큼 안 모였습니다"
        days = (last - first) / 86_400_000
        if days <= 0:
            return "  증가율    아직 판단할 만큼 안 모였습니다"
        per_day = size / days
        return (
            f"  증가율    하루 {per_day / 1e9:.2f} GB "
            f"→ 30일 {per_day * 30 / 1e9:.0f} GB · 90일 {per_day * 90 / 1e9:.0f} GB"
        )

    # -- 살림 -------------------------------------------------------------
    def _tick(self) -> None:
        self._pending += 1
        if self._pending >= self.commit_every:
            self.flush()

    def flush(self) -> None:
        if self._dropped_dirty:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES ('book_dropped', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(self.total_dropped()),),
            )
            self._dropped_dirty = False
        self.conn.commit()
        self._pending = 0

    def total_dropped(self) -> int:
        """이 파일에서 지금까지 버린 호가 행 수 (이전 실행분 포함)."""
        return self._dropped_base + self.counts.book_dropped

    def close(self) -> None:
        self.flush()
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
