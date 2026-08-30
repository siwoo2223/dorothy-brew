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

-- 재시작을 견뎌야 하는 루프 상태. 매매 기록이 아니라 봇의 진행 위치다.
CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
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

    # --- 루프 상태 -------------------------------------------------------
    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)", (key, str(value))
        )
        self.conn.commit()

    def get_state(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM bot_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def last_candle_ts(self) -> int:
        """마지막으로 판단을 끝낸 봉의 시각.

        복구하지 않으면 **재시작할 때마다 마지막 마감봉을 다시 판단한다.**
        12시간봉에서 돌파봉에 진입 → 손절 → 재시작이면, 그 봉의 돌파는
        아직 참이라 같은 자리에 다시 들어간다. 재시작이 반복되면
        (크래시 루프, 배포, 노트북 절전) 연속 손실 차단에 걸릴 때까지
        같은 매매를 반복한다.
        """
        try:
            return int(self.get_state("last_candle_ts", "0"))
        except ValueError:
            return 0

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

    def peak_equity(self) -> float:
        """기록된 최고 자본.

        재시작할 때 이 값을 복구하지 않으면 **고점 대비 낙폭 한도가
        재시작으로 초기화된다.** 고점에서 15% 내려온 상태에서 봇을 다시
        켜면 그 자리가 새 고점이 되어 한도가 영영 안 걸린다.
        연속 손실 카운터를 복구하는 이유와 같다.
        """
        row = self.conn.execute("SELECT MAX(equity) FROM equity").fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

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
