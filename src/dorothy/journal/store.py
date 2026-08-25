"""SQLite 매매 기록.

봇이 재시작되어도 손익·연속손실 카운터를 복구할 수 있어야 한다.
스프레드시트나 노션 일지로 내보내기도 여기서 출발한다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..models import Trade

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT    NOT NULL,
    side         TEXT    NOT NULL,
    size         REAL    NOT NULL,
    entry_price  REAL    NOT NULL,
    exit_price   REAL    NOT NULL,
    opened_at    INTEGER NOT NULL,
    closed_at    INTEGER NOT NULL,
    fee          REAL    NOT NULL DEFAULT 0,
    funding      REAL    NOT NULL DEFAULT 0,
    net_pnl      REAL    NOT NULL,
    reason       TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_trades_closed ON trades(closed_at);

CREATE TABLE IF NOT EXISTS equity (
    ts     INTEGER PRIMARY KEY,
    equity REAL NOT NULL
);
"""


class Journal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def record_trade(self, trade: Trade) -> None:
        self.conn.execute(
            """INSERT INTO trades
               (symbol, side, size, entry_price, exit_price, opened_at,
                closed_at, fee, funding, net_pnl, reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trade.symbol, trade.side.value, trade.size, trade.entry_price,
                trade.exit_price, trade.opened_at, trade.closed_at,
                trade.fee, trade.funding, trade.net_pnl, trade.reason,
            ),
        )
        self.conn.commit()

    def record_equity(self, ts: int, equity: float) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO equity (ts, equity) VALUES (?, ?)", (ts, equity)
        )
        self.conn.commit()

    def recent_trades(self, limit: int = 20) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM trades ORDER BY closed_at DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()

    def pnl_since(self, ts: int) -> float:
        cur = self.conn.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) AS s FROM trades WHERE closed_at >= ?", (ts,)
        )
        return float(cur.fetchone()["s"])

    def consecutive_losses(self) -> int:
        """재시작 후 연속 손실 카운터 복구용."""
        cur = self.conn.execute(
            "SELECT net_pnl FROM trades ORDER BY closed_at DESC LIMIT 50"
        )
        count = 0
        for row in cur.fetchall():
            if row["net_pnl"] < 0:
                count += 1
            else:
                break
        return count

    def close(self) -> None:
        self.conn.close()
