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
│   │   ├── fundamentals.py   yfinance fundamental fetcher → altdata.db
│   │   └── index_membership.py  ETF constituent registry → index_memberships table
│   │
│   ├── strategies/           signal generation
│   │   ├── momentum.py       cross-sectional 12-1 momentum (formation, skip, top_n)
│   │   ├── trend.py          200d trend filter with hysteresis band
│   │   ├── regime.py         VIX + bear_only_scale overlay (2-state)
│   │   ├── macro_regime.py   3-state economic regime (Expansion/Caution/Stress)
│   │   ├── phase.py          portfolio phase model (top_n by NAV: 3/6/10/15)
│   │   ├── congress_blend.py momentum with congressional buy score tilt
│   │   ├── meanrev.py        mean reversion (parked, not in live blend)
│   │   └── insider.py        congress/Form4 follow strategy (strategy 4)
│   │
│   ├── backtest/             backtesting
│   │   ├── engine.py         run_backtest() — vectorised, no-lookahead
│   │   ├── costs.py          CostModel, ALPACA_COSTS, T212_ISA_COSTS, ZERO_COSTS
│   │   ├── vol_target.py     vol_target_weights() — scales weights to 15% annual vol target
│   │   ├── tiers.py          exchange+market-cap cost tier assignment
│   │   ├── metrics.py        cagr(), sharpe(), max_drawdown(), summarize()
│   │   ├── walkforward.py    walk-forward harness with param-stability flag
│   │   ├── phase_backtest.py phase-aware backtest with monthly contributions
│   │   └── tax.py            UK CGT simulation — compare_tax_wrappers()
│   │
│   ├── analysis/             research and signal analysis
│   │   ├── congress_events.py  congressional trading event study
│   │   ├── trump_events.py     Trump post keyword-classification event study
│   │   ├── thesis.py           template-based investment thesis generator
│   │   ├── signal_audit.py     look-ahead bias audit / signal genesis report
│   │   └── forward_screener.py multi-factor forward screener + thematic radar
│   │
│   ├── universe/             platform classification
│   │   └── platform.py       classify(), split_by_platform(), available_on_*()
│   │
│   ├── live/                 OMS and monitors (ALL shadow mode by default)
│   │   ├── oms.py            shadow-first OMS — gated on DATADESK_ARM_BROKER=1
│   │   ├── shadow.py         signal audit store (every fast-path signal recorded)
│   │   ├── universe.py       active universe management
│   │   ├── market_calendar.py  exchange hours/holidays: NYSE, LSE, XETRA, TSE, HKEX
│   │   └── monitors/         event monitors (none auto-start)
│   │       ├── trump_monitor.py     TrumpMonitor — CNN polling + v3 taxonomy classifier
│   │       ├── supply_chain.py      SupplyChainMonitor — real v3 matrix + 1m yfinance
│   │       ├── news_monitor.py      Real RSS + Alpaca News, sentiment scoring, phi3:mini
│   │       ├── agent_worker.py      AgentWorker (requires Ollama)
│   │       ├── jensen_monitor.py    parked (no data source)
│   │       ├── rebalancer.py        Daily MOC rebalancer → best eligible sweep strategy
│   │       ├── price_feed.py        Alpaca websocket live price feed → OMS trailing stops
│   │       ├── research_analyst.py  Nightly stock discovery (momentum + quality + insider)
│   │       ├── strategy_analyst.py  Sweep analysis: promotions, overfitting, universe rank
│   │       └── risk_analyst.py      Portfolio risk: concentration, beta, correlation, drawdown
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
├── tests/                    143 tests (pytest)
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
- `backtest_runs` — saved results from every `run_backtest()` call (upsert by name: same name → replaces)
- `analyst_reports` — out-of-session analyst output (`analyst`: research/strategy/risk/news, `body`: plain-text, `data`: JSON payload)
- Monitored universe
- Shadow signal audit trail (every OMS fast-path signal)

---

## 3. Commands

