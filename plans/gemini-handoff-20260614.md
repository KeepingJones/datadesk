# DataDesk — Implementation Handoff for Gemini

This document is a complete implementation brief for DataDesk after 4 rounds of review
(CEO, Engineering, Design, DX). Every task below has been spec'd and approved. Implement
them in the order shown. All task have specific file paths and line numbers.

---

## Absolute Constraints — Read First

1. `PAPER_TRADE_MODE = True` in `datadesk/config.py` — **NEVER change this**.
2. `DATADESK_ARM_BROKER` defaults to `"0"` in `datadesk/live/oms.py:62` — broker calls never
   execute in shadow mode. Never default it to `"1"`.
3. Do **not** `git push` unless explicitly told. `.env` contains live API keys and must
   never be committed.
4. All trading is paper-only. Do not add any path that would make real money trades.

---

## Repo Structure (relevant paths)

```
datadesk/
├── live/
│   ├── oms.py              OMS fast-path (shadow-first)
│   ├── shadow.py           Shadow signal audit trail (SQLite)
│   └── monitors/
│       └── rebalancer.py   Daily MOC rebalancer
├── ingest/
│   ├── backfill.py         yfinance price history backfill
│   └── t212_client.py      T212 REST client
main.py                     CLI entrypoint (argparse)
README.md                   Public-facing docs
.env.example                Safe credential template (already committed)
tests/test_oms.py           10 existing OMS tests
```

---

## BLOCK 1: Code Fixes (from Engineering Review)

These are bugs. Ship all four.

### Fix A — `is_armed` property (`datadesk/live/oms.py:93`)

**Current code:**
```python
@property
def is_armed(self) -> bool:
    return self.alpaca is not None
```

**Change to:**
```python
@property
def is_armed(self) -> bool:
    return self.alpaca is not None or self.t212 is not None
```

**Why:** T212-only armed sessions report as shadow everywhere that checks `is_armed`.

---

### Fix B — `_execute_t212` improvements (`datadesk/live/oms.py:275`, `shadow.py`)

This is a 3-part change. Do them in order.

**Part B1 — Migration guard in `shadow.py`**

Add `order_id TEXT` column to `shadow_signals`, with migration guard for existing DBs.
Also change `record_signal` to return the inserted row ID.

Current `_SCHEMA` in `datadesk/live/shadow.py`:
```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_signals (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    source    TEXT NOT NULL,
    ticker    TEXT NOT NULL,
    side      TEXT NOT NULL,
    weight    REAL NOT NULL,
    ref_price REAL,
    reason    TEXT,
    executed  INTEGER NOT NULL DEFAULT 0
);
"""
```

Change `_connect()` to add the migration after schema creation:
```python
def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(db_path or PLATFORM_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    # Migration: add order_id column if not present (for existing DBs)
    cols = {row[1] for row in con.execute("PRAGMA table_info(shadow_signals)")}
    if "order_id" not in cols:
        con.execute("ALTER TABLE shadow_signals ADD COLUMN order_id TEXT")
        con.commit()
    return con
```

Change `record_signal` to return the inserted rowid:
```python
def record_signal(
    source: str,
    ticker: str,
    side: str,
    weight: float,
    ref_price: float | None = None,
    reason: str = "",
    executed: bool = False,
    db_path: Path | None = None,
) -> int:                                   # <-- return type changed from None to int
    with _connect(db_path) as con:
        cur = con.execute(
            "INSERT INTO shadow_signals (ts, source, ticker, side, weight, ref_price, reason, executed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(UTC).isoformat(),
                source, ticker, side, weight, ref_price, reason,
                1 if executed else 0,
            ),
        )
        return cur.lastrowid                # <-- return rowid
```

Add a new `update_order_id` function at the bottom of `shadow.py`:
```python
def update_order_id(row_id: int, order_id: str | None, db_path: Path | None = None) -> None:
    if order_id is None:
        return
    with _connect(db_path) as con:
        con.execute(
            "UPDATE shadow_signals SET order_id = ? WHERE id = ?",
            (order_id, row_id),
        )
```

