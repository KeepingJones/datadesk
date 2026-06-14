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

from datadesk.backtest.costs import ALPACA_COSTS
from datadesk.backtest.engine import run_backtest
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
    start: str,
    holdout_start: str,
) -> None:
    """Run full-period and holdout backtests and save both."""
    # Full period
    res = run_backtest(weights, prices, ALPACA_COSTS, start=start)
    save_backtest_run(f"{label}", params, res.metrics, res.equity)

    # Holdout (OOS) — last 252 trading days
    res_ho = run_backtest(weights, prices, ALPACA_COSTS, start=holdout_start)
    cagr_pct = res_ho.metrics.get("cagr", 0) * 100
    logger.info(f"  HOLDOUT CAGR: {cagr_pct:.1f}%")
    save_backtest_run(f"{label} HOLDOUT 252d", params, res_ho.metrics, res_ho.equity)


def run_sweep() -> None:
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

        # Holdout: last 252 trading days as OOS test window
        holdout_idx = max(warmup_idx + 1, n_days - 252)
        holdout_start = str(prices.index[holdout_idx].date())

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
                                _run_combo(label, params, w, prices, warmup_start, holdout_start)
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
                        _run_combo(label, params, w, prices, warmup_start, holdout_start)
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
                    _run_combo(label, params, w, prices, warmup_start, holdout_start)
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
                           w_t, prices, warmup_start, holdout_start)
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
                        _run_combo(label, params, w, prices, warmup_start, holdout_start)
                        combo_count += 1
                    except Exception as e:
                        logger.warning(f"  FAILED {label}: {e}")

        total_runs += combo_count
        logger.info(f"  {univ_name}: {combo_count} combos saved ({combo_count * 2} DB rows)")

    logger.info(f"\nSweep complete — {total_runs} unique strategy combos across {len(UNIVERSES)} universes")
    logger.info(f"Approximately {total_runs * 2} rows in platform.db (each has full-period + holdout)")


if __name__ == "__main__":
    run_sweep()
