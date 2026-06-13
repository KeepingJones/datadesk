"""
One-off migration: import daily bars from the legacy trading-bot store
(alt_data.db price_history) into the canonical history store.

Run:  python -m datadesk.history.migrate [path-to-alt_data.db]
Migration already completed 2026-06-11; this is kept for reruns against a fresh DB.
"""

import logging
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from datadesk.history.store import save_bars

logger = logging.getLogger(__name__)

DEFAULT_LEGACY = Path("altdata.db")  # consolidated 2026-06-13; price_history kept for migration reruns


def migrate_price_history(legacy_db: Path = DEFAULT_LEGACY) -> int:
    """Copy price_history rows into daily_bars. Returns rows written."""
    con = sqlite3.connect(f"file:{legacy_db}?mode=ro", uri=True)
    df = pd.read_sql(
        "SELECT ticker, timestamp, open, high, low, close, volume FROM price_history", con
    )
    con.close()

    # Legacy timestamps are mixed formats ('2021-04-26 04:00:00' and ISO with tz) —
    # normalise everything to a plain date
    df["date"] = pd.to_datetime(df["timestamp"], format="mixed", utc=True).dt.strftime("%Y-%m-%d")
    df = df.drop(columns=["timestamp"])

    # One bar per (ticker, date): keep the last seen
    df = df.drop_duplicates(subset=["ticker", "date"], keep="last")

    return save_bars(df, source="legacy_trading_bot")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    legacy = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LEGACY
    n = migrate_price_history(legacy)
    print(f"Migrated {n} bars from {legacy}")
