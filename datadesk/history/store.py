"""
Canonical daily OHLCV store.

SQLite with WAL — one row per (ticker, date). All loaders return pandas
frames indexed by DatetimeIndex, wide format (columns = tickers) for the
backtest engine.
"""

import logging
import sqlite3
from pathlib import Path

import pandas as pd

from datadesk.config import DB_PATH

logger = logging.getLogger(__name__)

HISTORY_DB = Path(str(DB_PATH).replace("datadesk.db", "history.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_bars (
    ticker TEXT NOT NULL,
    date   TEXT NOT NULL,   -- ISO yyyy-mm-dd
    open   REAL,
    high   REAL,
    low    REAL,
    close  REAL NOT NULL,
    volume REAL,
    source TEXT NOT NULL DEFAULT 'unknown',
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_bars_date ON daily_bars(date);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(db_path or HISTORY_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    return con


def save_bars(df: pd.DataFrame, source: str, db_path: Path | None = None) -> int:
    """
    Upsert daily bars. Expects columns: ticker, date, open, high, low, close, volume.
    Returns rows written.
    """
    required = {"ticker", "date", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"save_bars: missing columns {missing}")

    rows = [
        (
            r["ticker"],
            pd.Timestamp(r["date"]).strftime("%Y-%m-%d"),
            r.get("open"),
            r.get("high"),
            r.get("low"),
            r["close"],
            r.get("volume"),
            source,
        )
        for _, r in df.iterrows()
        if pd.notna(r["close"])
    ]
    with connect(db_path) as con:
        con.executemany(
            "INSERT OR REPLACE INTO daily_bars "
            "(ticker, date, open, high, low, close, volume, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    logger.info(f"history: wrote {len(rows)} bars from {source}")
    return len(rows)


def load_closes(
    tickers: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    db_path: Path | None = None,
) -> pd.DataFrame:
    """Wide close-price frame: index=date, columns=tickers."""
    return _load_field("close", tickers, start, end, db_path)


def load_volumes(
    tickers: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    db_path: Path | None = None,
) -> pd.DataFrame:
    return _load_field("volume", tickers, start, end, db_path)


def _load_field(field, tickers, start, end, db_path) -> pd.DataFrame:
    query = f"SELECT ticker, date, {field} FROM daily_bars WHERE 1=1"  # noqa: S608 — field is internal
    params: list = []
    if tickers:
        query += f" AND ticker IN ({','.join('?' * len(tickers))})"
        params.extend(tickers)
    if start:
        query += " AND date >= ?"
        params.append(start)
    if end:
        query += " AND date <= ?"
        params.append(end)

    with connect(db_path) as con:
        df = pd.read_sql(query, con, params=params)
    if df.empty:
        return pd.DataFrame()
    wide = df.pivot(index="date", columns="ticker", values=field)
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def coverage(db_path: Path | None = None) -> pd.DataFrame:
    """Per-ticker row counts and date ranges — feeds the pipelines dashboard."""
    with connect(db_path) as con:
        return pd.read_sql(
            "SELECT ticker, COUNT(*) AS rows, MIN(date) AS first, MAX(date) AS last "
            "FROM daily_bars GROUP BY ticker ORDER BY ticker",
            con,
        )
