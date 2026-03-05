"""
SQLite-backed store for usage snapshots.

Each snapshot records the computed rolling-window token counts at a
specific moment in time. This allows the chart to show history even
across app restarts without re-parsing all JSONL files every time.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,   -- ISO-8601 UTC timestamp
    tokens_5h   INTEGER NOT NULL,
    tokens_7d   INTEGER NOT NULL,
    limit_5h    INTEGER NOT NULL,
    limit_7d    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ts ON snapshots(ts);
"""


class DataStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._conn() as con:
            con.executescript(_CREATE_TABLE)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def insert_snapshot(
        self,
        ts: datetime,
        tokens_5h: int,
        tokens_7d: int,
        limit_5h: int,
        limit_7d: int,
    ) -> None:
        ts_str = ts.astimezone(timezone.utc).isoformat()
        with self._conn() as con:
            con.execute(
                "INSERT INTO snapshots (ts, tokens_5h, tokens_7d, limit_5h, limit_7d) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts_str, tokens_5h, tokens_7d, limit_5h, limit_7d),
            )

    def bulk_insert(self, rows: list[dict], limit_5h: int, limit_7d: int) -> None:
        """
        Insert multiple historical rows produced by usage_reader.build_history().

        rows : list of {"timestamp": datetime, "tokens_5h": int, "tokens_7d": int}
        """
        with self._conn() as con:
            for row in rows:
                ts_str = row["timestamp"].astimezone(timezone.utc).isoformat()
                con.execute(
                    "INSERT OR IGNORE INTO snapshots (ts, tokens_5h, tokens_7d, limit_5h, limit_7d) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ts_str, row["tokens_5h"], row["tokens_7d"], limit_5h, limit_7d),
                )

    def get_history(self, hours_back: int) -> list[dict]:
        """
        Return snapshots newer than `hours_back` hours ago, ordered by ts.
        """
        since = (
            datetime.now(timezone.utc) - timedelta(hours=hours_back)
        ).isoformat()
        with self._conn() as con:
            rows = con.execute(
                "SELECT ts, tokens_5h, tokens_7d, limit_5h, limit_7d "
                "FROM snapshots WHERE ts >= ? ORDER BY ts",
                (since,),
            ).fetchall()

        result = []
        for r in rows:
            ts = datetime.fromisoformat(r["ts"])
            lim_5h = r["limit_5h"] or 1
            lim_7d = r["limit_7d"] or 1
            result.append(
                {
                    "ts": ts,
                    "pct_5h": min(100.0, r["tokens_5h"] / lim_5h * 100),
                    "pct_7d": min(100.0, r["tokens_7d"] / lim_7d * 100),
                    "tokens_5h": r["tokens_5h"],
                    "tokens_7d": r["tokens_7d"],
                }
            )
        return result

    def latest_snapshot(self) -> Optional[dict]:
        with self._conn() as con:
            row = con.execute(
                "SELECT ts, tokens_5h, tokens_7d, limit_5h, limit_7d "
                "FROM snapshots ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "ts": datetime.fromisoformat(row["ts"]),
            "tokens_5h": row["tokens_5h"],
            "tokens_7d": row["tokens_7d"],
            "limit_5h": row["limit_5h"],
            "limit_7d": row["limit_7d"],
        }

    def purge_old(self, keep_days: int = 31) -> None:
        """Remove snapshots older than `keep_days` to keep the DB small."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=keep_days)
        ).isoformat()
        with self._conn() as con:
            con.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))

    def has_history(self) -> bool:
        with self._conn() as con:
            count = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        return count > 0
