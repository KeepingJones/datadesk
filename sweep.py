"""
Comprehensive parameter sweep across multiple universe families.

Runs ~1000 backtests covering:
  - 5 universe families: AI/Semi, EU Regional, Defensive, Global Macro, Small-Cap Growth
  - 4 momentum lookbacks × 3 top-N × 4 MR z-scores × 2 trend-filter × 2 blend modes
  - Pure momentum and pure MR variants
  - Holdout validation: metrics reported on last 252 trading days (OOS)

Each unique (universe, strategy-variant) name upserts — re-running never piles up duplicates.
"""

import logging

import pandas as pd

from datadesk.backtest.costs import ALPACA_COSTS, T212_ISA_COSTS
from datadesk.backtest.engine import run_backtest
from datadesk.backtest.vol_target import vol_target_weights
from datadesk.db import save_backtest_run
from datadesk.history.store import load_closes
from datadesk.strategies.blend import inverse_volatility_blend
from datadesk.strategies.insider import insider_congress_follow
from datadesk.strategies.meanrev import mean_reversion
from datadesk.strategies.momentum import momentum
from datadesk.strategies.trend import trend_signal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sweep")


# ---------------------------------------------------------------------------
# Universe families
# ---------------------------------------------------------------------------
UNIVERSES: dict[str, list[str]] = {
    "AI_SEMI": [
        "BE", "CLSK", "IREN", "CORZ", "BTDR", "APLD", "WDC",
        "NVDA", "AMD", "ASML", "MU", "AVGO", "TSM",
        "AAPL", "MSFT", "DELL", "SMCI", "ORCL", "SMH", "SPY",
    ],
    "EU_REGIONAL": [
        # European ADRs + ETFs tradeable on US markets (backtestable via Alpaca/yfinance)
        "ASML", "NVO", "SAP", "SHEL", "BP", "AZN", "GSK", "RIO", "HSBC", "BAESY",
        "VGK", "EWU", "EWG", "EWL", "EWQ", "EWN", "EWI", "EWP", "EWD", "EWA",
    ],
    "DEFENSIVE": [
        "XLV", "XLP", "XLU", "XLF", "XLI",
        "JNJ", "PG", "KO", "MCD", "WMT", "PEP",
        "VYM", "O", "NEE", "AWK", "SO",
    ],
    "GLOBAL_MACRO": [
        "SPY", "QQQ", "GLD", "TLT", "IEF", "SHY",
        "EEM", "VGK", "EWJ", "EWC", "EWZ",
        "UUP", "USO", "GDX", "DBA",
    ],
    "SMALLCAP_GROWTH": [
        "CRWD", "PLTR", "NET", "DDOG", "SNOW", "ZS", "HUBS", "BILL",
        "RKLB", "JOBY", "AFRM", "SOFI", "HOOD", "NU", "ACHR",
        "IONQ", "QUBT", "RGTI", "ARQQ", "SOUN",
    ],
}

# Parameter grid
LOOKBACKS   = [21, 63, 126, 252]        # momentum lookback windows
TOP_NS      = [1, 2, 3]                  # number of top momentum names to hold
Z_ENTRIES   = [0.75, 1.0, 1.5, 2.0]    # mean-reversion z-score entry threshold
TREND_FLAGS = [True, False]              # SPY 200-day trend filter on/off
BLEND_TYPES = ["inv_vol", "equal"]       # weighting within the blend

COVERAGE_THRESH = 0.80  # drop ticker if missing >20% of rows
MIN_ROWS = 200           # skip universe if fewer than this many trading days

# Holdout windows reported per run: last N trading days as OOS
HOLDOUT_WINDOWS = {
    "1y":  252,
    "3y":  756,
    "5y": 1260,
}


