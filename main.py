"""
DataDesk entry point.

  python main.py backtest          run the core momentum+trend backtest, save to platform store
  python main.py serve             start the ops console on http://localhost:8000
  python main.py collect-trump     refresh the Trump communications corpus
  python main.py backfill T1 T2..  backfill daily history for tickers (yfinance)
  python main.py coverage          print history-store coverage
"""

import argparse
import logging
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("error_log.txt", encoding="utf-8"),
    ],
)
logger = logging.getLogger("datadesk")


def cmd_backtest() -> None:
    from datadesk.backtest.costs import CostModel
    from datadesk.backtest.engine import run_backtest
    from datadesk.db import save_backtest_run
    from datadesk.history.store import coverage, load_closes
    from datadesk.strategies.momentum import momentum
    from datadesk.strategies.trend import apply_trend_filter

    cov = coverage()
    tickers = cov[cov["rows"] > 800]["ticker"].tolist()
    if not tickers:
        print(
            "History store is empty — run: python -m datadesk.history.migrate "
            "or python main.py backfill <tickers>"
        )
        return

    prices = load_closes(tickers=tickers)
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.9)).ffill(limit=5)

    params = {"lookback": 126, "top_n": 10, "skip": 21, "trend_window": 200, "trend_band": 0.02}
    weights = momentum(params["lookback"], params["top_n"], params["skip"])(prices)
    if "SPY" in prices.columns:
        weights = apply_trend_filter(
            weights, prices["SPY"], params["trend_window"], params["trend_band"]
        )

    warmup = prices.index[min(params["lookback"] + params["skip"] + 5, len(prices) - 1)]
    result = run_backtest(weights, prices, CostModel(default_tier="L1"), start=str(warmup.date()))

    save_backtest_run("momentum+trend (core)", params, result.metrics, result.equity)
    print(f"Universe: {prices.shape[1]} tickers, {prices.shape[0]} days")
    print(f"Metrics:  {result.metrics}")
    print("Saved to platform store — view at http://localhost:8000 (python main.py serve)")


def cmd_serve(port: int) -> None:
    import uvicorn

    from datadesk.api.app import app

    logger.info(f"DataDesk ops console: http://localhost:{port}")
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    except OSError:
        # never taskkill whatever holds the port — it may not be ours
        logger.error(f"Port {port} is in use. Stop the other process or use --port.")


def cmd_collect_trump() -> None:
    from datadesk.ingest.trump import collect

    print(f"New posts stored: {collect()}")


def cmd_backfill(tickers: list[str], source: str) -> None:
    if source == "massive":
        from datadesk.ingest.massive import backfill_massive

        written = backfill_massive(tickers)
    else:
        from datadesk.ingest.backfill import backfill_history

        written = backfill_history(tickers)

    for t, n in written.items():
        print(f"  {t:>10}  {n} bars")


def cmd_coverage() -> None:
    from datadesk.history.store import coverage

    print(coverage().to_string(index=False))


def cmd_holdout() -> None:
    """Improved strategy v2 (test-and-improvement-2026-06-12): momentum-core with a
    BEAR-ONLY overlay, always reported against the SPY benchmark on identical windows."""
    import pandas as pd

    from datadesk.backtest.costs import ALPACA_COSTS, T212_ISA_COSTS, ZERO_COSTS
    from datadesk.backtest.engine import run_backtest
    from datadesk.db import save_backtest_run
    from datadesk.history.store import coverage, load_closes
    from datadesk.strategies.momentum import momentum
    from datadesk.strategies.regime import bear_only_scale

    cov = coverage()
    # require near-complete history so the cross-section is comparable across dates
    tickers = cov[cov["rows"] > 2000]["ticker"].tolist()
    if not tickers:
        print("History store is empty (need tickers with >2000 bars — run backfill)")
        return

    prices = load_closes(tickers=tickers)
    prices = prices[prices.index >= "2016-05-24"].ffill().dropna(axis=1)
    print(f"Universe: {prices.shape[1]} tickers, {prices.shape[0]} days")

    w_mom = momentum(126, 10, 21)(prices)
    if "SPY" in prices.columns and "^VIX" in prices.columns:
        scale = bear_only_scale(prices["SPY"], prices["^VIX"])
        w_strat = w_mom.mul(scale, axis=0)
    else:
        w_strat = w_mom

    warmup = prices.index[min(150, len(prices) - 1)]
    holdout_start = prices.index[max(len(prices) - 252, 151)]
    spy_w = pd.DataFrame({"SPY": [1.0]}, index=[prices.index[0]]) if "SPY" in prices else None

    def line(tag, w, costs, start):
        m = run_backtest(w, prices, costs, start=start).metrics
        print(
            f"  {tag:30s} CAGR {m['cagr']:+.3f}  Sharpe {m['sharpe']:.2f}  "
            f"MaxDD {m['max_drawdown']:.2f}  turn {m.get('avg_annual_turnover', 0):.1f}"
        )
        return m

    for label, costs in [("ALPACA 0bps", ALPACA_COSTS), ("T212 15bps FX", T212_ISA_COSTS)]:
        print(f"\n=== {label} ===")
        print(" FULL PERIOD:")
        m_full = line("momentum-core + bear overlay", w_strat, costs, str(warmup.date()))
        if spy_w is not None:
            line("SPY benchmark", spy_w, ZERO_COSTS, str(warmup.date()))
        print(" HOLDOUT (last 252d):")
        m_hold = line("momentum-core + bear overlay", w_strat, costs, str(holdout_start.date()))
        if spy_w is not None:
            line("SPY benchmark", spy_w, ZERO_COSTS, str(holdout_start.date()))
        if label.startswith("ALPACA"):
            save_backtest_run(
                "v2 momentum-core (full, Alpaca)",
                {},
                m_full,
                run_backtest(w_strat, prices, costs, start=str(warmup.date())).equity,
            )
            save_backtest_run(
                "v2 momentum-core HOLDOUT 252d (Alpaca)",
                {},
                m_hold,
                run_backtest(w_strat, prices, costs, start=str(holdout_start.date())).equity,
            )

    print("\nSaved to platform store.")
    print("GATE 1: beat SPY on Sharpe AND max-drawdown in the holdout — not an absolute CAGR.")
    print(
        "NOTE: universe still survivorship-biased until Tiingo backfill — levels not yet evidence."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DataDesk — market data platform (paper only)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backtest")
    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("collect-trump")
    p_bf = sub.add_parser("backfill")
    p_bf.add_argument(
        "--source", choices=["yahoo", "massive"], default="yahoo", help="Data source to use"
    )
    p_bf.add_argument("tickers", nargs="+")
    sub.add_parser("coverage")
    sub.add_parser("holdout")
    args = parser.parse_args()

    if args.command == "backtest":
        cmd_backtest()
    elif args.command == "serve":
        cmd_serve(args.port)
    elif args.command == "collect-trump":
        cmd_collect_trump()
    elif args.command == "backfill":
        cmd_backfill(args.tickers, args.source)
    elif args.command == "coverage":
        cmd_coverage()
    elif args.command == "holdout":
        cmd_holdout()
