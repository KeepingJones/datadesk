"""
One-shot migration: pull all useful tables from trading-bot databases into
datadesk/altdata.db.  Safe to re-run — uses INSERT OR IGNORE on PKs.

Sources:
  ../trading-bot/alt_data.db          → congress_trading, insiders, legislator_profiles,
                                         news_articles, wallstreetbets, equity_reference,
                                         wikipedia, price_ticks, macro_indicators
  ../trading-bot/historical_backtest.db → macro_history, filings, news_extended,
                                          ticker_metadata, t212_collections
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DEST = ROOT / "altdata.db"
SRC_ALT = ROOT.parent / "trading-bot" / "alt_data.db"
SRC_HIST = ROOT.parent / "trading-bot" / "historical_backtest.db"

for src in (SRC_ALT, SRC_HIST):
    if not src.exists():
        print(f"ERROR: source not found: {src}")
        sys.exit(1)

con = sqlite3.connect(DEST)
con.execute("PRAGMA journal_mode=WAL")

# ── Schema for new tables ────────────────────────────────────────────────────
con.executescript("""
CREATE TABLE IF NOT EXISTS congress_trading (
    id               INTEGER PRIMARY KEY,
    ticker           TEXT,
    transaction_date TEXT,
    disclosure_date  TEXT,
    filer_name       TEXT,
    chamber          TEXT,
    transaction_type TEXT,
    amount_range     TEXT,
    created_at       TEXT
);

CREATE TABLE IF NOT EXISTS insiders (
    id               INTEGER PRIMARY KEY,
    accession_number TEXT UNIQUE,
    ticker           TEXT,
    transaction_date TEXT,
    filing_date      TEXT,
    issuer_name      TEXT,
    filer_name       TEXT,
    is_officer       INTEGER,
    is_director      INTEGER,
    officer_title    TEXT,
    transaction_type TEXT,
    shares           REAL,
    price_per_share  REAL,
    amount_usd       REAL,
    created_at       TEXT
);

CREATE TABLE IF NOT EXISTS legislator_profiles (
    bioguide_id  TEXT PRIMARY KEY,
    full_name    TEXT,
    committees   TEXT,
    last_updated TEXT
);

CREATE TABLE IF NOT EXISTS news_articles (
    id              INTEGER PRIMARY KEY,
    ticker          TEXT,
    source          TEXT,
    headline        TEXT,
    url             TEXT UNIQUE,
    sentiment_label TEXT,
    sentiment_score REAL,
    published_at    TEXT,
    created_at      TEXT,
    summary         TEXT
);

CREATE TABLE IF NOT EXISTS wallstreetbets (
    id             INTEGER PRIMARY KEY,
    ticker         TEXT,
    mention_count  INTEGER,
    sentiment_score REAL,
    date           TEXT,
    created_at     TEXT,
    source         TEXT
);

CREATE TABLE IF NOT EXISTS equity_reference (
    ticker_standard TEXT PRIMARY KEY,
    exchange        TEXT,
    ticker_alpaca   TEXT,
    ticker_t212     TEXT,
    isin            TEXT,
    name            TEXT,
    sector          TEXT,
    industry        TEXT,
    market_cap      REAL,
    currency        TEXT,
    last_updated    TEXT
);

CREATE TABLE IF NOT EXISTS wikipedia (
    id             INTEGER PRIMARY KEY,
    ticker         TEXT,
    page_views     INTEGER,
    view_momentum  REAL,
    date           TEXT,
    created_at     TEXT
);

CREATE TABLE IF NOT EXISTS price_ticks (
    id          INTEGER PRIMARY KEY,
    ticker      TEXT,
    close_price REAL,
    volume      REAL,
    source      TEXT,
    recorded_at TEXT
);

CREATE TABLE IF NOT EXISTS macro_indicators (
    indicator TEXT,
    symbol    TEXT,
    value     REAL,
    change_5d REAL,
    date      TEXT,
    data_date TEXT,
    PRIMARY KEY (indicator, data_date)
);

-- From historical_backtest.db
CREATE TABLE IF NOT EXISTS macro_history (
    Date      TEXT,
    value     REAL,
    indicator TEXT,
    PRIMARY KEY (indicator, Date)
);

