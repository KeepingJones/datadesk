"""
Index membership tracking.

Records which tickers belong to which benchmark indices and their approximate
weight. Stored in altdata.db `index_memberships` table.

Used to:
  1. Warn when a momentum portfolio has >X% overlap with a single index
     (you're just running a closet-index fund at that point)
  2. Identify index-driven momentum (forced institutional buying when a stock
     is added to an index — creates price pressure that looks like "signal")
  3. Surface index weight when generating ticker theses

Schema:
  index_memberships(ticker TEXT, index_ticker TEXT, approx_weight REAL,
                    source TEXT, as_of_date TEXT)
  PRIMARY KEY (ticker, index_ticker)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from datadesk.config import ALTDATA_DB

# Known index constituent mappings — approximate weights as of early 2025
# Source: public ETF constituent filings; update manually when large rebalances occur
# (index_ticker → list of (constituent_ticker, approx_weight_pct))
_INDEX_CONSTITUENTS: dict[str, list[tuple[str, float]]] = {
    "SMH": [   # VanEck Semiconductor ETF (top holdings, ~85% of ETF)
        ("NVDA", 20.0), ("TSM", 10.5), ("ASML", 7.5), ("AVGO", 7.0),
        ("AMD", 5.0),   ("QCOM", 4.5), ("INTC", 3.5), ("LRCX", 3.5),
        ("KLAC", 3.0),  ("AMAT", 3.0), ("MU", 2.5),   ("MCHP", 2.0),
        ("ON", 1.5),    ("NXPI", 1.5), ("MRVL", 1.5), ("STM", 1.5),
        ("GFS", 1.0),   ("COHU", 0.5),
    ],
    "QQQ": [   # Invesco Nasdaq-100 (top holdings, ~70% of ETF)
        ("AAPL", 8.5),  ("MSFT", 8.0),  ("NVDA", 7.5),  ("AMZN", 5.5),
        ("META", 4.5),  ("GOOG", 4.0),  ("GOOGL", 4.0), ("TSLA", 3.5),
        ("AVGO", 3.0),  ("COST", 2.5),  ("NFLX", 2.0),  ("ADBE", 1.5),
        ("AMD", 1.2),   ("QCOM", 1.2),  ("CSCO", 1.2),  ("AMAT", 1.0),
        ("SNPS", 0.8),  ("CDNS", 0.8),  ("MRVL", 0.7),  ("KLAC", 0.7),
        ("MCHP", 0.5),  ("LRCX", 0.5),
    ],
    "SPY": [   # S&P 500 SPDR — top 20 by weight
        ("AAPL", 6.5),  ("MSFT", 6.0),  ("NVDA", 5.5),  ("AMZN", 3.8),
        ("META", 2.8),  ("GOOG", 2.0),  ("GOOGL", 1.8), ("AVGO", 1.8),
        ("TSLA", 1.6),  ("JPM", 1.4),   ("WMT", 1.0),   ("ORCL", 0.8),
        ("NFLX", 0.8),  ("IBM", 0.6),   ("GS", 0.5),    ("AMX", 0.5),
        ("NOW", 0.5),   ("NEE", 0.4),   ("MSCI", 0.3),
    ],
    "XLK": [   # Technology Select SPDR
        ("AAPL", 20.0), ("NVDA", 19.0), ("MSFT", 19.0), ("AVGO", 7.0),
        ("AMD", 2.5),   ("QCOM", 2.5),  ("ORCL", 2.5),  ("CSCO", 2.0),
        ("NOW", 1.8),   ("ADBE", 1.5),  ("AMAT", 1.3),  ("SNPS", 1.0),
        ("CRM", 1.0),   ("KLAC", 0.9),  ("LRCX", 0.8),  ("CDNS", 0.8),
        ("TXN", 0.7),   ("MU", 0.7),    ("MRVL", 0.6),
    ],
}

_AS_OF = "2025-01-01"  # approximate date for these weights


def create_index_membership_table(db_path: Path | None = None) -> None:
    """Create the index_memberships table if it doesn't exist."""
    db = db_path or ALTDATA_DB
    con = sqlite3.connect(db)
    con.execute("""
        CREATE TABLE IF NOT EXISTS index_memberships (
            ticker       TEXT NOT NULL,
            index_ticker TEXT NOT NULL,
            approx_weight REAL,
            source       TEXT,
            as_of_date   TEXT,
            PRIMARY KEY (ticker, index_ticker)
        )
    """)
    con.commit()
    con.close()


def upsert_index_memberships(
    db_path: Path | None = None,
    constituents: dict[str, list[tuple[str, float]]] | None = None,
    as_of: str = _AS_OF,
    source: str = "manual",
) -> int:
    """
    Insert or replace index membership records.

    Returns number of rows upserted.
    """
    db = db_path or ALTDATA_DB
    data = constituents or _INDEX_CONSTITUENTS
    create_index_membership_table(db_path=db)

    rows = [
        (ticker, idx_ticker, weight, source, as_of)
        for idx_ticker, members in data.items()
        for ticker, weight in members
    ]
    con = sqlite3.connect(db)
    con.executemany(
        "INSERT OR REPLACE INTO index_memberships "
        "(ticker, index_ticker, approx_weight, source, as_of_date) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM index_memberships").fetchone()[0]
    con.close()
    return n


def load_index_memberships(
    tickers: list[str] | None = None,
    db_path: Path | None = None,
) -> pd.DataFrame:
    """Return DataFrame of (ticker, index_ticker, approx_weight, source, as_of_date)."""
    db = db_path or ALTDATA_DB
    try:
        con = sqlite3.connect(db)
        if tickers:
            placeholders = ",".join("?" * len(tickers))
            df = pd.read_sql(
                f"SELECT * FROM index_memberships WHERE ticker IN ({placeholders})",
                con, params=tickers,
            )
        else:
            df = pd.read_sql("SELECT * FROM index_memberships", con)
        con.close()
        return df
    except Exception:
        return pd.DataFrame(columns=["ticker", "index_ticker", "approx_weight", "source", "as_of_date"])


def index_overlap_report(
    portfolio_tickers: list[str],
    db_path: Path | None = None,
) -> dict[str, float]:
    """
    Given a list of tickers in a portfolio (equal-weight assumed), return
    dict {index_ticker: effective_overlap_pct} showing what fraction of
    portfolio weight is also index constituents.

    e.g. if 6/10 holdings are QQQ members, overlap is 60%.
    """
    memberships = load_index_memberships(tickers=portfolio_tickers, db_path=db_path)
    if memberships.empty:
        return {}
    n = len(portfolio_tickers)
    overlap = (
        memberships.groupby("index_ticker")["ticker"]
        .count()
        .apply(lambda count: round(count / n * 100, 1))
        .to_dict()
    )
    return overlap
