"""
Platform store: backtest runs, collector health — everything the dashboard reads.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from datadesk.config import DB_PATH

PLATFORM_DB = Path(str(DB_PATH).replace("datadesk.db", "platform.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at      TEXT NOT NULL,
    name        TEXT NOT NULL,
    params      TEXT NOT NULL,   -- JSON
    metrics     TEXT NOT NULL,   -- JSON
    equity      TEXT NOT NULL    -- JSON [[date, value], ...]
);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(db_path or PLATFORM_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    return con


def save_backtest_run(
    name: str,
    params: dict,
    metrics: dict,
    equity: pd.Series,
    db_path: Path | None = None,
) -> None:
    points = [[d.strftime("%Y-%m-%d"), round(float(v), 6)] for d, v in equity.items()]
    with _connect(db_path) as con:
        con.execute(
            "INSERT INTO backtest_runs (run_at, name, params, metrics, equity) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                datetime.now(UTC).isoformat(),
                name,
                json.dumps(params),
                json.dumps(metrics),
                json.dumps(points),
            ),
        )


def load_backtest_runs(limit: int = 10, db_path: Path | None = None) -> list[dict]:
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT run_at, name, params, metrics, equity FROM backtest_runs "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "run_at": r[0],
            "name": r[1],
            "params": json.loads(r[2]),
            "metrics": json.loads(r[3]),
            "equity": json.loads(r[4]),
        }
        for r in rows
    ]
