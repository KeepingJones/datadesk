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

## Current status (2026-06-13, strategy v2)

Universe: **25 tickers** (survivorship-biased — absolute levels inflated until
Tiingo/broader backfill). Strategy: momentum-core + bear_only_scale overlay
(de-risk to 0.4 only when SPY < 200dMA AND VIX > 30). Mean reversion and
insider overlays dropped from the live blend after attribution showed they dragged.

Full period results:
- momentum-core + bear overlay: CAGR +46.8%, Sharpe 1.61, MaxDD -26%, turn 6.9x
- SPY benchmark: CAGR +15.2%, Sharpe 0.87, MaxDD -34%

Holdout (last 252d, untouched until now):
- Strategy: CAGR +57.3%, Sharpe 1.96, MaxDD -16%
- SPY: CAGR +22.9%, Sharpe 1.73, MaxDD -9%

**Gate 1 verdict:** Sharpe ✓ (1.96 vs 1.73) — MaxDD ✗ (−16% vs −9%). Gate not
met on the drawdown leg in the holdout. Next step: widen universe via paid backfill
(Tiingo decision pending) before re-evaluating.
