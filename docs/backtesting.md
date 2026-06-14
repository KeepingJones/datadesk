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

1. **In-sample sweep** (`sweep.py`) — ~1000 combos across 5 universe families (AI/Semi, EU
   Regional, Defensive, Global Macro, Small-Cap Growth). Exploration only. Never quoted as a result.

2. **Walk-forward OOS** (`sweep.py: _run_walk_forward`) — expanding window: 3y train, 1y test,
   expand, repeat. Each fold saved as `{label} WFO fold-N`; aggregate saved as `WFO aggregate`.
   Parameters are fixed from the sweep — the WFO tests fold-to-fold stability, not selection.

3. **Param stability** (`backtest/walkforward.py`) — share of walk-forward segments choosing
   the modal parameter set. Low stability = overfit flag.

4. **Holdout windows** (1y / 3y / 5y) — saved per combo in platform.db as `{label} HOLDOUT Ny`.
   The rebalancer and strategy analyst filter to 3y holdout as the most reliable OOS signal.

5. **Final holdout gate** — the last 252d never used during development; one evaluation at the
   end before any deployment decision.

## Vol-targeting

```python
from datadesk.backtest.vol_target import vol_target_weights
w_scaled = vol_target_weights(weights, prices, target_vol=0.15, window=63)
```

Scales the weight matrix so the rolling portfolio vol targets 15% annualised. Scale factor =
target / rolling_vol, capped at 2×. The sweep saves `[VOL15]` variants for every combo so the
impact is visible in the leaderboard. Vol-targeting reduces tail risk in trending markets and
prevents outsized drawdowns when a universe becomes highly correlated.

## Rebalancer filter (promotion to live)

A strategy is eligible for live deployment via the daily rebalancer only if:
- **Sharpe ≥ 1.0** in the holdout period
- **MaxDD ≥ −30%** in the holdout period
- **top_n ≥ 2** — single-name concentrated positions excluded
- **Source**: 3y holdout preferred over 1y; any holdout over full-period in-sample

These filters are enforced in `_get_best_run()` in `rebalancer.py`.

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
