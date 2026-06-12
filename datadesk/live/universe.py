import logging
import sqlite3

import yfinance as yf

from datadesk.db import PLATFORM_DB

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS monitored_universe (
    ticker TEXT PRIMARY KEY,
    asset_class TEXT NOT NULL,
    currency TEXT NOT NULL,
    added_at TEXT NOT NULL
);
"""

def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(PLATFORM_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    
    # Pre-populate defaults if empty
    count = con.execute("SELECT COUNT(*) FROM monitored_universe").fetchone()[0]
    if count == 0:
        from datetime import UTC, datetime
        now = datetime.now(UTC).isoformat()
        defaults = [
            ("NVDA", "equity", "USD", now),
            ("TSM", "equity", "USD", now),
            ("SMCI", "equity", "USD", now),
            ("DELL", "equity", "USD", now),
            ("AAPL", "equity", "USD", now)
        ]
        con.executemany("INSERT INTO monitored_universe (ticker, asset_class, currency, added_at) VALUES (?, ?, ?, ?)", defaults)
        con.commit()
    return con

def get_active_universe() -> list[str]:
    with _connect() as con:
        rows = con.execute("SELECT ticker FROM monitored_universe").fetchall()
    return [r[0] for r in rows]

def add_ticker(ticker: str) -> dict:
    ticker = ticker.upper()
    # Validate via yfinance
    ticker_obj = yf.Ticker(ticker)
    info = ticker_obj.info
    if not info or 'symbol' not in info:
        return {"status": "error", "message": f"Invalid ticker: {ticker}"}
    
    currency = info.get('currency', 'USD')
    quote_type = info.get('quoteType', 'EQUITY').lower()
    
    from datetime import UTC, datetime
    now = datetime.now(UTC).isoformat()
    
    try:
        with _connect() as con:
            con.execute(
                "INSERT INTO monitored_universe (ticker, asset_class, currency, added_at) VALUES (?, ?, ?, ?)",
                (ticker, quote_type, currency, now)
            )
        logger.info(f"Added {ticker} to monitored universe")
        
        # Auto-backfill historical data for the new ticker
        from datadesk.ingest.backfill import backfill_smart
        backfill_smart([ticker])
        
        return {"status": "success", "ticker": ticker, "currency": currency}
    except sqlite3.IntegrityError:
        return {"status": "error", "message": f"{ticker} is already in the universe"}
