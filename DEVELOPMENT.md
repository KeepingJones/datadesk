# DataDesk — Developer Reference

Complete reference for contributing to or resuming development on DataDesk.
Read this alongside `README.md` (hiring-manager overview) and `docs/backtesting.md` (backtest protocol).

---

## Table of Contents

1. [Repository layout](#1-repository-layout)
2. [Databases](#2-databases)
3. [Commands (main.py)](#3-commands)
4. [Strategy layer](#4-strategy-layer)
5. [Backtest engine](#5-backtest-engine)
6. [After-tax simulation](#6-after-tax-simulation)
7. [Fundamental data](#7-fundamental-data)
8. [Universe & platform routing](#8-universe--platform-routing)
9. [OMS / live layer](#9-oms--live-layer)
10. [API endpoints](#10-api-endpoints)
11. [Dashboard](#11-dashboard)
12. [Testing](#12-testing)
13. [Adding tickers](#13-adding-tickers)
14. [Environment variables](#14-environment-variables)
15. [Hard rules](#15-hard-rules)
16. [Current status & gate](#16-current-status--gate)

---

## 1. Repository layout

```
datadesk/
├── main.py                   entry point — all CLI commands
├── datadesk/
│   ├── config.py             DB paths, environment flags, PAPER_TRADE_MODE
│   ├── db.py                 platform.db helpers (backtest runs, universe)
│   ├── logging_config.py
│   │
│   ├── quality/              price reconciliation
│   │   ├── recon.py          multi-source cross-validation
│   │   ├── classifier.py     7-cause break classifier
│   │   └── tiers.py          L1/L2/L3 liquidity tiers
│   │
│   ├── history/              canonical daily-bar store
│   │   ├── store.py          save_bars(), load_closes(), coverage()
│   │   └── migrate.py        initial migration from legacy alt_data.db
│   │
│   ├── ingest/               data ingestion
│   │   ├── backfill.py       yfinance backfill (DEFAULT_START = 2012-01-01)
│   │   ├── massive.py        Massive free-tier (last 2y US bars)
│   │   ├── yahoo.py          daily quote fetcher
│   │   ├── fred.py           FRED macro series
│   │   ├── ecb.py            ECB EUR reference rates
│   │   ├── trump.py          CNN Truth Social archive collector
│   │   ├── t212_client.py    read-only T212 REST client (60s cache)
│   │   └── fundamentals.py   yfinance fundamental fetcher → altdata.db
│   │
│   ├── strategies/           signal generation
│   │   ├── momentum.py       cross-sectional 12-1 momentum (formation, skip, top_n)
│   │   ├── trend.py          200d trend filter with hysteresis band
│   │   ├── regime.py         VIX + bear_only_scale overlay
│   │   ├── meanrev.py        mean reversion (parked, not in live blend)
│   │   └── insider.py        congress/Form4 follow strategy (strategy 4)
│   │
│   ├── backtest/             backtesting
│   │   ├── engine.py         run_backtest() — vectorised, no-lookahead
│   │   ├── costs.py          CostModel, ALPACA_COSTS, T212_ISA_COSTS, ZERO_COSTS
│   │   ├── metrics.py        cagr(), sharpe(), max_drawdown(), summarize()
│   │   ├── walkforward.py    walk-forward harness with param-stability flag
│   │   └── tax.py            UK CGT simulation — compare_tax_wrappers()
│   │
│   ├── universe/             platform classification
│   │   └── platform.py       classify(), split_by_platform(), available_on_*()
│   │
│   ├── live/                 OMS and monitors (ALL shadow mode by default)
│   │   ├── oms.py            shadow-first OMS — gated on DATADESK_ARM_BROKER=1
│   │   ├── shadow.py         signal audit store (every fast-path signal recorded)
│   │   ├── universe.py       active universe management
│   │   ├── monitors/         event monitors (none auto-start)
│   │   │   ├── trump.py      TrumpMonitor — CNN polling + v3 taxonomy classifier
│   │   │   ├── supply_chain.py  SupplyChainMonitor — real v3 matrix + 1m yfinance
│   │   │   ├── news.py       NewsMonitor
│   │   │   └── agent.py      AgentWorker (requires Ollama)
│   │   └── jensen.py         JensenMonitor (parked — no data source)
│   │
│   ├── monte_carlo/          simulation
│   │   └── simulation.py     bootstrap actual strategy returns → P5–P95 fan bands
│   │
│   ├── ai/                   classification
│   │   └── post_classifier.py  deterministic v3 taxonomy + Ollama fallback
│   │
│   ├── api/
│   │   └── app.py            FastAPI — all endpoints + weekly scheduler
│   └── dashboard/
│       └── index.html        single-page ops console (Jinja2 + Chart.js)
│
├── tests/                    119 tests (pytest)
├── scripts/
│   └── migrate_from_trading_bot.py  one-shot legacy migration (already run)
├── docs/
│   └── backtesting.md        backtest protocol + current results
│
├── altdata.db                alt-data (congress, insiders, news, macro, fundamentals)
├── history.db                daily OHLCV bars
├── platform.db               backtest runs, universe, shadow signals
└── .env                      secrets — never committed
```

---

## 2. Databases

### history.db
Canonical daily-bar store. Schema: `bars(ticker, date, open, high, low, close, volume, source)`.
- **Always append-only** via `save_bars()` with `INSERT OR IGNORE` — safe to re-run any ingest.
- `load_closes(tickers, start, end)` → wide DataFrame (date × ticker), forward-filled.
- `coverage()` → DataFrame of (ticker, rows, first, last).

### altdata.db
All alternative and fundamental data.

| Table | Rows | Description |
|---|---|---|
| `congress_trading` | 16,146 | STOCK Act disclosures — use `disclosure_date` not `transaction_date` |
| `insiders` | 267 | Form 4 transactions |
| `legislator_profiles` | 536 | Congress member metadata |
| `news_articles` | 5,520 | Ticker-tagged headlines |
| `wallstreetbets` | 74,007 | WSB sentiment archive |
| `equity_reference` | 203 | Reference data |
| `wikipedia` | 13,749 | Entity summaries |
| `price_ticks` | 167,611 | Legacy tick data |
| `macro_indicators` | 23,948 | Economic indicators |
| `macro_history` | 724,155 | FRED macro series |
| `filings` | 37,883 | SEC filing index |
| `news_extended` | 49,424 | Extended news corpus |
| `ticker_metadata` | 181 | Ticker → company mappings |
| `t212_collections` | 74 | T212 collection definitions |
| `equity_info` | 32 | Static company data (name, sector, country, description) |
| `equity_ratios` | 32 | Snapshot ratios (PE, PB, dividend yield, beta, margins) |
| `equity_financials` | 155 | Annual income statements (revenue, gross profit, EBIT, net income) |
| `equity_balance` | ~100 | Annual balance sheets (assets, liabilities, cash, debt) |

### platform.db
- `backtest_runs` — saved results from every `run_backtest()` call
- Monitored universe
- Shadow signal audit trail (every OMS fast-path signal)

---

## 3. Commands

```bash
python main.py backtest          # momentum+trend backtest, save to platform.db
python main.py holdout           # strategy v2 (momentum-core + bear overlay) vs SPY
python main.py tax-compare       # 3-column after-tax comparison: Alpaca pre, Alpaca post, ISA
python main.py serve [--port N]  # ops console on http://localhost:8000
python main.py coverage          # print history store coverage table
python main.py universe          # print platform availability per ticker (T212 ISA / Alpaca)
python main.py backfill [--source yahoo|massive] [--no-fundamentals] T1 T2 ...
                                 # backfill price history + fundamentals for tickers
python main.py enrich [T1 T2 ...]  # refresh fundamentals only (all or subset)
python main.py weekly-update     # gap-fill prices + refresh stale fundamentals (run Saturdays)
python main.py collect-trump     # refresh Trump CNN corpus
```

### `holdout` output
Prints two tables — FULL PERIOD and HOLDOUT (last 252d) — each with three rows:
1. Strategy (ALPACA costs)
2. Strategy (T212 ISA 15bps FX costs)
3. SPY benchmark (zero cost, no tax)

Gate 1 = beat SPY on **both** Sharpe and MaxDD in the holdout section.

### `tax-compare` output
Three columns per metric:
- `Alpaca pre-tax` — net of transaction costs, zero CGT
- `Alpaca post-tax` — UK CGT 24% applied annually above £3k exempt
- `T212 ISA` — 15bps FX fee per trade, zero CGT

---

## 4. Strategy layer

### Active blend (strategy v2)
`momentum-core + bear_only_scale`

```python
# weights set at close of day t, earn day t+1's return
w_mom = momentum(lookback=126, top_n=10, skip=21)(prices)
scale = bear_only_scale(prices["SPY"], prices["^VIX"])
# scale = 0.4 when SPY < 200dMA AND VIX > 30, else 1.0
w_final = w_mom.mul(scale, axis=0)
```

**Key parameters:**
- `lookback=126` (6 months formation window)
- `skip=21` (skip most recent month — avoids short-term reversal)
- `top_n=10` (equal-weight top 10 by 6-1 return)
- Bear overlay fires only when BOTH SPY below 200dMA AND VIX > 30

### Parked strategies
- `meanrev.py` — negative attribution in blended backtest; excluded from live blend
- `insider.py` / `congress` (strategy 4) — wired but awaiting larger honest universe

### Adding a new strategy
1. Create `datadesk/strategies/my_strategy.py` returning a `pd.DataFrame` of target weights (date × ticker)
2. Import and compose in `cmd_holdout()` or `cmd_backtest()` in `main.py`
3. Pass to `run_backtest(weights, prices, cost_model)` — engine handles everything else
4. Never modify `engine.py` to accommodate a strategy — all lookahead prevention is structural

---

## 5. Backtest engine

`datadesk/backtest/engine.py` — `run_backtest(target_weights, prices, cost_model, start, end) → BacktestResult`

**No-lookahead guarantee (structural):**
- Weights at close of day `t` → `held = w.shift(1)` → earns `prices.pct_change()` of day `t+1`
- The shift is unconditional — no signal can see the return it earns

**Cost model (`costs.py`):**
```python
ALPACA_COSTS   = CostModel(default_tier="L1", commission_bps=0.0, fx_fee_bps=0.0)
T212_ISA_COSTS = CostModel(default_tier="L1", commission_bps=0.0, fx_fee_bps=15.0)
ZERO_COSTS     = CostModel(flat_bps=0.0)
```
`fx_fee_bps=15.0` = 0.15% per trade. Round-trip (buy + sell) = 0.30%, charged on `|Δweight|`.

**`BacktestResult` fields:**
- `.returns` — net daily returns (Series)
- `.gross_returns` — before costs
- `.equity` — `(1 + returns).cumprod()`
- `.weights` — effective (forward-filled) daily weights
- `.turnover` — `|Δweight|.sum(axis=1)` per day
- `.metrics` — `dict` from `summarize()`

---

## 6. After-tax simulation

`datadesk/backtest/tax.py`

### `apply_uk_cgt(returns, tax_params, portfolio_start)`
Post-processes a daily returns series with UK CGT:
- Groups by UK tax year end (`YE-APR` = April 30, close enough to April 5)
- Applies `tax.annual_exempt` (£3,000) and `tax.cgt_rate` (24% higher-rate)
- Losses carry forward to offset future gains
- CGT deducted from equity at year-end

### `compare_tax_wrappers(target_weights, prices, tax_params, alpaca_cost, isa_cost)`
Runs the same strategy twice and returns `TaxComparisonResult` with three sets of metrics:
- `alpaca_pretax` — from `run_backtest()` with `ALPACA_COSTS`
- `alpaca_aftertax` — above with `apply_uk_cgt()` applied
- `isa` — from `run_backtest()` with `T212_ISA_COSTS` (no CGT)

### Tax parameters
```python
UK_HIGHER_RATE = TaxParams(cgt_rate=0.24, annual_exempt=3_000)  # Ewan's rate
UK_BASIC_RATE  = TaxParams(cgt_rate=0.18, annual_exempt=3_000)
```

### Break-even
At gains > 1.25% (higher-rate), T212 ISA wins over Alpaca taxable for US stocks.
Break-even formula: `0.30% FX round-trip / 24% CGT = 1.25%`.

---

## 7. Fundamental data

`datadesk/ingest/fundamentals.py` — `fetch_fundamentals(tickers, db_path)`

Stores into `altdata.db`:

| Table | Key | Contents |
|---|---|---|
| `equity_info` | `ticker` (PK) | name, sector, industry, country, exchange, description |
| `equity_ratios` | auto-id, `ticker`, `fetched_at` | PE, PB, PS, EV/EBITDA, dividend yield, beta, margins, ROE, D/E |
| `equity_financials` | `(ticker, fiscal_year)` | revenue, gross profit, EBIT, net income, EPS |
| `equity_balance` | `(ticker, fiscal_year)` | total assets, liabilities, cash, total debt, book value |

**equity_ratios is append-only** (timestamped snapshots). Query the latest with:
```sql
SELECT * FROM equity_ratios WHERE id IN (SELECT MAX(id) FROM equity_ratios GROUP BY ticker)
```

**Weekly refresh** runs automatically (Sunday 07:00 UTC) via the FastAPI startup scheduler.
Manual: `python main.py enrich` or `POST /api/trigger/enrich`.

---

## 8. Universe & platform routing

`datadesk/universe/platform.py`

### Classification
```python
classify("NVDA")     # → {alpaca: True, t212_isa: True, is_us_stock: True}
classify("LGEN.L")   # → {alpaca: False, t212_isa: True, is_uk: True}
classify("SPY")      # → {alpaca: True, t212_isa: False, is_us_etf: True, ucits_equivalent: "CSPX.L"}
classify("^VIX")     # → {alpaca: False, t212_isa: False}  — data only
```

### Routing decision table

| Signal type | Expected hold | Preferred broker | Reason |
|---|---|---|---|
| Long momentum, US stock | 1–6 months | **T212 ISA** (if allowance left) | CGT saving >> 0.30% FX cost |
| Long UK stock | Any | **T212 ISA** | 0% on UK dividends vs 33.75% |
| Short-term event (PEAD, congress) | 1–8 weeks | **Alpaca** if ISA exhausted | Low gain, save ISA for longer holds |
| Short positions | Any | **Alpaca** | ISA cannot short |
| Derivatives / options | Any | **Alpaca** | ISA cannot hold derivatives |
| US ETF exposure | Any | T212 ISA via **CSPX.L/EQQQ** | SPY/QQQ banned in ISA (PRIIPs) |

### ISA priority order (£20k/year allowance)
1. UK income stocks (highest dividend tax saving — 33.75% vs 0%)
2. Long-term US momentum positions (CGT saving compounds over years)
3. UCITS ETFs for passive exposure
4. Short-term US event trades last

---

## 9. OMS / live layer

**Default state: shadow mode forever.**
No broker orders are placed unless `DATADESK_ARM_BROKER=1` is set AND `PAPER_TRADE_MODE` is False.
Both conditions require explicit manual action. Never auto-set either.

### Signal flow
```
Strategy signal → OMS.fast_path() → shadow.record_signal()
                                   ↓ (only if DATADESK_ARM_BROKER=1)
                              broker.place_order()
```

### Monitors (none auto-start)
Started via dashboard buttons or `POST /api/daemons/{name}/start`.
- `trump_monitor` — polls CNN archive, classifies new posts via v3 taxonomy
- `supply_chain` — real supply-chain matrix + live 1m yfinance moves
- `news_monitor` — headline ingestion
- `agent_worker` — Ollama-backed inference (requires local model)
- `jensen_monitor` — parked (no data source)

---

## 10. API endpoints

All served by FastAPI on the port specified at launch (default 8000).

### Data
| Endpoint | Method | Description |
|---|---|---|
| `/api/universe/list` | GET | Active monitored universe |
| `/api/universe/add` | POST | Add ticker to universe |
| `/api/fundamentals` | GET | All fundamentals (or `?ticker=X` for one) |
| `/api/pnl_summary` | GET | Daily/weekly/monthly P&L from equity curve |
| `/api/daily_pnl` | GET | Daily P&L series |
| `/api/historic_trades` | GET | Closed positions |
| `/api/live_trades` | GET | Shadow signals (executed=True) |
| `/api/alpaca/account` | GET | Alpaca paper account summary |
| `/api/alpaca/positions` | GET | Alpaca open positions |
| `/api/t212/account` | GET | T212 live account cash summary |
| `/api/t212/positions` | GET | T212 open positions |
| `/api/daemons/status` | GET | All monitor daemon statuses |
| `/api/monte_carlo/status` | GET | MC simulation status + result when done |

### Triggers
| Endpoint | Method | Description |
|---|---|---|
| `/api/trigger/weekly-update` | POST | Gap-fill prices + refresh stale fundamentals |
| `/api/trigger/enrich` | POST | Refresh fundamentals for all (or body: `["T1","T2"]`) |
| `/api/trigger/backfill` | POST | Backfill price + fundamentals for body tickers |
| `/api/jobs/status` | GET | Background job tracker |
| `/api/monte_carlo/run` | POST | Start MC bootstrap (`?runs=N&model=bootstrap\|gbm`) |
| `/api/daemons/{name}/start` | POST | Start a monitor daemon |
| `/api/daemons/{name}/stop` | POST | Stop a monitor daemon |
| `/api/sweep/run` | POST | Parameter grid sweep |
| `/api/validation` | GET | Walk-forward validation results |

---

## 11. Dashboard

Single-page ops console at `http://localhost:8000`. Three tabs:

### Command & Control
- Daemon manager (start/stop each monitor)
- **Universe Maintenance** — Weekly Update + Refresh Fundamentals buttons with real-time job status
- Alpaca paper account (equity, daily P&L, buying power, positions)
- T212 live ISA account (total, invested, unrealised P&L, free cash, positions)
- Strategy P&L panel (daily/weekly/monthly from backtest equity curve)

### Universe & Discovery
- Active universe table
- Add ticker form

### Analytics & Validation
- Backtest leaderboard (top 5 by CAGR)
- Equity curve chart
- **Monte Carlo fan chart** — P5/P25/P50/P75/P95 percentile bands bootstrapped from real returns
- CAGR/Sharpe/MaxDD distribution ranges
- Walk-forward validation results
- Parameter sweep grid

### Tech stack
- Jinja2 templating (served directly by FastAPI — no build step)
- Chart.js (CDN) for equity curves and MC fan chart
- Vanilla JS — `fetch()` polling every 2s (fast data) and 5min (account data)
- **VS Code shows false-positive linter errors on `{{ }}` Jinja2 syntax** — not real errors

---

## 12. Testing

```bash
.venv/Scripts/python -m pytest              # all 119 tests
.venv/Scripts/python -m pytest tests/backtest/  # backtest + tax tests
.venv/Scripts/python -m pytest -k "tax"     # just CGT tests
```

Test coverage by module:
- `tests/backtest/` — engine, costs, metrics, walk-forward, **tax** (CGT, platform classification)
- `tests/history/` — bar store
- `tests/quality/` — recon engine, classifier
- `tests/strategies/` — momentum, trend, regime
- `tests/api/` — FastAPI endpoint smoke tests
- `tests/ingest/` — backfill, validation

CI runs on every push. Ruff linting enforced.

---

## 13. Adding tickers

### Price history only
```bash
python main.py backfill --source yahoo NVDA AAPL MSFT
# Automatically also fetches fundamentals unless --no-fundamentals
```

### Via dashboard
Universe & Discovery tab → Add Ticker form (calls `/api/universe/add`).

### What backfill does
1. `backfill_history(tickers)` — downloads from `DEFAULT_START = "2012-01-01"` via yfinance
2. Upserts into `history.db` via `save_bars()` with `INSERT OR IGNORE`
3. `fetch_fundamentals(tickers)` — stores into `altdata.db` equity_* tables

### Platform classification
The system automatically knows which broker can trade which ticker:
- `.L` suffix → T212 ISA only (UK listed)
- Known US ETF (SPY, QQQ etc.) → Alpaca only (PRIIPs ban in ISA)
- Everything else → both platforms (route by tax optimisation)

### Tickers currently in universe (249 total)
Key AI-focused tickers by region:

**US chips:** NVDA, AMD, AVGO, ARM, MRVL, KLAC, AMAT, LRCX, QCOM, MU, INTC, SMCI, COHU, FORM, ONTO, ON, NXPI
**US networking/fiber:** ANET, CSCO, COHR, LITE, CIEN, VIAV
**US data centers:** EQIX, DLR, VRT
**US AI energy:** NEE, VST
**US AI software:** MSFT, GOOGL, META, AMZN, PLTR, NOW, CRM, ORCL, SNOW, DDOG, CDNS, SNPS
**US AI chip IP:** ASML (NASDAQ ADR)
**Global semis:** TSM (TSMC ADR), STM, IFX.DE, 8035.T (Tokyo Electron), 6857.T (Advantest), 6861.T (Keyence)
**UK AI chips:** IQE.L, OXIG.L, RSW.L
**UK AI infra:** BT-A.L, CCC.L, BYIT.L
**UK Oxford/Cambridge:** ONT.L, OXB.L, OXIG.L, ALFA.L, KNOS.L
**UK AI energy:** SSE.L, NG.L, ITM.L, DRX.L
**UK funds (AI exposure):** SMT.L, PCT.L, HGT.L
**UK large-cap:** AZN.L, REL.L, EXPN.L, SGE.L, WISE.L, AUTO.L + FTSE 100 majors
**Quantum:** IONQ, OKLO

---

## 14. Environment variables

```bash
# Broker — Alpaca paper
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets  # paper endpoint

# Broker — T212
T212_MODE=live          # or "demo"
T212_LIVE_API_KEY=...
T212_LIVE_API_SECRET=...
T212_DEMO_API_KEY=...
T212_DEMO_API_SECRET=...

# Data sources
FRED_API_KEY=...
ALTDATA_DB_PATH=altdata.db   # override altdata.db location

# OMS gate — NEVER set in normal operation
DATADESK_ARM_BROKER=0        # 1 = allow live broker calls (default: 0)
```

**`.env` is gitignored. Never commit it.**
The file currently contains live T212 keys — do not push to public GitHub until that is cleaned.

---

## 15. Hard rules

| Rule | Why |
|---|---|
| `PAPER_TRADE_MODE = True` hardcoded in `config.py` | Never change. Paper only until gate passes. |
| `DATADESK_ARM_BROKER` defaults to `"0"` in `oms.py` | Broker calls never execute in shadow mode. |
| No monitor auto-starts at serve time | Prevents unexpected market activity on startup. |
| Alt-data signals use `disclosure_date` not `transaction_date` | Congress trades are disclosed up to 45 days late. Using transaction date = lookahead. |
| Backtest weights shift by 1 day unconditionally | No lookahead possible by construction. |
| INSERT OR IGNORE on all bar writes | Safe to re-run any ingest without duplicates. |
| Never `git push` without explicit instruction | `.env` with live keys must be cleaned first. |

---

## 16. Current status & gate

### Gate 1 (holdout, last 252d, 25-ticker survivorship-biased universe)
| Metric | Strategy | SPY | Status |
|---|---|---|---|
| Sharpe | 1.96 | 1.73 | ✓ |
| MaxDD | −16% | −9% | ✗ |

The MaxDD failure is partly explained by survivorship bias (only winners in the 25-name universe). Gate requires honest universe from Tiingo/EODHD before re-evaluation.

### What's built (119 tests passing)
- Price history: 249 tickers, history.db
- Fundamentals: 32 tickers enriched (equity_info, equity_ratios, equity_financials, equity_balance)
- Alt-data: all trading-bot databases unified into altdata.db
- Strategy v2: momentum-core + bear_only_scale
- After-tax simulation: `tax.py` + `tax-compare` command
- Platform routing: `universe/platform.py` — ISA vs Alpaca classification
- Monte Carlo: real bootstrap on strategy returns, P5–P95 fan chart
- Dashboard: maintenance buttons, MC fan chart, T212 + Alpaca panels

### Open before public GitHub push
1. `.env` cleanup — remove live T212 keys and Twitter password
2. Move `live/` to private trading-bot repo per DESIGN boundary

### Next build priorities
1. Wire fundamentals into momentum strategy as a quality filter (ROE > 0, D/E < threshold)
2. Run `tax-compare` on expanded 249-ticker universe
3. Add fundamentals screener panel to dashboard (`/api/fundamentals` endpoint ready)
4. Company thesis generator using sector + valuation + growth data
5. Pay for Tiingo/EODHD → re-run holdout on honest universe → recheck Gate 1
