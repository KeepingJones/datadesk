import sqlite3
import pandas as pd
from datadesk.history.store import load_closes
from sweep import UNIVERSES, BENCHMARKS, _backfill_missing, _load_universe
from datadesk.backtest.engine import run_backtest
from datadesk.strategies.momentum import momentum

# 1. Backfill just SPY and the benchmarks
_backfill_missing(["NVDA", "AMD", "SPY"] + BENCHMARKS)

# 2. Load
prices = load_closes(tickers=["NVDA", "AMD", "SPY"])
print(f"Prices shape: {prices.shape}")

bm_prices = load_closes(tickers=BENCHMARKS)
print(f"BM Prices shape: {bm_prices.shape}")
bm_returns = bm_prices.pct_change(fill_method=None)

# 3. Run simple backtest
w = momentum(126, 2, 21)(prices)
res = run_backtest(w, prices, start="2020-01-01", benchmark_returns=bm_returns)

print("\n--- Metrics with Benchmarks ---")
for k, v in res.metrics.items():
    print(f"{k}: {v}")