**Part B2 — Fix `_execute_t212` in `datadesk/live/oms.py:275`**

> **E3 gate:** Before hardcoding the T212 order ID field name, check what
> `t212.place_market_order()` actually returns. Run it against the demo sandbox and
> inspect the response dict. The field is likely `"id"` or `"orderId"` but is NOT
> confirmed in the codebase. If uncertain, default to `result.get("id") or result.get("orderId")`
> and log what you got.

Current `_execute_t212`:
```python
def _execute_t212(self, ticker: str, side: str, weight_pct: float) -> bool:
    try:
        equity = self.t212.get_equity()
        notional = round(equity * weight_pct, 2)
        if side == "SELL":
            self.t212.close_position(ticker)
        else:
            self.t212.place_market_order(ticker, notional)
        return True
    except Exception as e:
        logger.exception(f"[T212] order failed for {ticker}: {e}")
        return False
```

Change to (return `str | None` — the T212 order ID):
```python
def _execute_t212(self, ticker: str, side: str, weight_pct: float) -> str | None:
    import httpx
    try:
        equity = self.t212.get_equity()
        notional = round(equity * weight_pct, 2)
        if side == "SELL":
            result = self.t212.close_position(ticker)
            order_id = (result or {}).get("id") or (result or {}).get("orderId")
        else:
            result = self.t212.place_market_order(ticker, notional)
            order_id = (result or {}).get("id") or (result or {}).get("orderId")
        logger.info(f"[T212] {side} {ticker} ${notional} order_id={order_id}")
        return str(order_id) if order_id is not None else None
    except (httpx.HTTPStatusError, httpx.RequestError, RuntimeError, KeyError) as e:
        logger.exception(f"[T212] order failed for {ticker}: {e}")
        return None
```

**Part B3 — Wire up shadow UPDATE in `submit_signal` (`datadesk/live/oms.py`)**

In `submit_signal`, the call to `shadow.record_signal()` is around line 181.
Change it to capture the rowid, then after `_execute_t212` update the shadow row:

Find the section that looks like:
```python
shadow.record_signal(
    source=source,
    ticker=ticker,
    side=side,
    weight=weight_pct,
    ref_price=price,
    reason=reason,
    executed=executed,
)

# 4. Broker execution — unified path
order_id = str(uuid.uuid4())[:8]
if executed_alpaca:
    if not self._execute_alpaca(ticker, execution_ticker, side, weight_pct):
        return False
elif executed_t212:
    if not self._execute_t212(ticker, side, weight_pct):
        return False
```

Change to:
```python
shadow_row_id = shadow.record_signal(
    source=source,
    ticker=ticker,
    side=side,
    weight=weight_pct,
    ref_price=price,
    reason=reason,
    executed=executed,
)

# 4. Broker execution — unified path
order_id = str(uuid.uuid4())[:8]   # local book-keeping UUID (not broker ID)
if executed_alpaca:
    if not self._execute_alpaca(ticker, execution_ticker, side, weight_pct):
        return False
elif executed_t212:
    t212_order_id = self._execute_t212(ticker, side, weight_pct)
    if t212_order_id is None:
        return False
    shadow.update_order_id(shadow_row_id, t212_order_id)
```

---

### Fix C — Sharpe reconciliation (`README.md` + `DEVELOPMENT.md`)

**Problem:** README line 92 says `Sharpe 1.96` in the holdout section.
DEVELOPMENT.md §18 says `2.33`. One is stale.

**Action:** Query `platform.db` for the actual Sharpe from the most recent backtest run:
```python
import sqlite3
con = sqlite3.connect("platform.db")
rows = con.execute(
    "SELECT name, metrics FROM backtest_runs ORDER BY id DESC LIMIT 10"
).fetchall()
for name, metrics in rows:
    print(name, metrics)
```

Inspect the output. Find the holdout Sharpe. Update **both** files to the same number.
The metrics JSON column stores the Sharpe under key `"sharpe"`.

