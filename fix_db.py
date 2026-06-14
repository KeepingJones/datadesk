import sqlite3
from pathlib import Path
from datadesk.config import DB_PATH

ALTDATA_DB = Path(str(DB_PATH).replace("datadesk.db", "altdata.db"))

def alter_table_add_column(con, table, col_def):
    col_name = col_def.split()[0]
    try:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
        print(f"Added {col_name} to {table}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            pass
        else:
            print(f"Error adding {col_name} to {table}: {e}")

with sqlite3.connect(ALTDATA_DB) as con:
    # Add all missing columns from the true schema to equity_info
    info_cols = [
        "currency TEXT",
        "market_cap REAL",
        "shares_outstanding REAL",
        "employees INTEGER",
        "website TEXT"
    ]
    for c in info_cols:
        alter_table_add_column(con, "equity_info", c)

    # Add all missing columns to equity_ratios
    ratio_cols = [
        "market_cap REAL",
        "payout_ratio REAL",
        "revenue REAL",
        "revenue_growth REAL",
        "net_margin REAL",
        "roa REAL",
        "free_cashflow REAL",
        "week52_high REAL",
        "week52_low REAL",
        "week52_change REAL",
        "short_pct_float REAL"
    ]
    for c in ratio_cols:
        alter_table_add_column(con, "equity_ratios", c)

print("DB schemas upgraded.")
