"""
Strategy v3 vs v2 comparison — non-destructive sibling of gate1_honest_rerun.py.

Runs the SAME honest protocol (walk-forward OOS 2y-train/6m-test + last-252d
holdout, tiered costs) on the SAME survivorship-corrected universe, for three
arms so we can attribute where any improvement actually comes from:

  v2       momentum × bear_only_scale                     (documented baseline)
  v2+VT    momentum × bear_only_scale × vol_target(15%)   (ablation: VT only)
  v3       residual_momentum × regime_tier_scale × vol_target(15%)   (full v3)

The v2 vs v2+VT gap isolates the vol-target effect; the v2+VT vs v3 gap isolates
v3's genuine novelty (market-neutral residual momentum + the 4-tier graduated
regime). The trustworthy number is the stitched walk-forward Sharpe/MaxDD, not
the single-window holdout (see docs/gate1_report_2026-07-20.md).

Reuses run_backtest / walk_forward / vol_target_weights / momentum /
bear_only_scale unchanged. Does NOT write to platform.db (exploratory); dumps a
JSON next to the gate1 output for the record.

Note vs gate1: SPY and ^VIX are removed from the *selectable* set here (they are
the benchmark / vol index, not holdings) — applied identically to every arm, so
the comparison stays controlled. Everything else matches gate1_honest_rerun.py.

Usage: python scripts/gate_v3_compare.py
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from datadesk.backtest.costs import ZERO_COSTS, CostModel
from datadesk.backtest.engine import run_backtest
from datadesk.backtest.tiers import build_cost_tiers
from datadesk.backtest.vol_target import vol_target_weights
from datadesk.backtest.walkforward import grid, walk_forward
from datadesk.history.store import coverage, load_closes
from datadesk.ingest.fundamentals import load_quality_excludes
from datadesk.strategies.momentum import momentum
from datadesk.strategies.momentum_v3 import regime_tier_scale, residual_momentum
from datadesk.strategies.regime import bear_only_scale

TARGET_VOL, VT_WINDOW, VT_MAXLEV = 0.15, 63, 2.0
HOLDOUT_LOOKBACK, HOLDOUT_TOP_N, SKIP = 126, 10, 21


def _apply_vt(w, px):
    return vol_target_weights(w, px, target_vol=TARGET_VOL, window=VT_WINDOW, max_leverage=VT_MAXLEV)


def make_builds(selectable):
    """Return {arm_name: factory(**params) -> build(prices) -> weights}."""

    def v2(lookback, top_n):
        def build(px):
            w = momentum(lookback, top_n, SKIP, quality_universe=selectable)(px)
            if "SPY" in px.columns and "^VIX" in px.columns:
                w = w.mul(bear_only_scale(px["SPY"], px["^VIX"]), axis=0)
            return w
        return build

    def v2_vt(lookback, top_n):
        inner = v2(lookback, top_n)
        return lambda px: _apply_vt(inner(px), px)

    def v3(lookback, top_n):
        def build(px):
            w = residual_momentum(lookback, top_n, SKIP, quality_universe=selectable)(px)
            if "SPY" in px.columns and "^VIX" in px.columns:
                w = w.mul(regime_tier_scale(px["SPY"], px["^VIX"]), axis=0)
            return _apply_vt(w, px)
        return build

    return {"v2": v2, "v2+VT": v2_vt, "v3": v3}


def fmt(m: dict) -> str:
    return (f"Sharpe {m['sharpe']:5.2f}  MaxDD {m['max_drawdown']:7.2%}  "
            f"CAGR {m['cagr']:7.2%}  turn {m.get('avg_annual_turnover', 0):.1f}")


def main() -> None:
    cov = coverage()
    tickers = cov[cov["rows"] > 2000]["ticker"].tolist()
    prices = load_closes(tickers=tickers)
    prices = prices[prices.index >= "2016-05-24"].ffill().dropna(axis=1)

    excluded = load_quality_excludes()
    selectable = frozenset(set(prices.columns) - set(excluded) - {"SPY", "^VIX"})
    print(f"Universe: {prices.shape[1]} cols, {prices.shape[0]} days | "
          f"quality-excluded {len(set(excluded) & set(prices.columns))} | "
          f"{len(selectable)} selectable (SPY/^VIX held out of selection)")

    tiers = build_cost_tiers()
    ALPACA = CostModel(tier_by_ticker=tiers, commission_bps=0.0, fx_fee_bps=0.0)

    warmup = prices.index[min(150, len(prices) - 1)]
    holdout_start = prices.index[max(len(prices) - 252, 151)]
    spy_w = pd.DataFrame({"SPY": [1.0]}, index=[prices.index[0]]) if "SPY" in prices else None

    builds = make_builds(selectable)
    param_grid = grid(lookback=[63, 126, 252], top_n=[5, 10, 15])

    wf_out, hold_out = {}, {}

    # ── Stage 1: walk-forward OOS (the trustworthy, multi-regime number) ──────
    print("\n" + "=" * 78)
    print("STAGE 1: WALK-FORWARD OOS  (train 504d / test 126d, tiered Alpaca costs)")
    print("=" * 78)
    for arm, factory in builds.items():
        t0 = time.time()
        wf = walk_forward(prices, strategy_factory=factory, param_grid=param_grid,
                          train_days=504, test_days=126, cost_model=ALPACA, warmup_days=280)
        wf_out[arm] = {"metrics": wf.metrics, "param_stability": wf.param_stability,
                       "segments": len(wf.segments)}
        print(f"  {arm:6s} segs={len(wf.segments):2d}  stability={wf.param_stability:.2f}  "
              f"{fmt(wf.metrics)}   [{time.time() - t0:.0f}s]")

    # ── Stage 2: final 252d holdout (single window — fragile, for context) ────
    print("\n" + "=" * 78)
    print("STAGE 2: FINAL HOLDOUT (last 252d, lookback=126/top_n=10)")
    print("=" * 78)
    for arm, factory in builds.items():
        w = factory(lookback=HOLDOUT_LOOKBACK, top_n=HOLDOUT_TOP_N)(prices)
        m_full = run_backtest(w, prices, ALPACA, start=str(warmup.date())).metrics
        m_hold = run_backtest(w, prices, ALPACA, start=str(holdout_start.date())).metrics
        hold_out[arm] = {"full": m_full, "holdout": m_hold}
        print(f"  {arm:6s} FULL    {fmt(m_full)}")
        print(f"  {arm:6s} HOLDOUT {fmt(m_hold)}")
    if spy_w is not None:
        m_spy = run_backtest(spy_w, prices, ZERO_COSTS, start=str(holdout_start.date())).metrics
        hold_out["SPY"] = {"holdout": m_spy}
        print(f"  {'SPY':6s} HOLDOUT {fmt(m_spy)}")

    # ── Verdict ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("VERDICT (walk-forward is the number that matters)")
    print("=" * 78)
    gate = "walk-forward gate target: stitched Sharpe >= 1.0, MaxDD <= -15%"
    print(f"  {gate}")
    for arm in ("v2", "v2+VT", "v3"):
        m = wf_out[arm]["metrics"]
        sp = "PASS" if m["sharpe"] >= 1.0 else "FAIL"
        dd = "PASS" if m["max_drawdown"] >= -0.15 else "FAIL"
        print(f"  {arm:6s} WF Sharpe {m['sharpe']:.2f} [{sp}]  MaxDD {m['max_drawdown']:.2%} [{dd}]  "
              f"stability {wf_out[arm]['param_stability']:.2f}")

    out_path = Path(__file__).resolve().parent.parent / "docs" / "gate_v3_compare_output.json"
    out_path.write_text(json.dumps({"walk_forward": wf_out, "holdout": hold_out}, indent=2, default=str))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