If you can't run the DB query, query the `/api/runs` endpoint while the server is running:
`curl http://localhost:8000/api/runs | python -m json.tool`

---

### Fix D — Replace hardcoded `_get_best_run()` (`datadesk/live/monitors/rebalancer.py:74`)

**Current code (entire function):**
```python
def _get_best_run() -> dict | None:
    """
    Hardwired to the top-performing AI_SEMI strategy discovered during the historical sweep.
    Strategy: 6-month momentum, top 2 stocks, no macro trend filter.
    Historical 1-year holdout: 691.7% CAGR, 3.61 Sharpe.
    """
    return {
        "name": "AI_SEMI | mom_only(126,2) trend=N",
        "params": {
            "universe": "AI_SEMI",
            "variant": "mom_only",
            "mom_lookback": 126,
            "mom_top_n": 2,
            "trend_filter": False,
        },
        "metrics": {"sharpe": 3.61}
    }
```

**Replace with:**
```python
def _get_best_run() -> dict:
    """Load the highest-Sharpe 3y-holdout strategy from platform.db."""
    import json
    import sqlite3
    from datadesk.db import PLATFORM_DB

    try:
        con = sqlite3.connect(PLATFORM_DB)
        row = con.execute(
            """
            SELECT name, params, metrics FROM backtest_runs
            WHERE json_extract(metrics, '$.sharpe') IS NOT NULL
              AND json_extract(metrics, '$.max_drawdown') >= -0.30
            ORDER BY json_extract(metrics, '$.sharpe') DESC
            LIMIT 1
            """
        ).fetchone()
        con.close()
    except Exception as e:
        raise RuntimeError(
            f"No sweep runs in platform.db. Run `python main.py backtest` first. ({e})"
        )

    if row is None:
        raise RuntimeError(
            "No sweep runs in platform.db. Run `python main.py backtest` first."
        )

    name, params_raw, metrics_raw = row
    params = json.loads(params_raw) if isinstance(params_raw, str) else (params_raw or {})
    metrics = json.loads(metrics_raw) if isinstance(metrics_raw, str) else (metrics_raw or {})
    return {"name": name, "params": params, "metrics": metrics}
```

Also fix the caller at `rebalancer.py:142` — currently handles `None` silently.
Change:
```python
best = _get_best_run()
if best is None:
    logger.warning("[REBALANCER] no backtest runs found — run the sweep first")
    return {"status": "no_runs"}
```
To:
```python
best = _get_best_run()   # raises RuntimeError if no runs — crash loudly, don't silently no-op
```
(Remove the None check — `_get_best_run` now raises instead of returning None.)

---

### Fix A+D Tests — Add to `tests/test_oms.py`

Add three new tests (the existing 10 tests all pass and should continue to pass):

```python
# Test 1 — T212-only armed session reports is_armed=True
def test_t212_armed_is_armed_true(monkeypatch):
    monkeypatch.setenv("DATADESK_ARM_BROKER", "1")
    monkeypatch.setenv("T212_DEMO_API_KEY", "fake-demo-key")
    monkeypatch.setenv("T212_MODE", "demo")
    # Patch T212Client to avoid real HTTP
    import datadesk.live.oms as oms_mod
    from unittest.mock import MagicMock
    monkeypatch.setattr(
        "datadesk.ingest.t212_client.T212Client",
        lambda: MagicMock(mode="demo"),
    )
    oms = oms_mod.OMSFastPath()
    assert oms.is_armed is True
    assert oms.alpaca is None
    assert oms.t212 is not None


# Test 2 — T212 order ID is persisted to shadow store
def test_t212_order_id_persisted(tmp_path, monkeypatch):
    import datadesk.live.shadow as shadow_mod
    db = tmp_path / "shadow_test.db"
    row_id = shadow_mod.record_signal("test", "AAPL_US_EQ", "BUY", 0.05, db_path=db)
    shadow_mod.update_order_id(row_id, "T212-ORDER-ABC123", db_path=db)
    import sqlite3
    con = sqlite3.connect(db)
    result = con.execute(
        "SELECT order_id FROM shadow_signals WHERE id = ?", (row_id,)
    ).fetchone()
    assert result[0] == "T212-ORDER-ABC123"


# Test 3 — rebalancer queries DB (not hardcoded)
def test_rebalancer_queries_db(tmp_path, monkeypatch):
    import json, sqlite3
    import datadesk.live.monitors.rebalancer as reb_mod
    db = tmp_path / "platform.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE backtest_runs (id INTEGER PRIMARY KEY, name TEXT, params TEXT, metrics TEXT)"
    )
    con.execute(
        "INSERT INTO backtest_runs VALUES (1, 'test_strat', ?, ?)",
        (json.dumps({"universe": "AI_SEMI", "variant": "mom_only"}),
         json.dumps({"sharpe": 2.5, "max_drawdown": -0.12})),
    )
    con.commit()
    monkeypatch.setattr("datadesk.db.PLATFORM_DB", db)
    best = reb_mod._get_best_run()
    assert best["name"] == "test_strat"
    assert best["metrics"]["sharpe"] == 2.5
```