```bash
# Core strategy
python main.py backtest          # momentum+trend backtest, save to platform.db
python main.py holdout           # full strategy comparison: tiered costs, regime, phase-aware
python main.py tax-compare       # 3-column after-tax: Alpaca pre, Alpaca post, ISA

# Server
python main.py serve [--port N]  # ops console on http://localhost:8000

# Data management
python main.py coverage          # print history store coverage table
python main.py universe          # print platform availability per ticker (T212 ISA / Alpaca)
python main.py backfill [--source yahoo|massive] [--no-fundamentals] T1 T2 ...
python main.py enrich [T1 T2 ...]   # refresh fundamentals only (all or subset)
python main.py weekly-update        # gap-fill prices + refresh stale fundamentals (Saturdays)
python main.py collect-trump        # refresh Trump CNN corpus
python main.py index-seed           # populate index_memberships table (SMH/QQQ/SPY/XLK)

# Research / discovery
python main.py screen               # forward screener: top buys by momentum+quality+congress+theme
python main.py signal-audit         # look-ahead bias audit: when did signal first fire per ticker?
python main.py universe-expand [--theme THEME] [--dry-run]  # add new tickers from themed ETFs
python main.py event-study [congress|trump]  # event study results
python main.py phase-projection [--monthly M] [--initial I] [--cagr C] [--years Y]
```

### `holdout` output
Sections printed:
1. **ALPACA tiered costs** — full period + holdout (last 252d) for equal-weight and inv-vol-weight
2. **T212 ISA tiered+FX** — same with 15bps FX per trade
3. **Congress-momentum blend** — equal-weight + congress score tilt vs pure momentum
4. **Economic regime** — Expansion/Caution/Stress day counts; 3-state overlay vs bear_only
5. **Phase-aware backtest** — £500 start + £500/mo contributions, dynamic top_n, transition log

Gate 1 = beat SPY on **both** Sharpe and MaxDD in the holdout section.

### `screen` output
Two sections:
1. **Thematic S-curve radar** — HOT/WARM/COLD for each tech theme (3-month momentum)
2. **Ranked stock table** — top 20 by composite score with all signal components visible

### `signal-audit` output
Per-ticker table showing:
- `1st Signal` — first date 6-1 momentum was positive (real-time, no lookahead)
- `Price@Sig` — closing price on signal date
- `From Sig%` / `Total%` — fraction of total return captured by signal
- `Look-ahead?` — YES if ticker was added after backtest start (selection bias warning)

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

### Phase-aware strategy (phase.py)

Portfolio scales with account size. `portfolio_phase(nav_gbp)` returns the phase for a given NAV:

| Phase | NAV | top_n | Min position |
|---|---|---|---|
| 1 — Accumulation | < £5k | 3 | £50 |
| 2 — Growth | £5k–£25k | 6 | £100 |
| 3 — Compounding | £25k–£100k | 10 | £500 |
| 4 — Scale | > £100k | 15 | £2,000 |

Phase is re-evaluated only at month-end rebalance dates to prevent daily threshold chatter.

### 3-state economic regime (macro_regime.py)

Extends the 2-state `bear_only_scale` with yield curve data:

| Regime | Condition | Scale |
|---|---|---|
| EXPANSION | SPY > 150dMA, VIX < 25, yield curve > -0.5% | 1.0 |
| CAUTION | yield curve < -0.5% OR VIX > 25 OR SPY < 150dMA | 0.65 |
| STRESS | SPY < 200dMA AND (VIX > 32 OR yield curve < -1%) | 0.35 |

Yield curve = ^TNX (10Y) minus ^IRX (3M) from yfinance. Free, no API key.
Backtest period breakdown: ~61% Expansion, ~36% Caution (2022-23 rate hike cycle), ~3% Stress.

### Congress-momentum blend (congress_blend.py)

Multiplicative boost to momentum scores for tickers with recent congressional buys:
- `congress_boost=2.0` → tickers with a congressional buy in last 45 days score 2× in ranking
- Event study shows +6.5% abnormal 20d return on congress buys, but net alpha over pure momentum is ~0 (momentum already captures it)
- Module kept as research tool; not recommended for live blend

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

