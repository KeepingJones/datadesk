"""
DataDesk entry point.

  python main.py backtest          run the core momentum+trend backtest, save to platform store
  python main.py serve             start the ops console on http://localhost:8000
  python main.py collect-trump     refresh the Trump communications corpus
  python main.py backfill T1 T2..  backfill daily history + fundamentals for tickers
  python main.py coverage          print history-store coverage
  python main.py enrich [T1 T2..] fetch/refresh fundamentals for all (or listed) tickers
  python main.py weekly-update     gap-fill prices + refresh fundamentals for whole universe
  python main.py tax-compare       side-by-side after-tax comparison (ISA vs Alpaca)
  python main.py universe          print platform availability breakdown per ticker
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


def cmd_backfill(tickers: list[str], source: str, skip_fundamentals: bool = False) -> None:
    if source == "massive":
        from datadesk.ingest.massive import backfill_massive

        written = backfill_massive(tickers)
    else:
        from datadesk.ingest.backfill import backfill_history

        written = backfill_history(tickers)

    for t, n in written.items():
        print(f"  {t:>10}  {n} bars")

    if not skip_fundamentals:
        from datadesk.ingest.fundamentals import fetch_fundamentals

        print("\nFetching fundamentals & static company data...")
        fetch_fundamentals(tickers)


def cmd_weekly_update() -> None:
    """Weekly maintenance: gap-fill all price history + refresh stale fundamentals.

    Run once a week (e.g. Saturday morning). Does:
      1. Smart price backfill for every ticker in the store (fills gaps since last bar)
      2. Refreshes fundamentals for tickers whose equity_ratios row is older than 7 days
    """
    import sqlite3
    from datetime import datetime, timedelta

    from datadesk.config import ALTDATA_DB
    from datadesk.history.store import coverage
    from datadesk.ingest.backfill import backfill_smart
    from datadesk.ingest.fundamentals import fetch_fundamentals

    cov = coverage()
    all_tickers = cov["ticker"].tolist()
    tradeable = [t for t in all_tickers if not t.startswith("^")]

    print(f"[1/2] Price gap-fill for {len(tradeable)} tradeable tickers...")
    written = backfill_smart(tradeable)
    new_bars = sum(written.values())
    print(f"      {new_bars} new bars written")

    print(f"\n[2/2] Fundamentals refresh (checking staleness)...")
    stale_cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat(timespec="seconds")
    try:
        con = sqlite3.connect(ALTDATA_DB)
        fresh = {
            r[0]
            for r in con.execute(
                "SELECT ticker FROM equity_ratios WHERE fetched_at > ? GROUP BY ticker",
                (stale_cutoff,),
            )
        }
        con.close()
    except Exception:
        fresh = set()

    stale = [t for t in tradeable if t not in fresh]
    print(f"      {len(stale)} tickers need refresh (not fetched in last 7 days)")
    if stale:
        fetch_fundamentals(stale, verbose=True)

    print(f"\nWeekly update complete. {len(tradeable)} tickers maintained.")


def cmd_enrich(tickers: list[str] | None = None) -> None:
    """Fetch/refresh fundamentals for all tickers in the history store (or a subset)."""
    from datadesk.history.store import coverage
    from datadesk.ingest.fundamentals import fetch_fundamentals

    if not tickers:
        cov = coverage()
        tickers = cov["ticker"].tolist()

    print(f"Enriching {len(tickers)} tickers with fundamentals...")
    print(f"  {'Ticker':<12} {'Name':<30} {'Mkt Cap':>8}  {'Sector':<22} {'Valuation'}")
    print("  " + "─" * 85)
    fetch_fundamentals(tickers)


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
    from datadesk.ingest.fundamentals import load_quality_excludes
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

    excluded = load_quality_excludes()
    eligible = set(prices.columns) - excluded
    print(f"Universe: {prices.shape[1]} tickers, {prices.shape[0]} days "
          f"| quality filter excluded {len(excluded)} micro-caps → {len(eligible)} eligible")

    w_mom = momentum(126, 10, 21, quality_universe=eligible)(prices)
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


def cmd_tax_compare() -> None:
    """Run strategy v2 on each account wrapper and compare after-tax CAGR/Sharpe.

    Three columns:
      Alpaca pre-tax  — gross minus transaction costs, zero CGT applied
      Alpaca post-tax — CGT at 24% applied annually above £3k exempt (higher-rate)
      T212 ISA        — 0.15% FX fee each way on US names, zero CGT
    """
    import pandas as pd

    from datadesk.backtest.costs import ALPACA_COSTS, T212_ISA_COSTS, ZERO_COSTS
    from datadesk.backtest.engine import run_backtest
    from datadesk.backtest.tax import UK_HIGHER_RATE, compare_tax_wrappers, print_tax_comparison
    from datadesk.history.store import coverage, load_closes
    from datadesk.ingest.fundamentals import load_quality_excludes
    from datadesk.strategies.momentum import momentum
    from datadesk.strategies.regime import bear_only_scale

    cov = coverage()
    tickers = cov[cov["rows"] > 2000]["ticker"].tolist()
    if not tickers:
        print("History store empty — run backfill first")
        return

    prices = load_closes(tickers=tickers)
    prices = prices[prices.index >= "2016-05-24"].ffill().dropna(axis=1)

    excluded = load_quality_excludes()
    eligible = set(prices.columns) - excluded
    print(f"Universe: {prices.shape[1]} tickers, {prices.shape[0]} days "
          f"| quality filter excluded {len(excluded)} micro-caps → {len(eligible)} eligible")

    w_mom = momentum(126, 10, 21, quality_universe=eligible)(prices)
    if "SPY" in prices.columns and "^VIX" in prices.columns:
        scale = bear_only_scale(prices["SPY"], prices["^VIX"])
        w_strat = w_mom.mul(scale, axis=0)
    else:
        w_strat = w_mom

    warmup = prices.index[min(150, len(prices) - 1)]
    holdout_start = prices.index[max(len(prices) - 252, 151)]

    for period_label, start in [
        ("FULL PERIOD", str(warmup.date())),
        ("HOLDOUT (last 252d)", str(holdout_start.date())),
    ]:
        comp = compare_tax_wrappers(
            target_weights=w_strat,
            prices=prices,
            tax_params=UK_HIGHER_RATE,
            alpaca_cost=ALPACA_COSTS,
            isa_cost=T212_ISA_COSTS,
            start=start,
        )
        print_tax_comparison(comp, label=f"momentum-core v2 — {period_label}")

        # SPY benchmark for context (zero cost, no CGT — it's the reference)
        if "SPY" in prices.columns:
            spy_w = pd.DataFrame({"SPY": [1.0]}, index=[prices.index[0]])
            spy_m = run_backtest(spy_w, prices, ZERO_COSTS, start=start).metrics
            print(
                f"  SPY benchmark (buy-hold, no tax):  "
                f"CAGR {spy_m['cagr']:+.3f}  Sharpe {spy_m['sharpe']:.2f}  "
                f"MaxDD {spy_m['max_drawdown']:.2f}\n"
            )

    print("NOTE: 24% CGT applied on annual net gains above £3,000 exempt amount.")
    print("NOTE: Universe is survivorship-biased — levels are indicative, not evidence.")


def cmd_universe() -> None:
    """Print platform availability breakdown for all tickers in the history store."""
    from datadesk.history.store import coverage
    from datadesk.universe.platform import classify, split_by_platform

    cov = coverage()
    tickers = cov["ticker"].tolist()
    buckets = split_by_platform(tickers)

    print(f"\nHistory store: {len(tickers)} tickers total")
    print(f"  ISA-only (UK .L)     : {len(buckets['isa_only'])} — {buckets['isa_only'][:10]}")
    print(f"  Both platforms       : {len(buckets['both'])} — {buckets['both'][:10]}")
    print(f"  Alpaca-only (US ETF) : {len(buckets['alpaca_only'])} — {buckets['alpaca_only']}")
    print(f"  Data-only (index ^)  : {len(buckets['unavailable'])} — {buckets['unavailable']}")
    print()
    print("  Ticker details:")
    print(f"  {'Ticker':<15} {'UK':>4} {'US stock':>9} {'US ETF':>7} {'Alpaca':>7} {'T212 ISA':>9}")
    print("  " + "-" * 55)
    for t in sorted(tickers):
        c = classify(t)
        def b(v): return "Y" if v else "-"
        print(
            f"  {t:<15} {b(c['is_uk']):>4} {b(c['is_us_stock']):>9} "
            f"{b(c['is_us_etf']):>7} {b(c['alpaca']):>7} {b(c['t212_isa']):>9}"
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
    p_bf.add_argument("--no-fundamentals", action="store_true", help="Skip fundamental data fetch")
    sub.add_parser("coverage")
    sub.add_parser("holdout")
    sub.add_parser("tax-compare")
    sub.add_parser("universe")
    p_enrich = sub.add_parser("enrich")
    p_enrich.add_argument("tickers", nargs="*", help="Tickers to enrich (default: all in store)")
    sub.add_parser("weekly-update")
    args = parser.parse_args()

    if args.command == "backtest":
        cmd_backtest()
    elif args.command == "serve":
        cmd_serve(args.port)
    elif args.command == "collect-trump":
        cmd_collect_trump()
    elif args.command == "backfill":
        cmd_backfill(args.tickers, args.source, skip_fundamentals=args.no_fundamentals)
    elif args.command == "enrich":
        cmd_enrich(args.tickers or None)
    elif args.command == "weekly-update":
        cmd_weekly_update()
    elif args.command == "coverage":
        cmd_coverage()
    elif args.command == "holdout":
        cmd_holdout()
    elif args.command == "tax-compare":
        cmd_tax_compare()
    elif args.command == "universe":
        cmd_universe()