---

## BLOCK 2: DX Tasks (from DX Review)

### DX-T1 — Add `init-db` subcommand to `main.py`

Add a `cmd_init_db()` function that creates schemas in all `.db` files. Schemas use
`CREATE TABLE IF NOT EXISTS`, so they're safe to re-run.

```python
def cmd_init_db() -> None:
    """Create all database schemas. Run once on a fresh clone before backfill."""
    from datadesk.live import shadow
    from datadesk.db import PLATFORM_DB, ALTDATA_DB
    import sqlite3, os

    # Shadow store schema (also handles order_id migration)
    shadow._connect().close()
    print("  shadow store: OK")

    # Platform DB (backtest runs, analyst reports)
    # Schema is created by the first write — just touch it
    con = sqlite3.connect(PLATFORM_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.close()
    print(f"  platform DB ({PLATFORM_DB}): OK")

    # Alt data DB
    con = sqlite3.connect(ALTDATA_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.close()
    print(f"  altdata DB ({ALTDATA_DB}): OK")

    print("\nDone. Next: python main.py backfill --preset ai_semi")
```

Wire it into argparse: add `subparsers.add_parser("init-db", help="Create all database schemas (run once on fresh clone)")`
and route it in the main dispatcher.

---

### DX-T2 — Add `--preset` flag to `backfill` subcommand

The `backfill` subcommand currently requires explicit tickers. Add `--preset ai_semi`
that fetches the 25-ticker AI/Semi universe.

Define the preset at the top of `main.py` (or import from `sweep.py`):
```python
_BACKFILL_PRESETS = {
    "ai_semi": [
        "NVDA", "AMD", "TSM", "AVGO", "QCOM", "INTC", "MU", "AMAT", "LRCX", "KLAC",
        "ASML", "MRVL", "ON", "WOLF", "TER", "ONTO", "MKSI", "ACLS", "COHU", "AMBA",
        "SMTC", "POWI", "DIOD", "MPWR", "SITM",
    ],
}
```

Add batching with rate-limit delay in `cmd_backfill`:
```python
def cmd_backfill(tickers: list[str], source: str, skip_fundamentals: bool = False,
                 preset: str | None = None) -> None:
    if preset:
        tickers = _BACKFILL_PRESETS.get(preset)
        if not tickers:
            print(f"Unknown preset '{preset}'. Available: {list(_BACKFILL_PRESETS)}")
            return
        print(f"Preset '{preset}': {len(tickers)} tickers")

    # Batch in groups of 5 with 2s delay to avoid yfinance rate limits
    import time
    from datadesk.ingest.backfill import backfill_history
    batch_size = 5
    all_written = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        written = backfill_history(batch)
        all_written.update(written)
        if i + batch_size < len(tickers):
            time.sleep(2)

    for t, n in all_written.items():
        print(f"  {t:>10}  {n} bars")
    ...  # rest of function unchanged (fundamentals fetch)
```

