"""
Gate 1 honest re-run — 2026-07-20.

Re-runs the documented backtesting protocol (docs/backtesting.md) on the
survivorship-corrected universe (existing yfinance-covered names + genuinely
delisted/acquired tickers backfilled via Tiingo — see
docs/gate1_report_2026-07-20.md for the full writeup).

Stages, in the order docs/backtesting.md lists them:
  1. Walk-forward OOS: 2y train / 6m test, rolling (backtest/walkforward.py
     walk_forward(), train_days=504, test_days=126 — the literal defaults,
     matching the "2y train, 6m test" protocol description). Costs always on
     (tiered by liquidity). Reports param-stability.
  2. Final holdout gate: last 252 trading days, untouched by the walk-forward
     parameter selection above. Reported both raw and with vol-targeting
     (backtest/vol_target.py, 15% target) applied to address the MaxDD gate.

Does not modify any existing methodology — reuses run_backtest(), walk_forward(),
vol_target_weights(), momentum(), bear_only_scale() exactly as they exist.
Saves every run to platform.db via save_backtest_run() so results also surface
on the dashboard leaderboard.

Usage: python scripts/gate1_honest_rerun.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from datadesk.backtest.costs import ZERO_COSTS, CostModel
from datadesk.backtest.engine import run_backtest
from datadesk.backtest.tiers import build_cost_tiers
from datadesk.backtest.vol_target import vol_target_weights
from datadesk.backtest.walkforward import grid, walk_forward
from datadesk.db import save_backtest_run
from datadesk.history.store import coverage, load_closes
from datadesk.ingest.fundamentals import load_quality_excludes
from datadesk.ingest.index_membership import index_overlap_report
from datadesk.strategies.momentum import momentum
from datadesk.strategies.regime import bear_only_scale

RUN_TAG = "2026-07-20 HONEST"

DELISTED_ADDED = {
    "XLNX", "CERN", "ATVI", "MXIM", "CTXS", "SPLK", "FLIR", "ABMD", "SGEN", "CAVM",
    "TWTR", "SIVB", "SBNY",
}


def strategy_factory(lookback: int, top_n: int, eligible: frozenset):
    """momentum(126,10,21)-style factory + fixed bear_only_scale overlay, closed
    over the quality-filtered eligible set. Matches cmd_holdout's v2 blend
    exactly; only lookback/top_n are swept by the walk-forward grid."""

    def build(prices_slice: pd.DataFrame) -> pd.DataFrame:
        w = momentum(lookback, top_n, 21, quality_universe=eligible)(prices_slice)
        if "SPY" in prices_slice.columns and "^VIX" in prices_slice.columns:
            scale = bear_only_scale(prices_slice["SPY"], prices_slice["^VIX"])
            w = w.mul(scale, axis=0)
        return w

    return build


def line(tag: str, w: pd.DataFrame, prices: pd.DataFrame, costs: CostModel, start: str) -> dict:
    m = run_backtest(w, prices, costs, start=start).metrics
    print(
        f"  {tag:38s} CAGR {m['cagr']:+.3f}  Sharpe {m['sharpe']:.2f}  "
        f"MaxDD {m['max_drawdown']:.2f}  turn {m.get('avg_annual_turnover', 0):.1f}"
    )
    return m


def main() -> None:
    cov = coverage()
    tickers = cov[cov["rows"] > 2000]["ticker"].tolist()
    print(f"Universe passing rows>2000 filter: {len(tickers)} tickers")
    added_present = sorted(DELISTED_ADDED & set(tickers))
    print(f"Delisted names cleared the filter and are in-universe: {added_present}")

    prices = load_closes(tickers=tickers)
    prices = prices[prices.index >= "2016-05-24"].ffill().dropna(axis=1)

    excluded = load_quality_excludes()
    eligible = frozenset(set(prices.columns) - excluded)
    print(
        f"Universe: {prices.shape[1]} tickers, {prices.shape[0]} days "
        f"| quality filter excluded {len(excluded)} -> {len(eligible)} eligible"
    )
    delisted_eligible = sorted(DELISTED_ADDED & eligible)
    print(f"Delisted names eligible after quality filter: {delisted_eligible}")

    overlap = index_overlap_report(list(eligible))
    if overlap:
        print("Index overlap (of eligible): " + "  ".join(f"{k}: {v}%" for k, v in sorted(overlap.items())))

    ticker_tiers = build_cost_tiers()
    ALPACA_TIERED = CostModel(tier_by_ticker=ticker_tiers, commission_bps=0.0, fx_fee_bps=0.0)
    T212_TIERED = CostModel(tier_by_ticker=ticker_tiers, commission_bps=0.0, fx_fee_bps=15.0)

    warmup = prices.index[min(150, len(prices) - 1)]
    holdout_start = prices.index[max(len(prices) - 252, 151)]
    spy_w = pd.DataFrame({"SPY": [1.0]}, index=[prices.index[0]]) if "SPY" in prices else None

    # ------------------------------------------------------------------
    # Stage 1: Walk-forward OOS -- 2y train / 6m test, rolling
    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("STAGE 1: WALK-FORWARD OOS (train_days=504 [~2y], test_days=126 [~6m])")
    print("=" * 78)

    def factory(**params):
        return strategy_factory(eligible=eligible, **params)

    param_grid = grid(lookback=[63, 126, 252], top_n=[5, 10, 15])
    wf_result = walk_forward(
        prices,
        strategy_factory=factory,
        param_grid=param_grid,
        train_days=504,
        test_days=126,
        cost_model=ALPACA_TIERED,
        warmup_days=280,
    )
    print(f"  Segments: {len(wf_result.segments)}")
    print(f"  Param stability (modal param share): {wf_result.param_stability:.2f}")
    print(f"  Stitched OOS metrics: {wf_result.metrics}")
    for seg in wf_result.segments:
        print(f"    train {seg['train']} -> test {seg['test']}  params={seg['params']}  "
              f"test_sharpe={seg['test_metrics'].get('sharpe')}")
    save_backtest_run(
        f"v2 momentum+bear {RUN_TAG} WFO aggregate (2y/6m)",
        {"train_days": 504, "test_days": 126, "grid": param_grid, "param_stability": wf_result.param_stability},
        wf_result.metrics,
        (1 + wf_result.returns).cumprod(),
    )

    # ------------------------------------------------------------------
    # Stage 2: Final holdout gate -- last 252 trading days, RAW vs VOL-TARGETED
    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("STAGE 2: FINAL HOLDOUT GATE (last 252 trading days)")
    print("=" * 78)

    w_eq = momentum(126, 10, 21, quality_universe=eligible)(prices)

    def apply_bear(w):
        if "SPY" in prices.columns and "^VIX" in prices.columns:
            scale = bear_only_scale(prices["SPY"], prices["^VIX"])
            return w.mul(scale, axis=0)
        return w

    w_strat = apply_bear(w_eq)
    w_strat_vt = vol_target_weights(w_strat, prices, target_vol=0.15, window=63, max_leverage=2.0)

    results = {}
    for cost_label, costs in [("ALPACA tiered", ALPACA_TIERED), ("T212 ISA tiered+FX", T212_TIERED)]:
        print(f"\n--- {cost_label} ---")
        print(" FULL PERIOD:")
        m_full_raw = line("v2 raw", w_strat, prices, costs, str(warmup.date()))
        m_full_vt = line("v2 + vol-target 15%", w_strat_vt, prices, costs, str(warmup.date()))
        if spy_w is not None:
            line("SPY benchmark", spy_w, prices, ZERO_COSTS, str(warmup.date()))
        print(" HOLDOUT (last 252d):")
        m_hold_raw = line("v2 raw", w_strat, prices, costs, str(holdout_start.date()))
        m_hold_vt = line("v2 + vol-target 15%", w_strat_vt, prices, costs, str(holdout_start.date()))
        m_hold_spy = None
        if spy_w is not None:
            m_hold_spy = line("SPY benchmark", spy_w, prices, ZERO_COSTS, str(holdout_start.date()))

        results[cost_label] = {
            "full_raw": m_full_raw, "full_vt": m_full_vt,
            "holdout_raw": m_hold_raw, "holdout_vt": m_hold_vt, "holdout_spy": m_hold_spy,
        }

        if cost_label.startswith("ALPACA"):
            save_backtest_run(f"v2 raw {RUN_TAG} (full)", {}, m_full_raw,
                               run_backtest(w_strat, prices, costs, start=str(warmup.date())).equity)
            save_backtest_run(f"v2 raw {RUN_TAG} HOLDOUT 252d", {}, m_hold_raw,
                               run_backtest(w_strat, prices, costs, start=str(holdout_start.date())).equity)
            save_backtest_run(f"v2 [VOL15] {RUN_TAG} (full)", {"target_vol": 0.15}, m_full_vt,
                               run_backtest(w_strat_vt, prices, costs, start=str(warmup.date())).equity)
            save_backtest_run(f"v2 [VOL15] {RUN_TAG} HOLDOUT 252d", {"target_vol": 0.15}, m_hold_vt,
                               run_backtest(w_strat_vt, prices, costs, start=str(holdout_start.date())).equity)

    print("\n" + "=" * 78)
    print("GATE 1 VERDICT")
    print("=" * 78)
    a = results["ALPACA tiered"]
    spy = a["holdout_spy"]
    for tag, m in [("raw", a["holdout_raw"]), ("vol-targeted 15%", a["holdout_vt"])]:
        sharpe_pass = m["sharpe"] >= 1.0
        dd_pass_doc = m["max_drawdown"] >= -0.20  # documented gate: MaxDD <= 20%
        cagr_pass_doc = m["cagr"] >= 0.15
        dd_pass_spy = m["max_drawdown"] >= spy["max_drawdown"] if spy else None
        sharpe_pass_spy = m["sharpe"] >= spy["sharpe"] if spy else None
        print(f"\n  [{tag}] Sharpe {m['sharpe']:.2f}  MaxDD {m['max_drawdown']:.2%}  CAGR {m['cagr']:.2%}")
        print(f"    Documented gate (Sharpe>=1.0, MaxDD<=20%, CAGR>=15%): "
              f"Sharpe {'PASS' if sharpe_pass else 'FAIL'}, MaxDD {'PASS' if dd_pass_doc else 'FAIL'}, "
              f"CAGR {'PASS' if cagr_pass_doc else 'FAIL'}")
        if spy:
            print(f"    SPY-relative (beat SPY Sharpe {spy['sharpe']:.2f} & MaxDD {spy['max_drawdown']:.2%}): "
                  f"Sharpe {'PASS' if sharpe_pass_spy else 'FAIL'}, MaxDD {'PASS' if dd_pass_spy else 'FAIL'}")

    out = {
        "universe_tickers": prices.shape[1],
        "delisted_added_in_universe": added_present,
        "delisted_eligible": delisted_eligible,
        "walk_forward": {"segments": len(wf_result.segments), "param_stability": wf_result.param_stability,
                          "metrics": wf_result.metrics},
        "holdout": results,
    }
    out_path = Path(__file__).resolve().parent.parent / "docs" / "gate1_rerun_2026-07-20_raw_output.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nRaw output saved to {out_path}")
    print("Saved to platform store.")


if __name__ == "__main__":
    main()
