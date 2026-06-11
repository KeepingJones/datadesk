# DataDesk

A market data platform for a paper GBP fund — the internal tooling a fund's market
data team actually runs: multi-source price reconciliation, dataset catalogue,
quality scoring, backtesting with honest validation, and usage/cost allocation.

**Paper only. `PAPER_TRADE_MODE = True` is hardcoded. No live capital, ever.**

## What exists today

| Module | What it does |
|---|---|
| `datadesk/quality/` | Multi-source price reconciliation: pairwise cross-checks, 7-cause break classification, liquidity-tiered tolerances (L1/L2/L3 by ADV) |
| `datadesk/ingest/` | Source connectors: Yahoo Finance, FRED, ECB reference rates |
| `datadesk/history/` | Canonical daily OHLCV store (SQLite/WAL) + legacy migration |
| `datadesk/backtest/` | Vectorised engine, tiered cost model, walk-forward validation, metrics |
| `datadesk/strategies/` | Cross-sectional momentum, trend filter, mean reversion, VIX regime overlay |
| `tests/` | 64 tests, no network access, CI-gated |

See [docs/backtesting.md](docs/backtesting.md) for the validation protocol — the
short version: walk-forward only, costs always on, param-instability is a rejection
flag, and the current honest result is that the benchmark still wins on the small
migrated universe.

## Quickstart

```bash
pip install -e .[dev]
pytest                      # 64 tests, ~4s, no network
ruff check .
```

## Roadmap (explicitly not built yet)

Dataset catalogue with license/entitlement tags · LLM-assisted dataset discovery
and tagging (Ollama) · per-desk usage tracking and cost allocation · SLA breach/
rebate analytics · ops-console dashboard · wider universe backfill (Stooq/Alpaca)
· alt-data signals (SEC EDGAR, congress disclosures) with point-in-time joins.

## Why SQLite + DuckDB and not KDB+/TimescaleDB?

Daily-bar scale (hundreds of tickers × ~10y ≈ low millions of rows) doesn't justify
a tick-database's operational cost. SQLite (WAL) handles transactional writes;
DuckDB attaches the same file for columnar analytical reads. The right tool for the
data volume is part of the design, not a compromise.