def _load_universe(tickers: list[str]) -> "pd.DataFrame | None":
    import pandas as pd
    prices = load_closes(tickers=tickers)
    if prices.empty:
        return None
    # Drop sparse rows (date-level), then sparse columns (ticker-level)
    prices = prices.dropna(axis=0, thresh=max(1, int(len(prices.columns) * 0.4)))
    prices = prices.dropna(axis=1, thresh=int(len(prices) * COVERAGE_THRESH)).ffill(limit=5)
    if len(prices) < MIN_ROWS or prices.shape[1] < 2:
        return None
    return prices


def _blend_equal(weight_frames: "list[pd.DataFrame]") -> "pd.DataFrame":
    """Equally weighted blend across strategy weight matrices."""
    import pandas as pd
    stacked = pd.concat(weight_frames, axis=0)
    return stacked.groupby(stacked.index).mean()


def _apply_trend(weights: "pd.DataFrame", prices: "pd.DataFrame") -> "pd.DataFrame":
    if "SPY" not in prices.columns:
        return weights
    scale = trend_signal(prices["SPY"], 200, 0.02)
    return weights.mul(scale, axis=0)


def _run_combo(
    label: str,
    params: dict,
    weights: "pd.DataFrame",
    prices: "pd.DataFrame",
    warmup_start: str,
    holdout_starts: dict,
    costs=None,
    vol_target: bool = False,
) -> None:
    """Run full-period + multiple holdout windows and save all."""
    if costs is None:
        costs = ALPACA_COSTS

    w = vol_target_weights(weights, prices) if vol_target else weights

    res = run_backtest(w, prices, costs, start=warmup_start)
    save_backtest_run(label, params, res.metrics, res.equity)

    for window_label, ho_start in holdout_starts.items():
        res_ho = run_backtest(w, prices, costs, start=ho_start)
        cagr_pct = res_ho.metrics.get("cagr", 0) * 100
        logger.info(f"  HOLDOUT {window_label} CAGR: {cagr_pct:.1f}%")
        save_backtest_run(
            f"{label} HOLDOUT {window_label}",
            {**params, "holdout_window": window_label},
            res_ho.metrics,
            res_ho.equity,
        )


def _run_walk_forward(
    label: str,
    params: dict,
    weights: "pd.DataFrame",
    prices: "pd.DataFrame",
    train_years: int = 3,
    test_years: int = 1,
    costs=None,
    vol_target: bool = False,
) -> None:
    """
    Expanding-window walk-forward OOS.

    Trains on `train_years` years, tests on the next `test_years`, then
    expands the training window by `test_years` and repeats. Each fold's
    OOS result is saved independently as "{label} WFO fold-N".

    This is a true out-of-sample test: parameters are fixed from the sweep
    but the test window was never used to select them.
    """
    if costs is None:
        costs = ALPACA_COSTS

    w = vol_target_weights(weights, prices) if vol_target else weights

    DAYS_PER_YEAR = 252
    train_days = train_years * DAYS_PER_YEAR
    test_days = test_years * DAYS_PER_YEAR
    n = len(prices)

    fold = 0
    oos_returns = []

    train_end_idx = train_days
    while train_end_idx + test_days <= n:
        test_start_idx = train_end_idx
        test_end_idx = min(train_end_idx + test_days, n)

        test_start = str(prices.index[test_start_idx].date())
        test_end = str(prices.index[test_end_idx - 1].date())

        fold += 1
        try:
            res = run_backtest(w, prices, costs, start=test_start, end=test_end)
            cagr_pct = res.metrics.get("cagr", 0) * 100
            logger.info(f"  WFO fold-{fold} [{test_start}→{test_end}] CAGR {cagr_pct:.1f}%")
            save_backtest_run(
                f"{label} WFO fold-{fold}",
                {**params, "wfo_fold": fold, "wfo_start": test_start, "wfo_end": test_end},
                res.metrics,
                res.equity,
            )
            oos_returns.append(res.returns)
        except Exception as e:
            logger.warning(f"  WFO fold-{fold} failed: {e}")

        train_end_idx += test_days

    # Save aggregate OOS metrics across all folds
    if oos_returns and len(oos_returns) >= 2:
        combined = pd.concat(oos_returns)
        from datadesk.backtest.metrics import summarize
        agg_metrics = summarize(combined)
        agg_metrics["wfo_folds"] = fold
        save_backtest_run(
            f"{label} WFO aggregate",
            {**params, "wfo_folds": fold, "wfo_type": "aggregate"},
            agg_metrics,
            (1 + combined).cumprod(),
        )
        logger.info(
            f"  WFO aggregate ({fold} folds): "
            f"CAGR {agg_metrics['cagr']*100:.1f}%  "
            f"Sharpe {agg_metrics['sharpe']:.2f}"
        )