Update argparse for `backfill` to add `--preset` arg:
```
parser_backfill.add_argument("--preset", choices=list(_BACKFILL_PRESETS), help="Seed a named universe")
```

---

### DX-T3 — Fix `python main.py` default behavior

Currently running `python main.py` with no args launches the backtest (5+ min). Change it
to show first-time instructions.

In the main dispatcher (the `if __name__ == "__main__"` block or equivalent), change the
default (no-subcommand) branch to:

```python
if args.command is None:
    print("""DataDesk — market data platform (paper only)

First time? Run:
  python main.py init-db
  python main.py backfill --preset ai_semi   # seeds 25-ticker AI/Semi universe (~5 min)
  python main.py backtest                    # runs momentum strategy (~3 min)
  python main.py serve                       # ops console on http://localhost:8000

All subcommands: python main.py --help
""")
    sys.exit(0)
```

---

### DX-T4 — Fix quickstart in `README.md`

Replace the current Quickstart section:

**Current:**
```bash
python -m venv .venv && .venv/Scripts/activate  # Windows
pip install -e ".[dev]"
cp .env.example .env  # add ALPACA_API_KEY, ALPACA_SECRET_KEY, FRED_API_KEY

python main.py          # backtest + holdout report, then serve on :8000
python main.py serve    # ops console only (skip backtest)
python main.py holdout  # holdout report only
```

**Replace with:**
```bash
## Quickstart

Requires Python 3.11+.

git clone https://github.com/KeepingJones/datadesk.git
cd datadesk

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate      # Mac/Linux
# .venv\Scripts\activate       # Windows

pip install -e ".[dev]"
cp .env.example .env            # fill in FRED_API_KEY (free at fred.stlouisfed.org)

# First-time setup: seed the 25-ticker AI/Semi universe (~8-12 min total)
python main.py init-db
python main.py backfill --preset ai_semi     # fetches via yfinance, no API key needed
python main.py backtest                      # runs momentum strategy, saves results
python main.py serve                         # ops console on http://localhost:8000

# Run tests
pytest

# Windows convenience scripts (optional)
# launch.bat   — start all monitoring daemons
# run.ps1      — dev mode (restarts on code change)
```

Also fix the error message in `cmd_backtest()` in `main.py` (line ~46):
```python
# Current:
print(
    "History store is empty — run: python -m datadesk.history.migrate "
    "or python main.py backfill <tickers>"
)

# Change to:
print(
    "History store is empty. Seed it first:\n"
    "  python main.py init-db\n"
    "  python main.py backfill --preset ai_semi   # 25-ticker AI/Semi universe (~5 min)\n\n"
    "Then: python main.py backtest && python main.py serve"
)
```

---

### DX-T5 — Add help strings to all argparse subcommands in `main.py`

Find every `subparsers.add_parser(...)` call and add a `help=` argument. Examples:

```python
subparsers.add_parser("serve",         help="Start the ops console on http://localhost:8000")
subparsers.add_parser("backtest",      help="Run momentum+trend backtest, save results to platform.db")
subparsers.add_parser("backfill",      help="Download daily price history for tickers via yfinance")
subparsers.add_parser("init-db",       help="Create all database schemas (run once on fresh clone)")
subparsers.add_parser("weekly-update", help="Gap-fill all price history + refresh stale fundamentals")
subparsers.add_parser("coverage",      help="Print history-store bar coverage per ticker")
subparsers.add_parser("holdout",       help="Print holdout backtest report")
subparsers.add_parser("universe",      help="Print platform availability breakdown per ticker")
subparsers.add_parser("enrich",        help="Fetch/refresh fundamentals for tickers")
subparsers.add_parser("collect-trump", help="Refresh Trump communications corpus from CNN archive")
subparsers.add_parser("tax-compare",   help="Side-by-side after-tax comparison: ISA vs Alpaca")
```

---

### DX-T6 — Add `REQUIRED` / `OPTIONAL` labels to `.env.example`

Open `.env.example`. Add `# REQUIRED` and `# OPTIONAL` comments to each section.
The file is already well-commented — just add section markers:

