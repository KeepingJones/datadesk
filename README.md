# DataDesk

A market data platform built to mirror the internal tooling a prop trading firm or systematic fund would maintain: dataset catalogue, multi-source quality reconciliation, alternative data ingestion, AI-assisted tagging, usage/cost tracking, and a quantitative strategy library with honest backtests.

**All trading is paper-only. `PAPER_TRADE_MODE = True` is hardcoded and never changes.**

---

## What it does

| Layer | What's built |
|---|---|
| **Quality** | Multi-source price reconciliation with 7-cause break classifier and liquidity-tiered tolerances |
| **History** | Canonical daily-bar store — 221k+ bars across 168 tickers, 5y+ depth |
| **Alt-data** | Congress (STOCK Act) trades, Form 4 insiders, SEC filings, macro series (724k rows), Trump communications corpus (33k posts), WSB sentiment archive |
| **Strategies** | Cross-sectional momentum, trend filter, mean reversion, VIX regime overlay, congress/insider follow — walk-forward validated, costs always on |
| **Sweep** | ~1000-combo parameter sweep across 5 universe families (AI/Semi, EU, Defensive, Global Macro, Small-Cap); vol-targeting and expanding-window WFO variants; 1y/3y/5y holdout windows per combo |
| **Backtesting** | Vectorised engine, tiered cost model (L1/L2/L3 + FX), vol-targeting (15% target), walk-forward OOS (expanding window); T212 ISA vs Alpaca cost comparison |
| **OMS** | Shadow-first fast-path OMS: every signal recorded, broker execution gated on `DATADESK_ARM_BROKER=1`; live Alpaca websocket price feed → trailing stops |
| **Daily rebalancer** | Fires at NYSE MOC window; picks highest-Sharpe 3y-holdout strategy (top_n ≥ 2, MaxDD ≥ −30%); routes per-ticker to the correct exchange |
| **Out-of-session analysts** | Research analyst (nightly stock discovery + insider/congress scoring), Strategy analyst (sweep review, promotion/demotion, overfitting flags), Risk analyst (concentration, beta, correlation, drawdown checks) |
| **News monitor** | Real RSS feed polling (Reuters/MarketWatch/WSJ) + Alpaca News API; keyword sentiment scoring; optional phi3:mini LLM signal; saves to analyst_reports |
| **Dashboard** | Dark ops-console UI — backtest leaderboard, equity curves, universe management, P&L summary, AI feed, Alpaca paper + T212 ISA status, Monte Carlo bootstrap simulations |

---

## Architecture

```
datadesk/
├── quality/        price reconciliation engine, 7-cause break classifier, liquidity tiers
├── history/        canonical daily-bar store (SQLite WAL) + migration tooling
├── ingest/         yahoo, FRED, ECB, yfinance backfill, Massive free-tier, t212_client
├── strategies/     momentum, trend, meanrev, regime overlays, insider/congress follow
├── backtest/       vectorised engine, cost model, vol_target, walk-forward harness, metrics
├── ai/             Trump post classifier (v3 taxonomy, deterministic + Ollama fallback)
├── live/
│   ├── oms.py               shadow-first OMS, gated on DATADESK_ARM_BROKER=1
│   ├── market_calendar.py   exchange hours/holidays (NYSE, LSE, XETRA, TSE, HKEX)
│   └── monitors/
│       ├── rebalancer.py        daily MOC rebalancer → best 3y-holdout strategy
│       ├── price_feed.py        Alpaca websocket → OMS trailing stops
│       ├── research_analyst.py  nightly stock discovery (momentum + quality + insider)
│       ├── strategy_analyst.py  sweep review: promotions, overfitting, universe ranking
│       ├── risk_analyst.py      concentration, sector, beta, correlation, drawdown checks
│       ├── news_monitor.py      RSS + Alpaca News feed with sentiment scoring
│       ├── trump_monitor.py     CNN polling + v3 taxonomy
│       ├── supply_chain.py      supply-chain matrix event monitor
│       ├── agent_worker.py      Ollama-backed inference worker
│       └── jensen_monitor.py    parked
├── api/            FastAPI — all dashboard + /api/reports endpoint
├── dashboard/      Single-page ops console (Jinja2 + Chart.js, no build chain)
└── sweep.py        ~1000-combo parameter sweep (5 universes, vol-target, WFO, T212 costs)

altdata.db          All alt-data: congress, insiders, filings, news, macro, fundamentals
history.db          Daily OHLCV bars (221k+)
platform.db         Backtest runs, analyst_reports, shadow signal audit trail
```