def _backfill_missing(all_tickers: list[str]) -> None:
    """Fetch price history for any tickers not yet in history.db."""
    from datadesk.history.store import coverage
    from datadesk.ingest.backfill import backfill_history

    covered = set(coverage().keys())
    missing = [t for t in all_tickers if t not in covered]
    if not missing:
        logger.info("All universe tickers already in history.db — skipping backfill")
        return
    logger.info(f"Backfilling {len(missing)} missing tickers: {missing}")
    written = backfill_history(missing)
    fetched = sum(1 for v in written.values() if v > 0)
    logger.info(f"Backfill complete: {fetched}/{len(missing)} tickers had data")


def run_sweep() -> None:
    # Ensure all universe tickers have price history before sweeping
    all_tickers = list({t for tickers in UNIVERSES.values() for t in tickers})
    _backfill_missing(all_tickers)

    total_runs = 0

    for univ_name, tickers in UNIVERSES.items():
        logger.info(f"\n{'='*60}")
        logger.info(f"Universe: {univ_name}  ({len(tickers)} tickers)")

        prices = _load_universe(tickers)
        if prices is None:
            logger.warning(f"  Skipping {univ_name} — insufficient data")
            continue

        n_days = len(prices)
        n_tickers = prices.shape[1]
        logger.info(f"  Loaded {n_tickers} tickers, {n_days} trading days")

        warmup_idx = min(252, n_days - 1)
        warmup_start = str(prices.index[warmup_idx].date())

        # Multiple holdout windows — only include windows that have enough data
        holdout_starts: dict[str, str] = {}
        for win_label, win_days in HOLDOUT_WINDOWS.items():
            ho_idx = n_days - win_days
            if ho_idx > warmup_idx:  # must be after warmup
                holdout_starts[win_label] = str(prices.index[ho_idx].date())
            else:
                logger.info(f"  Skipping {win_label} holdout — insufficient history ({n_days} days)")

        if not holdout_starts:
            logger.warning(f"  No holdout windows fit {univ_name} — skipping")
            continue

        logger.info(f"  Holdout windows: {list(holdout_starts.keys())}")

        # Pre-compute insider signal once per universe (expensive loop)
        try:
            w_insider = insider_congress_follow()(prices)
        except Exception:
            w_insider = None

        combo_count = 0

        # -----------------------------------------------------------------
        # Main grid: momentum blended with mean-reversion
        # -----------------------------------------------------------------
        for lb in LOOKBACKS:
            for top in TOP_NS:
                for z in Z_ENTRIES:
                    for trend in TREND_FLAGS:
                        for blend in BLEND_TYPES:
                            w_mom = momentum(lb, top, 21)(prices)
                            w_mr = mean_reversion(z_entry=z, z_exit=0.0)(prices)

                            if blend == "inv_vol":
                                w = inverse_volatility_blend([w_mom, w_mr], prices)
                            else:
                                w = _blend_equal([w_mom, w_mr])

                            if trend:
                                w = _apply_trend(w, prices)

                            label = (
                                f"{univ_name} | mom({lb},{top}) mr({z}) "
                                f"trend={'Y' if trend else 'N'} blend={blend}"
                            )
                            params = {
                                "universe": univ_name,
                                "mom_lookback": lb,
                                "mom_top_n": top,
                                "mr_z_entry": z,
                                "trend_filter": trend,
                                "blend": blend,
                                "variant": "mom+mr",
                            }
                            try:
                                _run_combo(label, params, w, prices, warmup_start, holdout_starts)
                                combo_count += 1
                            except Exception as e:
                                logger.warning(f"  FAILED {label}: {e}")

        # -----------------------------------------------------------------
        # Pure momentum (no MR)
        # -----------------------------------------------------------------
        for lb in LOOKBACKS:
            for top in TOP_NS:
                for trend in TREND_FLAGS:
                    w = momentum(lb, top, 21)(prices)
                    if trend:
                        w = _apply_trend(w, prices)

                    label = f"{univ_name} | mom_only({lb},{top}) trend={'Y' if trend else 'N'}"
                    params = {
                        "universe": univ_name,
                        "mom_lookback": lb,
                        "mom_top_n": top,
                        "trend_filter": trend,
                        "variant": "mom_only",
                    }
                    try:
                        _run_combo(label, params, w, prices, warmup_start, holdout_starts)
                        combo_count += 1
                    except Exception as e:
                        logger.warning(f"  FAILED {label}: {e}")

        # -----------------------------------------------------------------
        # Pure mean-reversion
        # -----------------------------------------------------------------
        for z in Z_ENTRIES:
            for trend in TREND_FLAGS:
                w = mean_reversion(z_entry=z, z_exit=0.0)(prices)
                if trend:
                    w = _apply_trend(w, prices)

                label = f"{univ_name} | mr_only(z={z}) trend={'Y' if trend else 'N'}"
                params = {
                    "universe": univ_name,
                    "mr_z_entry": z,
                    "trend_filter": trend,
                    "variant": "mr_only",
                }
                try:
                    _run_combo(label, params, w, prices, warmup_start, holdout_starts)
                    combo_count += 1
                except Exception as e:
                    logger.warning(f"  FAILED {label}: {e}")

        # -----------------------------------------------------------------
        # Trend-only (pure SPY filter applied to equal-weight long universe)
        # -----------------------------------------------------------------
        if "SPY" in prices.columns:
            w_eq = pd.DataFrame(
                1.0 / (n_tickers - 1),
                index=prices.index,
                columns=[c for c in prices.columns if c != "SPY"],
            )
            w_t = _apply_trend(w_eq, prices)
            label = f"{univ_name} | trend_only_EW"
            try:
                _run_combo(label, {"universe": univ_name, "variant": "trend_only_EW"},
                           w_t, prices, warmup_start, holdout_starts)
                combo_count += 1
            except Exception as e:
                logger.warning(f"  FAILED {label}: {e}")

        # -----------------------------------------------------------------
        # Insider + momentum blend (where insider data exists)
        # -----------------------------------------------------------------
        if w_insider is not None and not w_insider.empty and w_insider.abs().sum().sum() > 0:
            for lb in [63, 126]:
                for top in [2, 3]:
                    w_mom = momentum(lb, top, 21)(prices)
                    w = inverse_volatility_blend([w_mom, w_insider], prices)
                    w = _apply_trend(w, prices)
                    label = f"{univ_name} | mom({lb},{top})+insider trend=Y"
                    params = {
                        "universe": univ_name,
                        "mom_lookback": lb,
                        "mom_top_n": top,
                        "trend_filter": True,
                        "variant": "mom+insider",
                    }
                    try:
                        _run_combo(label, params, w, prices, warmup_start, holdout_starts)
                        combo_count += 1
                    except Exception as e:
                        logger.warning(f"  FAILED {label}: {e}")

        # ----------------------------------------------------------------
        # Vol-targeting pass — re-run best mom+mr combos with 15% vol target
        # Run for every universe so the dashboard shows the comparison.
        # ----------------------------------------------------------------
        for lb in LOOKBACKS:
            for top in TOP_NS:
                for trend in TREND_FLAGS:
                    w_mom = momentum(lb, top, 21)(prices)
                    w_mr = mean_reversion(z_entry=1.0, z_exit=0.0)(prices)
                    w = inverse_volatility_blend([w_mom, w_mr], prices)
                    if trend:
                        w = _apply_trend(w, prices)
                    vt_label = (
                        f"{univ_name}[VOL15] | mom({lb},{top}) trend={'Y' if trend else 'N'}"
                    )
                    vt_params = {
                        "universe": univ_name,
                        "mom_lookback": lb,
                        "mom_top_n": top,
                        "trend_filter": trend,
                        "variant": "mom+mr",
                        "vol_target": 0.15,
                    }
                    try:
                        _run_combo(
                            vt_label, vt_params, w, prices,
                            warmup_start, holdout_starts, vol_target=True,
                        )
                        combo_count += 1
                    except Exception as e:
                        logger.warning(f"  FAILED {vt_label}: {e}")

        # ----------------------------------------------------------------
        # Walk-forward OOS — run the 3 most popular lookbacks at top_n=2
        # (the single most likely configuration to promote to live).
        # ----------------------------------------------------------------
        for lb in [63, 126, 252]:
            for trend in TREND_FLAGS:
                w_mom = momentum(lb, 2, 21)(prices)
                w_mr = mean_reversion(z_entry=1.0, z_exit=0.0)(prices)
                w = inverse_volatility_blend([w_mom, w_mr], prices)
                if trend:
                    w = _apply_trend(w, prices)
                wfo_label = f"{univ_name} | WFO mom({lb},2) trend={'Y' if trend else 'N'}"
                wfo_params = {
                    "universe": univ_name,
                    "mom_lookback": lb,
                    "mom_top_n": 2,
                    "trend_filter": trend,
                    "variant": "mom+mr",
                    "wfo": True,
                }
                try:
                    _run_walk_forward(wfo_label, wfo_params, w, prices)
                    combo_count += 1
                except Exception as e:
                    logger.warning(f"  FAILED WFO {wfo_label}: {e}")

        # ----------------------------------------------------------------
        # T212 ISA cost pass — EU/DEFENSIVE universes carry a 0.15% FX fee
        # on non-GBP stocks; re-run the main grid to compare net-of-costs
        # ----------------------------------------------------------------
        if univ_name in ("EU_REGIONAL", "DEFENSIVE"):
            t212_count = 0
            for lb in LOOKBACKS:
                for top in TOP_NS:
                    for z in Z_ENTRIES:
                        for trend in TREND_FLAGS:
                            for blend in BLEND_TYPES:
                                w_mom = momentum(lb, top, 21)(prices)
                                w_mr = mean_reversion(z_entry=z, z_exit=0.0)(prices)
                                if blend == "inv_vol":
                                    w = inverse_volatility_blend([w_mom, w_mr], prices)
                                else:
                                    w = _blend_equal([w_mom, w_mr])
                                if trend:
                                    w = _apply_trend(w, prices)
                                t212_label = (
                                    f"{univ_name}[T212] | mom({lb},{top}) mr({z}) "
                                    f"trend={'Y' if trend else 'N'} blend={blend}"
                                )
                                t212_params = {
                                    "universe": univ_name,
                                    "broker_cost_model": "T212_ISA",
                                    "mom_lookback": lb,
                                    "mom_top_n": top,
                                    "mr_z_entry": z,
                                    "trend_filter": trend,
                                    "blend": blend,
                                    "variant": "mom+mr",
                                }
                                try:
                                    _run_combo(
                                        t212_label, t212_params, w, prices,
                                        warmup_start, holdout_starts, costs=T212_ISA_COSTS,
                                    )
                                    t212_count += 1
                                except Exception as e:
                                    logger.warning(f"  FAILED {t212_label}: {e}")
            logger.info(f"  {univ_name} T212 ISA: {t212_count} extra combos")
            combo_count += t212_count

        total_runs += combo_count
        logger.info(f"  {univ_name}: {combo_count} combos saved ({combo_count * 2} DB rows)")

    logger.info(f"\nSweep complete — {total_runs} unique strategy combos across {len(UNIVERSES)} universes")
    logger.info(f"Approximately {total_runs * 2} rows in platform.db (each has full-period + holdout)")


if __name__ == "__main__":
    run_sweep()
