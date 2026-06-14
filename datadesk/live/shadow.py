"""
Shadow signal store — the audit trail for DESIGN §6.2 shadow-first validation.

EVERY fast-path signal is recorded here, armed or not. Shadow mode means this
table is the only place a signal lands; armed mode means it lands here AND at
the broker. Forward validation reads this table to compute would-have P&L.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from datadesk.db import PLATFORM_DB

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_signals (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    source    TEXT NOT NULL,   -- which monitor/strategy emitted it
    ticker    TEXT NOT NULL,
    side      TEXT NOT NULL,
    weight    REAL NOT NULL,
    ref_price REAL,            -- price at signal time (would-have entry)
    reason    TEXT,
    executed  INTEGER NOT NULL DEFAULT 0  -- 1 if an armed broker order followed
);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(db_path or PLATFORM_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    # Migration: add order_id column if not present (for existing DBs)
    cols = {row[1] for row in con.execute("PRAGMA table_info(shadow_signals)")}
    if "order_id" not in cols:
        con.execute("ALTER TABLE shadow_signals ADD COLUMN order_id TEXT")
        con.commit()
    return con


def record_signal(
    source: str,
    ticker: str,
    side: str,
    weight: float,
    ref_price: float | None = None,
    reason: str = "",
    executed: bool = False,
    db_path: Path | None = None,
) -> int:
    with _connect(db_path) as con:
        cur = con.execute(
            "INSERT INTO shadow_signals (ts, source, ticker, side, weight, ref_price, reason, executed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(UTC).isoformat(),
                source,
                ticker,
                side,
                weight,
                ref_price,
                reason,
                1 if executed else 0,
            ),
        )
        return cur.lastrowid


def load_signals(limit: int = 100, db_path: Path | None = None) -> pd.DataFrame:
    with _connect(db_path) as con:
        return pd.read_sql(
            "SELECT * FROM shadow_signals ORDER BY id DESC LIMIT ?", con, params=(limit,)
        )

def update_order_id(row_id: int, order_id: str | None, db_path: Path | None = None) -> None:
    if order_id is None:
        return
    with _connect(db_path) as con:
        con.execute(
            "UPDATE shadow_signals SET order_id = ? WHERE id = ?",
            (order_id, row_id),
        )
