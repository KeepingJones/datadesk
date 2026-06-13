"""
Monte Carlo simulation on real strategy returns.

Bootstraps the actual daily returns from a saved backtest run to produce
percentile confidence bands for equity curves, CAGR, Sharpe, and Max DD.
This tells you: "if market regimes were randomly re-ordered, what range of
outcomes is realistic?"

Outputs a JSON summary (percentile bands + metric distributions) suitable
for rendering a fan chart on the dashboard.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from datadesk.backtest.metrics import cagr as _cagr, sharpe as _sharpe, max_drawdown as _mdd


def _equity_curve(returns: np.ndarray) -> np.ndarray:
    return np.cumprod(1 + returns)


def run_simulation(
    runs: int = 1000,
    model: str = "bootstrap",
    status_callback=None,
    returns_series: pd.Series | None = None,
) -> str:
    """
    Bootstrap or GBM simulation on real strategy returns.

    Args:
        runs: number of simulated paths
        model: 'bootstrap' (resample daily returns) or 'gbm' (fit mu/sigma)
        status_callback: optional fn(progress_int) called each iteration
        returns_series: daily returns series (pd.Series). If None, loads the
                        latest saved backtest run from platform.db.

    Returns path to a JSON file with results.
    """
    if returns_series is None:
        returns_series = _load_latest_returns()

    if returns_series is None or len(returns_series) < 20:
        raise ValueError("No backtest returns available — run a backtest first")

    r = returns_series.dropna().values
    n = len(r)
    mu = float(np.mean(r))
    sigma = float(np.std(r))

    # Percentile bands sampled at equal intervals across the path length
    sample_points = min(252, n)
    step = max(1, n // sample_points)
    indices = list(range(0, n, step))
    if indices[-1] != n - 1:
        indices.append(n - 1)

    all_paths: list[np.ndarray] = []
    final_cagrs: list[float] = []
    final_sharpes: list[float] = []
    final_mdds: list[float] = []

    for i in range(runs):
        if model == "bootstrap":
            sim_r = np.random.choice(r, size=n, replace=True)
        else:  # gbm
            dt = 1 / 252
            shocks = np.random.normal(
                loc=(mu - 0.5 * sigma ** 2) * dt,
                scale=sigma * np.sqrt(dt),
                size=n,
            )
            sim_r = shocks

        curve = _equity_curve(sim_r)
        all_paths.append(curve[indices])

        s = pd.Series(sim_r)
        final_cagrs.append(_cagr(s))
        final_sharpes.append(_sharpe(s))
        final_mdds.append(_mdd(s))

        if status_callback:
            status_callback(i + 1)

    paths_array = np.array(all_paths)  # shape: (runs, len(indices))

    percentiles = [5, 25, 50, 75, 95]
    bands = {
        f"p{p}": paths_array[
            np.argsort(paths_array[:, -1])[int(runs * p / 100)]
        ].tolist()
        for p in percentiles
    }

    # Metric distributions
    cagrs_arr = np.array(final_cagrs)
    sharpes_arr = np.array(final_sharpes)
    mdds_arr = np.array(final_mdds)

    result = {
        "runs": runs,
        "model": model,
        "n_days": n,
        "actual_cagr": round(_cagr(returns_series), 4),
        "actual_sharpe": round(_sharpe(returns_series), 3),
        "actual_mdd": round(_mdd(returns_series), 4),
        "path_indices": indices,
        "bands": bands,
        "cagr_dist": {
            "p5": round(float(np.percentile(cagrs_arr, 5)), 4),
            "p25": round(float(np.percentile(cagrs_arr, 25)), 4),
            "p50": round(float(np.percentile(cagrs_arr, 50)), 4),
            "p75": round(float(np.percentile(cagrs_arr, 75)), 4),
            "p95": round(float(np.percentile(cagrs_arr, 95)), 4),
        },
        "sharpe_dist": {
            "p5": round(float(np.percentile(sharpes_arr, 5)), 3),
            "p25": round(float(np.percentile(sharpes_arr, 25)), 3),
            "p50": round(float(np.percentile(sharpes_arr, 50)), 3),
            "p75": round(float(np.percentile(sharpes_arr, 75)), 3),
            "p95": round(float(np.percentile(sharpes_arr, 95)), 3),
        },
        "mdd_dist": {
            "p5": round(float(np.percentile(mdds_arr, 5)), 4),
            "p25": round(float(np.percentile(mdds_arr, 25)), 4),
            "p50": round(float(np.percentile(mdds_arr, 50)), 4),
            "p75": round(float(np.percentile(mdds_arr, 75)), 4),
            "p95": round(float(np.percentile(mdds_arr, 95)), 4),
        },
    }

    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = str(results_dir / f"mc_{model}_{runs}_{ts}.json")
    with open(path, "w") as f:
        json.dump(result, f)
    return path


def _load_latest_returns() -> pd.Series | None:
    """Load the equity curve from the most recent backtest run and derive daily returns."""
    try:
        import sqlite3
        from datadesk.config import DB_PATH

        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT equity_curve FROM backtest_runs ORDER BY run_at DESC LIMIT 1"
        ).fetchone()
        con.close()
        if not rows:
            return None
        equity = pd.Series(json.loads(rows[0]))
        if len(equity) < 2:
            return None
        return equity.pct_change().dropna()
    except Exception:
        return None
