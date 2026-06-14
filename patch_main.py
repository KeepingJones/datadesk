import re

with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add cmd_init_db function before cmd_backtest
init_db_func = """
def cmd_init_db() -> None:
    from datadesk.db import PLATFORM_DB
    from datadesk.config import DB_PATH
    import sqlite3
    from pathlib import Path
    from datadesk.history.store import connect as history_connect
    from datadesk.live.shadow import _connect as shadow_connect

    with history_connect(): pass
    with shadow_connect(): pass

    with sqlite3.connect(PLATFORM_DB) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript('''
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            params TEXT NOT NULL,
            metrics TEXT NOT NULL,
            equity_curve TEXT,
            ts TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS active_universe (
            ticker TEXT PRIMARY KEY,
            added_date TEXT NOT NULL,
            reason TEXT
        );
        CREATE TABLE IF NOT EXISTS analyst_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analyst TEXT NOT NULL,
            ts TEXT NOT NULL,
            body TEXT,
            data TEXT
        );
        ''')

    ALTDATA_DB = Path(str(DB_PATH).replace("datadesk.db", "altdata.db"))
    with sqlite3.connect(ALTDATA_DB) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript('''
        CREATE TABLE IF NOT EXISTS equity_info (
            ticker TEXT PRIMARY KEY, name TEXT, sector TEXT, industry TEXT,
            country TEXT, exchange TEXT, description TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS equity_ratios (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
            fetched_at TEXT NOT NULL, forward_pe REAL, trailing_pe REAL,
            price_to_book REAL, price_to_sales REAL, ev_to_ebitda REAL,
            dividend_yield REAL, beta REAL, gross_margin REAL, operating_margin REAL,
            profit_margin REAL, return_on_equity REAL, return_on_assets REAL,
            debt_to_equity REAL, current_ratio REAL
        );
        CREATE TABLE IF NOT EXISTS equity_financials (
            ticker TEXT NOT NULL, fiscal_year INTEGER NOT NULL, revenue REAL,
            gross_profit REAL, ebit REAL, net_income REAL, eps_diluted REAL,
            PRIMARY KEY(ticker, fiscal_year)
        );
        CREATE TABLE IF NOT EXISTS equity_balance (
            ticker TEXT NOT NULL, fiscal_year INTEGER NOT NULL, total_assets REAL,
            total_liabilities REAL, cash_and_equiv REAL, total_debt REAL,
            book_value REAL, PRIMARY KEY(ticker, fiscal_year)
        );
        ''')
    print("Databases initialized.")

def cmd_backtest() -> None:"""
content = content.replace("def cmd_backtest() -> None:", init_db_func)

# 2. Add --preset logic in cmd_backfill
backfill_preset_logic = """
_BACKFILL_PRESETS = {
    "ai_semi": [
        "NVDA", "AMD", "AVGO", "ARM", "MRVL", "KLAC", "AMAT", "LRCX",
        "QCOM", "MU", "INTC", "SMCI", "COHU", "FORM", "ONTO", "ON", "NXPI",
        "MSFT", "GOOGL", "META", "AMZN", "PLTR", "NOW", "CRM", "ORCL",
        "SNOW", "DDOG", "CDNS", "SNPS"
    ]
}

def cmd_backfill(tickers: list[str], source: str, skip_fundamentals: bool = False, preset: str = None) -> None:
    if preset and preset in _BACKFILL_PRESETS:
        tickers = _BACKFILL_PRESETS[preset]
        print(f"Using preset '{preset}': {len(tickers)} tickers")

    import time
    if source == "massive":
        from datadesk.ingest.massive import backfill_massive
        written = backfill_massive(tickers)
    else:
        from datadesk.ingest.backfill import backfill_history
        written = {}
        for i in range(0, len(tickers), 5):
            batch = tickers[i:i+5]
            print(f"Backfilling batch {i//5 + 1}: {batch}")
            try:
                written.update(backfill_history(batch))
            except Exception as e:
                print(f"Warning: Batch {batch} failed ({e}). Rate limited? Sleeping 5s...")
                time.sleep(5)
            time.sleep(2)
"""
content = re.sub(r'def cmd_backfill.*?written = backfill_history\(tickers\)', backfill_preset_logic, content, flags=re.DOTALL)

# 3. Update the error message in cmd_backtest
old_err = """print(
            "History store is empty — run: python -m datadesk.history.migrate "
            "or python main.py backfill <tickers>"
        )"""
new_err = """print(
            "History store is empty — run: python main.py init-db "
            "then python main.py backfill --preset ai_semi"
        )"""
content = content.replace(old_err, new_err)

# 4. Modify argparse section
# Make command required=False
content = content.replace('dest="command", required=True', 'dest="command", required=False')

# Add help strings to subcommands and add init-db and backfill preset
argparse_replacements = [
    ('sub.add_parser("backtest")', 'sub.add_parser("backtest", help="Run momentum+trend backtest")'),
    ('sub.add_parser("serve")', 'sub.add_parser("serve", help="Start the ops console")'),
    ('sub.add_parser("collect-trump")', 'sub.add_parser("collect-trump", help="Refresh Trump corpus")'),
    ('sub.add_parser("coverage")', 'sub.add_parser("coverage", help="Print history store coverage")'),
    ('sub.add_parser("holdout")', 'sub.add_parser("holdout", help="Run holdout strategy comparison")'),
    ('sub.add_parser("tax-compare")', 'sub.add_parser("tax-compare", help="After-tax simulation comparison")'),
    ('sub.add_parser("universe")', 'sub.add_parser("universe", help="Print platform availability per ticker")'),
    ('sub.add_parser("weekly-update")', 'sub.add_parser("weekly-update", help="Gap-fill prices and fundamentals")'),
    ('sub.add_parser("index-seed")', 'sub.add_parser("index-seed", help="Populate index_memberships")'),
    ('sub.add_parser("signal-audit")', 'sub.add_parser("signal-audit", help="Look-ahead bias audit")'),
    ('sub.add_parser("screen")', 'sub.add_parser("screen", help="Forward screener for top buys")'),
]

for old, new in argparse_replacements:
    content = content.replace(old, new)

# Add init-db to subparsers
content = content.replace('sub.add_parser("backtest", help="Run momentum+trend backtest")',
                          'sub.add_parser("init-db", help="Initialize database schemas")\n    sub.add_parser("backtest", help="Run momentum+trend backtest")')

# Add preset to backfill parser
preset_arg = 'p_bf.add_argument("--preset", choices=["ai_semi"], help="Use predefined ticker list")\n    p_bf.add_argument("tickers"'
content = content.replace('p_bf.add_argument("tickers"', preset_arg)
content = content.replace('p_bf.add_argument("tickers", nargs="+")', 'p_bf.add_argument("tickers", nargs="*", help="Tickers to backfill")')

# Handle default behavior and cmd_backfill args
main_if = """
    if args.command is None:
        print("DataDesk Quickstart:")
        print("  python main.py init-db")
        print("  python main.py backfill --preset ai_semi")
        print("  python main.py backtest")
        print("  python main.py serve")
        import sys; sys.exit(0)

    if args.command == "init-db":
        cmd_init_db()
    elif args.command == "backtest":"""
content = content.replace('if args.command == "backtest":', main_if)

content = content.replace('cmd_backfill(args.tickers, args.source, skip_fundamentals=args.no_fundamentals)', 
                          'cmd_backfill(args.tickers, args.source, skip_fundamentals=args.no_fundamentals, preset=getattr(args, "preset", None))')

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched main.py successfully.")