**Vol-targeting (`backtest/vol_target.py`):**
```python
from datadesk.backtest.vol_target import vol_target_weights
w_scaled = vol_target_weights(weights, prices, target_vol=0.15, window=63, max_leverage=2.0)
```
Scales the weight matrix daily so the rolling realised portfolio vol targets 15% annualised.
Scale = target_vol / rolling_vol, capped at max_leverage. Applied before costs in the engine.
The sweep saves `[VOL15]` labelled variants for every combo alongside the raw version.

**Walk-forward OOS (`sweep.py: _run_walk_forward`):**
Expanding-window WFO — train on 3 years, test on the next year, expand training window, repeat.
Each fold saved as `{label} WFO fold-N`. Aggregate OOS metrics saved as `{label} WFO aggregate`.
Distinct from holdout: holdout tests the parameter selection process; WFO tests individual fold stability.

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

**Intraday / event-driven:**
- `trump_monitor` — polls CNN archive, classifies new posts via v3 taxonomy
- `supply_chain` — real supply-chain matrix + live 1m yfinance moves
- `news_monitor` — RSS (Reuters/MarketWatch/WSJ) + Alpaca News, keyword sentiment, phi3:mini signal
- `agent_worker` — Ollama-backed inference (requires local model)
- `jensen_monitor` — parked (no data source)
- `rebalancer` — fires at NYSE MOC window (15:48 ET) each trading day; picks best eligible 3y-holdout strategy; routes per-ticker via market_calendar; drift threshold 2%
- `price_feed` — Alpaca websocket; dynamically subscribes to all held US tickers; calls `oms.update_prices()` on each trade tick (trailing stops + take-profits); gracefully no-ops without keys

**Out-of-session analysts (run when NYSE is closed):**
- `research_analyst` — scores all tickers in altdata.db on momentum/quality/insider/congress composite; writes top-20 discovery candidates to analyst_reports
- `strategy_analyst` — loads all sweep results; flags overfitting (full_cagr/holdout > 2×); builds promotion/demotion list (Sharpe ≥ 1.0, MaxDD ≥ -30%, top_n ≥ 2); universe ranking by mean 1y CAGR
- `risk_analyst` — intraday fast check (30 min): daily loss + concentration; nightly deep: sector, beta, pairwise correlation, drawdown vs strategy expectation

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
| `/api/runs` | GET | Backtest leaderboard (latest per name, ordered by CAGR) |
| `/api/reports` | GET | Analyst reports (`?analyst=research\|strategy\|risk\|news&limit=N`) |
| `/api/alpaca/account` | GET | Alpaca paper account summary |
| `/api/alpaca/positions` | GET | Alpaca open positions |
| `/api/alpaca/mode` | GET/POST | Toggle Alpaca paper/live mode |
| `/api/t212/account` | GET | T212 live account cash summary |
| `/api/t212/positions` | GET | T212 open positions |
| `/api/t212/mode` | GET/POST | Toggle T212 demo/live mode |
| `/api/daemons/status` | GET | All monitor daemon statuses (includes new analysts + price_feed) |
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

## 16. Backtest realism & bias

### Honest assessment of the numbers

The headline CAGR of ~38% (full period 2016–2026) is overstated. Here is the honest decomposition:

| Source of bias | Estimated CAGR inflation | Fix |
|---|---|---|
| Survivorship bias (universe constructed in 2025) | 8–15% | Tiingo/EODHD point-in-time universe |
| NVDA + 4 stocks carrying the whole result | Not separable | Wider universe dilutes concentration |
| Execution at close vs T212 market open | ~0.3–1%/year | Accept — structural in T212 |
| UK AIM / micro-cap spread now L3 (fixed) | ✓ corrected | `backtest/tiers.py` |
| Congress signal adds no net alpha | ✓ confirmed | Pure momentum retained |

**Realistic range:** A properly constructed momentum strategy on S&P 500 / Nasdaq-100 earns ~8–12% CAGR gross, ~6–10% after costs. Our ~38% implies roughly 25% comes from selection bias and NVDA concentration.

### Signal genesis (was it hindsight?)