CREATE TABLE IF NOT EXISTS filings (
    form      TEXT,
    date      TEXT,
    accession TEXT PRIMARY KEY,
    ticker    TEXT
);

CREATE TABLE IF NOT EXISTS news_extended (
    ticker     TEXT,
    title      TEXT,
    url        TEXT PRIMARY KEY,
    published  TEXT,
    source     TEXT
);

CREATE TABLE IF NOT EXISTS ticker_metadata (
    ticker               TEXT PRIMARY KEY,
    name                 TEXT,
    gics_sector          TEXT,
    gics_industry_group  TEXT,
    market_cap           REAL,
    sp_rating            TEXT,
    t212_isa             INTEGER,
    leverage_factor      REAL,
    sector               TEXT,
    industry             TEXT
);

CREATE TABLE IF NOT EXISTS t212_collections (
    ticker          TEXT,
    collection_name TEXT,
    rank            INTEGER,
    holders_change  REAL,
    updated_at      TEXT,
    PRIMARY KEY (ticker, collection_name)
);
""")
con.commit()

def attach_and_copy(src_path: Path, copies: list[tuple[str, str, str]]):
    """copies = [(src_table, dest_table, insert_sql), ...]"""
    src_con = sqlite3.connect(src_path)
    for src_table, dest_table, insert_sql in copies:
        rows = src_con.execute(f"SELECT * FROM {src_table}").fetchall()
        if not rows:
            print(f"  {src_table}: empty, skipping")
            continue
        try:
            con.executemany(insert_sql, rows)
            con.commit()
            print(f"  {src_table} → {dest_table}: {len(rows):,} rows")
        except Exception as e:
            print(f"  {src_table}: ERROR {e}")
    src_con.close()

print(f"\nMigrating from {SRC_ALT.name}...")
attach_and_copy(SRC_ALT, [
    ("congress_trading", "congress_trading",
     "INSERT OR IGNORE INTO congress_trading VALUES (?,?,?,?,?,?,?,?,?)"),
    ("insiders", "insiders",
     "INSERT OR IGNORE INTO insiders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"),
    ("legislator_profiles", "legislator_profiles",
     "INSERT OR IGNORE INTO legislator_profiles VALUES (?,?,?,?)"),
    ("news_articles", "news_articles",
     "INSERT OR IGNORE INTO news_articles VALUES (?,?,?,?,?,?,?,?,?,?)"),
    ("wallstreetbets", "wallstreetbets",
     "INSERT OR IGNORE INTO wallstreetbets VALUES (?,?,?,?,?,?,?)"),
    ("equity_reference", "equity_reference",
     "INSERT OR IGNORE INTO equity_reference VALUES (?,?,?,?,?,?,?,?,?,?,?)"),
    ("wikipedia", "wikipedia",
     "INSERT OR IGNORE INTO wikipedia VALUES (?,?,?,?,?,?)"),
    ("price_ticks", "price_ticks",
     "INSERT OR IGNORE INTO price_ticks VALUES (?,?,?,?,?,?)"),
    ("macro_indicators", "macro_indicators",
     "INSERT OR IGNORE INTO macro_indicators VALUES (?,?,?,?,?,?)"),
])

print(f"\nMigrating from {SRC_HIST.name}...")
attach_and_copy(SRC_HIST, [
    ("macro", "macro_history",
     "INSERT OR IGNORE INTO macro_history VALUES (?,?,?)"),
    ("filings", "filings",
     "INSERT OR IGNORE INTO filings VALUES (?,?,?,?)"),
    ("news", "news_extended",
     "INSERT OR IGNORE INTO news_extended VALUES (?,?,?,?,?)"),
    ("ticker_metadata", "ticker_metadata",
     "INSERT OR IGNORE INTO ticker_metadata VALUES (?,?,?,?,?,?,?,?,?,?)"),
    ("t212_collections", "t212_collections",
     "INSERT OR IGNORE INTO t212_collections VALUES (?,?,?,?,?)"),
])

# Final row counts
print("\nFinal altdata.db contents:")
tables = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
for (t,) in tables:
    n = con.execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()[0]
    print(f"  {t}: {n:,}")

con.close()
print("\nDone.")