```bash
# ─── REQUIRED ────────────────────────────────────────────────────────────────
FRED_API_KEY=your_fred_key_here           # Free at fred.stlouisfed.org

# ─── OPTIONAL: T212 (UK/EU equities via ISA) ──────────────────────────────
T212_MODE=demo
T212_DEMO_API_KEY=your_t212_demo_key_here
# WARNING: Do NOT set T212_LIVE_API_KEY alongside T212_DEMO_API_KEY
# T212_LIVE_API_KEY=your_t212_live_key_here

# ─── OPTIONAL: Alpaca (US equities, paper trading) ────────────────────────
ALPACA_API_KEY=your_alpaca_api_key_here
ALPACA_SECRET_KEY=your_alpaca_secret_key_here

# ─── OPTIONAL: DataDesk control flags ────────────────────────────────────
# Default "0" = shadow mode. Set to "1" only with valid broker keys.
DATADESK_ARM_BROKER=0

# ─── OPTIONAL: Database paths (defaults work out of the box) ─────────────
DATADESK_DB_PATH=datadesk/data/datadesk.db
ALTDATA_DB_PATH=datadesk/data/altdata.db
DATA_DIR=datadesk/data

# ─── OPTIONAL: Data providers ─────────────────────────────────────────────
TIINGO_API_KEY=your_tiingo_key_here
FUND_BASE_CURRENCY=GBP

# ─── OPTIONAL: AI / LLM (powers thesis generator and news analysis) ───────
ANALYST_MODEL=gemini-2.5-pro
OLLAMA_URL=http://localhost:11434
```

---

### DX-T7 — Add dashboard screenshot to `README.md`

1. Start the server: `python main.py serve`
2. Open `http://localhost:8000` in a browser
3. Take a screenshot showing the Command & Control panel + Strategy P&L table populated
   with real data (run `python main.py backtest` first to populate the leaderboard)
4. Save to `docs/assets/dashboard.png`
5. In README.md, add after the architecture tree:

```markdown
### Live ops console

![DataDesk ops console](docs/assets/dashboard.png)
```

---

### DX-T8 — Create `CHANGELOG.md`

Create a new file `CHANGELOG.md` at project root:

```markdown
# Changelog

All notable changes to DataDesk are documented here.

## [Unreleased]

### Added
- `python main.py init-db` — schema creation command for fresh clones
- `python main.py backfill --preset ai_semi` — seeds 25-ticker AI/Semi universe via yfinance
- OPERATIONS.md — pre-flight, signal-fire, and override checklists
- `.env.example` — credential template with REQUIRED/OPTIONAL labels

### Fixed
- `OMSFastPath.is_armed` — now correctly returns True for T212-only armed sessions
- `_execute_t212` — specific exception handling; T212 order ID persisted to shadow store
- Shadow store migration — `order_id TEXT` column added via PRAGMA table_info guard
- `_get_best_run()` — replaced hardcoded params with live platform.db query
- Rebalancer cold-start — raises RuntimeError instead of silently returning no-op
- README Sharpe discrepancy — reconciled to single authoritative value from platform.db
- Dashboard button sizing, danger styling, heading hierarchy, P&L formatting (7 fixes)

### Architecture
- Shadow-first OMS: every signal recorded before any broker call
- DATADESK_ARM_BROKER=0 default — broker execution never happens without explicit opt-in
```

---

### DX-T9 — Add `LICENSE` file

Create `LICENSE` at project root with standard MIT license text:

```
MIT License

Copyright (c) 2026 Ewan Jones

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## BLOCK 3: README + OPERATIONS.md Rewrite (from CEO Plan Track B)

### README rewrite

The current README leads with a component table. It needs to lead with architecture, then
Methodology & Honest Results (with both Sharpe pass AND MaxDD fail), then Live Execution.

**Exact section order required:**

1. **Architecture paragraph** (one paragraph, before any table):

```markdown
DataDesk is a systematic equity trading platform covering 249 tickers. It ingests
price, fundamental, and alt-data signals daily via a multi-source ingestion layer;
applies a momentum-core strategy with a 3-state macro regime overlay (EXPANSION /
CAUTION / STRESS using yield curve + VIX + SPY vs 200dMA); executes through a
shadow-first OMS with Alpaca (US equities, paper) and T212 (UK/EU equities, ISA).
Every signal is recorded to a shadow store before any broker call is attempted.
```

2. **`## Methodology & Honest Results`** section — MUST include ALL of:
   - Gross backtested CAGR: ~38% (2016–2026, walk-forward OOS)
   - Estimated CAGR after survivorship bias correction: ~13–15%
   - Gate 1 Sharpe: **PASSES** (2.33 vs SPY 1.72) — but also state MaxDD **DOES NOT PASS**
     (-14% vs SPY -9%)
   - The strategy is NOT Gate 1 cleared
   - Bias sources: ~25% of gross CAGR from survivorship bias + NVDA concentration
   - Link to `DEVELOPMENT.md#backtest-realism--bias` for full audit

   > Note on the Sharpe number: README currently says 1.96, DEVELOPMENT.md says 2.33.
   > Use the number from Fix C (query platform.db). Update both files to match.

3. **`## Live Execution (Demo)`** section:

```markdown
## Live Execution (Demo)

T212 demo account configured via shadow-first OMS. Signals routing to
demo.trading212.com (paper money) under validation.

- Every signal is recorded to `platform.db/shadow_signals` before any broker call
- `DATADESK_ARM_BROKER=0` (default): shadow mode — no execution
- `DATADESK_ARM_BROKER=1` + T212 demo key: armed — signals route to T212 demo ISA
- Demo → live transition requires 8+ weeks paper validation (see OPERATIONS.md)
```

4. The existing **component table** (`## What it does`) — keep as-is, promote it below the above.

5. Existing **Quickstart** (updated in DX-T4).

---

### Create `OPERATIONS.md`

Create `OPERATIONS.md` at project root. The E1 amendment from Engineering Review corrects
the "No restart required" mistake — the override procedure MUST say "Restart the OMS process".

```markdown
# DataDesk Operations

All trading is paper-only. `PAPER_TRADE_MODE = True` is hardcoded and never changes.

---

## Pre-flight checklist (run before market open)

- [ ] Is .env loaded? Run: `python -c "import os; print(os.getenv('DATADESK_ARM_BROKER'))"`
      Expected: `"1"` (armed) or `"0"` (shadow)
- [ ] Is T212_MODE correct? Run: `python -c "import os; print(os.getenv('T212_MODE'))"`
      Expected: `"demo"` during test phase — never `"live"` unless explicitly promoted
- [ ] Run from project root: `python main.py serve` or `python -m datadesk.live.main`
      (Note: `from sweep import UNIVERSES` in rebalancer.py requires project root on path)
- [ ] Are risk limits in place? Check `max_position_pct = 0.10` and
      `max_daily_loss_pct = 0.05` in `datadesk/live/oms.py:__init__`
- [ ] Is the shadow store writable? Check `platform.db` exists and is not locked

---

## Signal-fire checklist (when rebalancer fires)

- [ ] Did OMS log `OMS ARMED` or `OMS SHADOW`? (`SHADOW` = no broker call made)
- [ ] Did T212 return an order ID? (`None` = API failure, not a fill)
      Check: `SELECT order_id FROM shadow_signals ORDER BY id DESC LIMIT 5`
      on `platform.db`
- [ ] Does the shadow store record match the broker call?
      Check `datadesk/data/shadow_store.db` or `platform.db`
- [ ] Are fill prices within expected slippage? (>1% slippage = investigate)

---

## Override procedure (something looks wrong)

1. Set `DATADESK_ARM_BROKER=0` in `.env` — immediately targets shadow mode on next restart
2. **Restart the OMS process** — the `ARM_BROKER` flag is read at startup (`oms.py:__init__`),
   not per-signal. Kill and restart the datadesk process to apply the change.
   The running process does not pick up `.env` changes dynamically.
3. Verify shadow mode: OMS log should show `OMS SHADOW: ...` on next signal
4. Log the incident: what signal fired, what went wrong, what you did

---

## Deployment gate (paper → live ISA capital)

Do NOT move to live capital until all three gates pass:

1. **Gate 1 (backtesting):** Holdout Sharpe ≥ 1.0 AND MaxDD ≤ −20% AND beats SPY on both
   — *currently: Sharpe PASSES, MaxDD DOES NOT PASS*
2. **Gate 2 (paper validation):** 8+ weeks T212 demo — live results within 1 sigma of
   backtest expectation
3. **Gate 3 (live with cap):** Alpaca live US with capped capital; T212 ISA with
   manual-confirm before any automation
4. **Always-on:** Per-position size limit 10% max, portfolio kill switch at −10% from peak

---

## Key file locations

| File | Purpose |
|------|---------|
| `platform.db` | Backtest runs, analyst reports, shadow signal audit trail |
| `datadesk/data/altdata.db` | Congress/insider trades, FRED macro, news, fundamentals |
| `datadesk/data/history.db` | Daily OHLCV bars (221k+) |
| `error_log.txt` | All OMS and daemon logs |
| `.env` | API keys — never commit, never push |
```