Run `python main.py signal-audit` to see the first date each ticker was identified by real-time momentum. Key finding:
- **NVDA**: first signal Aug 2014 at $0.46. Strategy captured 80% of the +54,850% total gain — momentum identified it during the gaming GPU super-cycle, before the AI narrative. No hindsight required.
- **No look-ahead tickers**: every ticker in the backtest had price data before the backtest start. Selection bias is in *which* stocks we track, not in the price series.

### Cost model realism (`backtest/tiers.py`)

`build_cost_tiers()` assigns half-spread by exchange and market cap:

| Tier | Half-spread | Applies to |
|---|---|---|
| L1 | 5bps | US/UK large-cap >$5B, ETFs |
| L2 | 15bps | US/UK mid-cap $500M–$5B, European, Japanese, HK |
| L3 | 40bps | UK AIM, US micro-cap <$500M, OTC/pink sheets |

Use `CostModel(tier_by_ticker=build_cost_tiers())` for realistic per-ticker costs.

### Index overlap

`python main.py index-seed` populates `index_memberships` and `holdout` reports overlap:
- Current backtest universe: ~18% SMH, ~16% QQQ, ~13% SPY overlap
- High overlap = strategy partly riding index rebalancing flows (not pure alpha)
- Target: keep any single index overlap below 40%

### What's still unrealistic (requires paid data)

1. **Point-in-time universe** — Tiingo/EODHD provide historical S&P 500 constituents by date. ~$20/mo. Eliminates survivorship bias entirely.
2. **Delisted stocks** — currently absent from history.db. All delistings are losses we didn't record.

---

## 17. Forward screener & stock discovery

### Finding the next NVDA

The next breakout stock will likely be *currently unknown* — a small-cap in a new S-curve before it becomes widely followed. The discovery pipeline:

**Step 1: Theme S-curve radar** (`python main.py screen` → Thematic S-curve radar section)
  - Tracks 7 tech themes: AI_INFRA, QUANTUM, DATACENTRE_POWER, OPTICAL_NET, SEMI_EQUIP, CLOUD_AI_SW, ENERGY_TRANS, UK_TECH
  - Score = % of theme members in top-quartile 3-month momentum
  - Score > 50% → theme in acceleration phase → strong buy signal for theme members
  - Example: AI_INFRA was 67% hot in June 2026 (NVDA, KLAC, MU all moving together)

**Step 2: Momentum ranking within hot themes** (forward screener composite score)
  - Momentum (50%) + Fundamental quality (30%) + Congress signal (20%) + Theme tilt (+5% bonus)
  - NVDA in 2014 would have scored: 80th percentile momentum + gaming GPU quality + no congress signal → top-10 composite

**Step 3: Universe expansion** (`python main.py universe-expand`)
  - Add all members of themed ETFs we don't yet track
  - Sources: SMH, QQQ, QTUM (quantum), BOTZ (robotics), AIQ (AI broad), CLOU (cloud)
  - After adding, momentum signal will discover unknown names automatically

**Step 4: News sentiment (not yet wired)**
  - Hook in `datadesk/analysis/forward_screener.py:news_sentiment_score()`
  - Free option: VADER on `news_articles` table headlines
  - Better: FinBERT on real-time financial news feeds
  - When wired, pass `news_weight=0.10` to `rank_universe()`

**Step 5: Form 4 insider buys on unknowns**
  - `insiders` table in altdata.db contains Form 4 filings
  - Executives buying their OWN company stock = strongest free "unknown stock" signal
  - Add to forward screener by joining `insiders` WHERE transaction_type='buy' AND date > 45d ago

### Quantum computing (next S-curve)

QUANTUM theme currently WARM (25% of members in top-quartile momentum as of June 2026).
Watch for this to go HOT as: (a) error correction milestones are hit, (b) enterprise pilots start.
Key names to track when theme accelerates: IONQ, GOOGL (quantum research), IBM, MSFT.
Add to universe via `universe-expand --theme QUANTUM` when price history is insufficient.

---

## 18. Current status & gate

