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

CREATE TABLE IF NOT EXISTS analyst_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    analyst     TEXT NOT NULL,   -- 'research' | 'strategy' | 'risk'
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,   -- plain-text briefing
    data        TEXT NOT NULL    -- JSON payload (findings, scores, alerts)
);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(db_path or PLATFORM_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    return con


def save_report(analyst: str, title: str, body: str, data: dict, db_path: Path | None = None) -> None:
    with _connect(db_path) as con:
        con.execute(
            "INSERT INTO analyst_reports (created_at, analyst, title, body, data) VALUES (?,?,?,?,?)",
            (datetime.now(UTC).isoformat(), analyst, title, body, json.dumps(data)),
        )


def load_reports(analyst: str | None = None, limit: int = 20, db_path: Path | None = None) -> list[dict]:
    with _connect(db_path) as con:
        if analyst:
            rows = con.execute(
                "SELECT created_at, analyst, title, body, data FROM analyst_reports "
                "WHERE analyst=? ORDER BY id DESC LIMIT ?", (analyst, limit)
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT created_at, analyst, title, body, data FROM analyst_reports "
                "ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [{"created_at": r[0], "analyst": r[1], "title": r[2], "body": r[3], "data": json.loads(r[4])} for r in rows]


def save_backtest_run(
    name: str,
    params: dict,
    metrics: dict,
    equity: pd.Series,
    db_path: Path | None = None,
) -> None:
    """Upsert a backtest run — same name replaces the previous entry."""
    points = [[d.strftime("%Y-%m-%d"), round(float(v), 6)] for d, v in equity.items()]
    with _connect(db_path) as con:
        con.execute("DELETE FROM backtest_runs WHERE name = ?", (name,))
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
    """Return latest run per unique name, ordered by CAGR descending."""
    with _connect(db_path) as con:
        rows = con.execute(
            """
            SELECT run_at, name, params, metrics, equity
            FROM backtest_runs
            WHERE id IN (SELECT MAX(id) FROM backtest_runs GROUP BY name)
            ORDER BY json_extract(metrics, '$.cagr') DESC
            LIMIT ?
            """,
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
