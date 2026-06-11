# Backtesting protocol

How strategies are validated in this repo, and why the protocol is stricter than
"run it over history and report the number".

## The engine

Vectorised daily-bar engine ([engine.py](../datadesk/backtest/engine.py)):

- A strategy emits a **target-weights frame** (dates × tickers). Sparse is fine —
  the engine forward-fills between rebalances.
- **No lookahead by construction:** weights set at the close of day *t* earn day
  *t+1*'s return. The first day of any backtest earns exactly zero.
- **Costs** are charged on |Δweight| per day: half-spread by liquidity tier
  (L1 5bp / L2 15bp / L3 40bp) + commission + FX fee
  ([costs.py](../datadesk/backtest/costs.py)). The T212 ISA book adds 15bp FX.

## Validation, in order of rigour

1. **In-sample sweep** — exploration only. Never quoted as a result.
2. **Walk-forward** ([walkforward.py](../datadesk/backtest/walkforward.py)) —
   train 18 months, pick the best params on train only, run them untouched on the
   next 6 months, roll forward. The stitched out-of-sample series is the quotable
   number.
3. **Param stability** — the share of walk-forward segments choosing the modal
   parameter set. Low stability = the "edge" moves when the window moves = overfit
   flag, reported alongside every result.
4. **Holdout** — the final 12 months of data are never used during development.
   One evaluation, at the end, before any deployment decision.

## Rules we hold ourselves to

- Parameter grids report the **distribution** across the grid, not the best cell.
- Alt-data joins use `observed_at` (when we could have known), never `event_at`
  (when it happened). Congress trades lag disclosure by 30–45 days; backtests
  must too.
- A strategy that only works at one parameter point is rejected, not tuned.
- Costs are never optional. There is no "gross of costs" headline number.

## Current status (2026-06-11 smoke test, migrated legacy data)

Universe: **18 tickers with full 5y history** — far too small for cross-sectional
momentum to mean much (top-10 of 18 is barely selection). Until the Stooq/Alpaca
backfill widens the universe and extends history past one market regime, every
number below is plumbing-verification, not evidence:

- Momentum walk-forward OOS (2024-01 → 2026-04): CAGR 16.9%, Sharpe 0.68,
  max DD −30.4%, param stability 0.4 (unstable — expected on this universe)
- Momentum + 200d trend filter (fixed params, 2022-06 →): CAGR 11.2%, Sharpe 0.63
- SPY buy-and-hold, same window: CAGR 15.1%, Sharpe 0.91 — **the benchmark
  currently wins**; the system has work to do, and saying so is the point of
  this document.