### Gate 1 (holdout, last 252d, tiered-cost universe)
| Metric | Strategy (T212) | SPY | Status |
|---|---|---|---|
| Sharpe | 2.33 | 1.72 | ✓ |
| MaxDD | −14% | −9% | ✗ |

MaxDD gap persists. With survivorship-biased universe, our drawdowns look shallow because we only hold stocks that survived. Gate re-evaluation requires Tiingo/EODHD honest universe.

### What's built

**Data & alt-data:**
- Price history: 249 tickers, history.db; Fundamentals: 80+ tickers enriched
- Alt-data: congress event study, Trump post event study, insider filings, news, macro

**Strategy & backtesting:**
- Strategy v2: momentum-core + bear_only_scale + 3-state macro_regime
- Phase-aware backtest: £500 start + £500/mo → £308k final NAV over 9 years
- After-tax: ISA vs Alpaca taxable (ISA wins above 1.25% annual gain)
- Tiered cost model: L1/L2/L3 by exchange + market cap
- Sweep: ~1000 combos across 5 universe families; T212 ISA cost pass for EU/DEFENSIVE
- Vol-targeting: 15% annualised vol target scaling (sweep saves [VOL15] variants)
- Walk-forward OOS: expanding-window, 3y train / 1y test per fold (sweep saves WFO folds)
- Holdout windows: 1y, 3y, 5y per combo in platform.db

**Live / execution:**
- Shadow-first OMS: every signal recorded; broker gated on DATADESK_ARM_BROKER=1
- Daily rebalancer: fires at NYSE MOC window; picks best eligible 3y-holdout strategy
- Rebalancer filter: top_n ≥ 2, Sharpe ≥ 1.0, MaxDD ≥ -30%, prefers 3y holdout
- Exchange calendar: NYSE/LSE/XETRA/TSE/HKEX — trading day, MOC window, open/close checks
- Live price feed: Alpaca websocket → OMS trailing stops + take-profits
- T212 order execution: place_market_order, close_position, resolve_ticker (yf→T212 format)

**Analysts (out-of-session):**
- Research analyst: nightly discovery scan, composite score (momentum/quality/insider/congress)
- Strategy analyst: sweep analysis, promotion/demotion list, overfitting detection
- Risk analyst: intraday concentration + daily loss; nightly sector/beta/correlation/drawdown
- News monitor: real RSS feeds + Alpaca News; sentiment scoring; analyst_reports

**Dashboard & API:**
- /api/reports endpoint: fetch analyst output by type and recency
- /api/runs: deduped leaderboard (MAX(id) per name, CAGR DESC)
- Daemon panel covers all 10 daemons including new analysts + price_feed

### Open before public GitHub push
1. `.env` cleanup — remove live T212 keys
2. Decide: keep `live/` in this repo or move to private trading-bot repo

### Remaining paid-data gap
- Tiingo/EODHD (~$20–29/mo) for point-in-time S&P 500 constituents → honest holdout

---

## 19. Upcoming / Roadmap (Options Trading)

Options trading is a planned extension for the Alpaca US book only (options are not permitted in the T212 ISA). This will be rolled out strictly after the core equity book has proven stable in live execution for at least 4 weeks.

**Planned Options Overlays:**
1. **Income Generation:** Covered calls on held momentum winners (~30-45 DTE, ~0.30 delta) and cash-secured puts for paid entries on mean-reversion flags.
2. **Hedging:** Buying SPY put spreads sized to cut portfolio beta during short-lived event risk (e.g., tariff whipsaws), replacing the current "sell to cash" overlay behaviour. Total premium spend capped at 1% NAV per quarter.

**Validation Strategy:** 
Free historical option-chain data is not reliable. Validation will rely on synthetic pricing (Black-Scholes on per-stock realised volatility) cross-checked against CBOE benchmarks (BXM, PUT). Real historical IV data (e.g., ORATS) will be procured before any significant capital scaling.

**Safety Limits:**
- No naked short options ever.
- Short calls 100% covered by shares; short puts 100% cash-secured.
- Net options notional ≤ 20% NAV.