---

## BLOCK 4: T212 Demo Arm (from CEO Plan Track A)

This requires human action first — Gemini cannot do the key rotation.

**Human steps (Ewan must do these):**
1. Log into trading212.com → Settings → API → generate a **demo** API key
2. Open `.env` and set:
   ```
   T212_MODE=demo
   T212_DEMO_API_KEY=<your_demo_key>
   DATADESK_ARM_BROKER=1
   ```
   Do NOT set `T212_LIVE_API_KEY` alongside `T212_DEMO_API_KEY` (footgun risk)
3. Restart the OMS: `python main.py serve`
4. Verify logs show: `OMS ARMED: T212 demo client initialized.` (not `OMS SHADOW`)

**After human arms it — implement the E3 verification:**

Before finalising Fix B's order ID field name, run a test order in the demo sandbox:
```python
# Run in a Python REPL with .env loaded
from dotenv import load_dotenv; load_dotenv()
from datadesk.ingest.t212_client import T212Client
t = T212Client()
result = t.place_market_order("AAPL_US_EQ", 10.0)
print(result)   # inspect field names — look for 'id' or 'orderId'
```

Use the actual field name from this output in Fix B2. If the field is absent or the call
fails, log the raw response and use `result.get("id") or result.get("orderId")` as the
safe fallback.

---

## Commit sequence (once all blocks are done)

```bash
# Block 1: Code fixes
git add datadesk/live/oms.py datadesk/live/shadow.py datadesk/live/monitors/rebalancer.py tests/test_oms.py
git commit -m "fix: is_armed includes T212, _execute_t212 captures order ID, _get_best_run queries DB"

# Block 2: DX tasks
git add main.py .env.example README.md docs/assets/dashboard.png CHANGELOG.md LICENSE
git commit -m "dx: init-db command, backfill preset, multi-platform quickstart, REQUIRED/OPTIONAL .env labels"

# Block 3: Docs
git add README.md OPERATIONS.md DEVELOPMENT.md
git commit -m "docs: methodology-first README, OPERATIONS.md, Sharpe reconciliation"

# Pre-push checklist (run before git push):
# git log -p | grep -iE "key|secret|token"   → must return nothing
# git ls-files .env                            → must return nothing
# README must contain ## Methodology & Honest Results with BOTH Sharpe pass AND MaxDD fail
# OPERATIONS.md must exist
# .env.example must be committed
```

---

## What NOT to change

- `PAPER_TRADE_MODE = True` in `datadesk/config.py`
- `DATADESK_ARM_BROKER` default `"0"` in `datadesk/live/oms.py`
- Any live trading path (there should be none — this is paper-only)
- `.env` — keys live here, never tracked by git
- The shadow-first ordering in `submit_signal`: `shadow.record_signal()` is called BEFORE
  any broker execution. This is by design (DESIGN §6.2). Do not reorder.