---

## Data sources

| Source | What | Volume |
|---|---|---|
| Yahoo Finance / yfinance | Daily OHLCV, FX, indices | 221k bars |
| FRED | Macro series (yields, spreads, VIX, credit) | 724k rows |
| ECB | EUR reference rates | — |
| Massive (free tier) | US daily bars (recent 2y) | supplemental |
| SEC EDGAR | Form 4 insider transactions | 267 filings |
| Congress STOCK Act | Legislator trade disclosures | 16k rows |
| CNN Truth Social archive | Trump posts 2022→present | 33k posts |
| SEC filings (processed) | Earnings, 8-K, 10-K text | 38k accessions |
| News (multi-source) | Ticker-tagged headlines | 55k articles |
| WSB sentiment | Reddit mention/sentiment (archived) | 74k daily records |

---

## Backtesting protocol

Full protocol: [docs/backtesting.md](docs/backtesting.md)

Key constraints:
- **No lookahead by construction** — weights at close of day *t* earn day *t+1*'s return
- **Costs always on** — half-spread by liquidity tier + commission + FX; no gross-of-costs headlines
- **Walk-forward only** — train 18m → test 6m rolling; in-sample sweep is exploration, never the result
- **Alt-data point-in-time** — congress/insider joins use `disclosure_date`/`filing_date`, not event date; 30–45 day disclosure lag is respected
- **Holdout untouched** — final 12 months never seen during development; one evaluation at the end

Current results (25-ticker universe, momentum-core + bear overlay):
- Full period: CAGR 46.8%, Sharpe 1.61, MaxDD -26% vs SPY CAGR 15.2%, Sharpe 0.87
- Holdout (last 252d): Sharpe 1.96 vs SPY 1.73 — Gate 1 Sharpe met; MaxDD -16% vs SPY -9% not yet met
- **Caveat:** 25-name survivorship-biased universe — absolute levels inflated until Tiingo/broader backfill

---

## Quickstart

```bash
git clone https://github.com/KeepingJones/datadesk.git
cd datadesk
python -m venv .venv && .venv/Scripts/activate  # Windows
pip install -e ".[dev]"
cp .env.example .env  # add ALPACA_API_KEY, ALPACA_SECRET_KEY, FRED_API_KEY

python main.py          # backtest + holdout report, then serve on :8000
python main.py serve    # ops console only (skip backtest)
python main.py holdout  # holdout report only
```

---

## Why SQLite and not KDB+/InfluxDB/Snowflake?

Deliberate choice: a prop trading firm evaluating this cares about correctness and reasoning, not tooling familiarity signalled by infra complexity.

- **SQLite WAL** gives serialisable reads + concurrent writers with zero ops overhead — entirely appropriate for 200k-bar research datasets
- **No KDB+** because the data volume doesn't justify a columnar time-series store; at this scale SQLite beats it on query latency for most access patterns
- **No cloud warehouse** because the point-in-time discipline (no lookahead) is easier to enforce and audit in a local store where every write is explicit
- If this were a production system with 50+ sources and tick-level ingest, the answer changes — and the architecture is designed to make that migration straightforward

---

## Deployment gate (paper → live)

1. Holdout: blended portfolio Sharpe ≥ 1.0, MaxDD ≤ 20%, beats SPY on both metrics
2. Paper: 8+ weeks Alpaca paper — live results within 1 sigma of backtest expectation
3. Then: Alpaca live US with capped capital; T212 ISA picks manual-confirm before any automation
4. Always-on: per-position size limits (10% max), portfolio kill switch at -10% from peak

Live execution: never. `PAPER_TRADE_MODE = True` is hardcoded.
