"""
Trump Truth Social collector.

Source: CNN's research archive (verified 2026-06-11: 33,891 posts,
2022-02 → present, exact timestamps). No scraping needed.

Point-in-time discipline: every row stores `observed_at` (when WE collected it)
alongside `created_at` (when it was posted). Backtests of the communications
signal must use observed_at for anything collected live, and treat the bulk
archive import as historical-context-only where collection lag is unknown.
"""

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests

from datadesk.config import DB_PATH

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://ix.cnn.io/data/truth-social/truth_archive.json"
ALTDATA_DB = Path(str(DB_PATH).replace("datadesk.db", "altdata.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trump_posts (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,   -- ISO, from the platform
    observed_at TEXT NOT NULL,   -- ISO, when we collected it
    content     TEXT,
    url         TEXT,
    replies     INTEGER,
    reblogs     INTEGER,
    favourites  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_trump_created ON trump_posts(created_at);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(db_path or ALTDATA_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    return con


def fetch_archive(timeout: int = 120) -> list[dict]:
    r = requests.get(ARCHIVE_URL, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError(f"unexpected archive shape: {type(data)}")
    return data


def save_posts(posts: list[dict], db_path: Path | None = None) -> int:
    """Insert new posts only (existing ids untouched → observed_at stays first-seen)."""
    observed = datetime.now(UTC).isoformat()
    rows = [
        (
            p["id"],
            p["created_at"],
            observed,
            p.get("content", ""),
            p.get("url", ""),
            p.get("replies_count"),
            p.get("reblogs_count"),
            p.get("favourites_count"),
        )
        for p in posts
        if p.get("id") and p.get("created_at")
    ]
    with _connect(db_path) as con:
        before = con.execute("SELECT COUNT(*) FROM trump_posts").fetchone()[0]
        con.executemany(
            "INSERT OR IGNORE INTO trump_posts "
            "(id, created_at, observed_at, content, url, replies, reblogs, favourites) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        added = con.execute("SELECT COUNT(*) FROM trump_posts").fetchone()[0] - before
    logger.info(f"trump collector: {added} new posts ({len(rows)} in feed)")
    return added


def collect(db_path: Path | None = None) -> int:
    """Fetch the live archive and store anything new. The nightly/polling entry point."""
    return save_posts(fetch_archive(), db_path=db_path)


def load_posts(
    start: str | None = None,
    end: str | None = None,
    db_path: Path | None = None,
) -> pd.DataFrame:
    query = "SELECT * FROM trump_posts WHERE 1=1"
    params: list = []
    if start:
        query += " AND created_at >= ?"
        params.append(start)
    if end:
        query += " AND created_at <= ?"
        params.append(end)
    with _connect(db_path) as con:
        df = pd.read_sql(query + " ORDER BY created_at", con, params=params)
    if not df.empty:
        df["created_at"] = pd.to_datetime(df["created_at"], format="ISO8601")
    return df
