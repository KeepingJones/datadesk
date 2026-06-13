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

    from datadesk.backtest.costs import ALPACA_COSTS, T212_ISA_COSTS, ZERO_COSTS, CostModel
    from datadesk.backtest.engine import run_backtest
    from datadesk.backtest.tiers import build_cost_tiers
    from datadesk.db import save_backtest_run
    from datadesk.history.store import coverage, load_closes
    from datadesk.ingest.fundamentals import load_quality_excludes
    from datadesk.ingest.index_membership import index_overlap_report
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

    overlap = index_overlap_report(list(eligible))
    if overlap:
        overlap_str = "  ".join(f"{k}: {v}%" for k, v in sorted(overlap.items()))
        print(f"Index overlap (of eligible): {overlap_str}")

    # Realistic costs: tier by exchange + market cap
    ticker_tiers = build_cost_tiers()
    ALPACA_TIERED = CostModel(tier_by_ticker=ticker_tiers, commission_bps=0.0, fx_fee_bps=0.0)
    T212_TIERED   = CostModel(tier_by_ticker=ticker_tiers, commission_bps=0.0, fx_fee_bps=15.0)

    w_eq   = momentum(126, 10, 21, quality_universe=eligible)(prices)
    w_vol  = momentum(126, 10, 21, quality_universe=eligible, vol_weight=True)(prices)

    def apply_bear(w):
        if "SPY" in prices.columns and "^VIX" in prices.columns:
            scale = bear_only_scale(prices["SPY"], prices["^VIX"])
            return w.mul(scale, axis=0)
        return w

    w_strat     = apply_bear(w_eq)
    w_strat_vol = apply_bear(w_vol)

    warmup = prices.index[min(150, len(prices) - 1)]
    holdout_start = prices.index[max(len(prices) - 252, 151)]
    spy_w = pd.DataFrame({"SPY": [1.0]}, index=[prices.index[0]]) if "SPY" in prices else None

    def line(tag, w, costs, start):
        m = run_backtest(w, prices, costs, start=start).metrics
        print(
            f"  {tag:35s} CAGR {m['cagr']:+.3f}  Sharpe {m['sharpe']:.2f}  "
            f"MaxDD {m['max_drawdown']:.2f}  turn {m.get('avg_annual_turnover', 0):.1f}"
        )
        return m

    for label, costs in [("ALPACA tiered costs", ALPACA_TIERED), ("T212 ISA tiered+FX", T212_TIERED)]:
        print(f"\n=== {label} ===")
        print(" FULL PERIOD:")
        m_full     = line("equal-weight + bear overlay      ", w_strat, costs, str(warmup.date()))
        m_full_vol = line("inv-vol-weight + bear overlay    ", w_strat_vol, costs, str(warmup.date()))
        if spy_w is not None:
            line("SPY benchmark                    ", spy_w, ZERO_COSTS, str(warmup.date()))
        print(" HOLDOUT (last 252d):")
        m_hold     = line("equal-weight + bear overlay      ", w_strat, costs, str(holdout_start.date()))
        m_hold_vol = line("inv-vol-weight + bear overlay    ", w_strat_vol, costs, str(holdout_start.date()))
        if spy_w is not None:
            line("SPY benchmark                    ", spy_w, ZERO_COSTS, str(holdout_start.date()))
        if label.startswith("ALPACA"):
            for name, w, m_f, m_h in [
                ("v2 equal-weight", w_strat, m_full, m_hold),
                ("v2 inv-vol-weight", w_strat_vol, m_full_vol, m_hold_vol),
            ]:
                save_backtest_run(
                    f"{name} (full)",
                    {},
                    m_f,
                    run_backtest(w, prices, costs, start=str(warmup.date())).equity,
                )
                save_backtest_run(
                    f"{name} HOLDOUT 252d",
                    {},
                    m_h,
                    run_backtest(w, prices, costs, start=str(holdout_start.date())).equity,
                )

    # Congress-blend comparison
    print("\n=== CONGRESS-MOMENTUM BLEND (congress boost x2, 45d window) ===")
    try:
        from datadesk.strategies.congress_blend import congress_momentum
        w_cong = congress_momentum(126, 10, 21, congress_boost=2.0, quality_universe=eligible)(prices)
        w_cong_bear = apply_bear(w_cong)
        print(" FULL PERIOD:")
        line("congress-blend + bear overlay    ", w_cong_bear, T212_ISA_COSTS, str(warmup.date()))
        line("pure momentum (equal-wt)          ", w_strat, T212_ISA_COSTS, str(warmup.date()))
        print(" HOLDOUT (last 252d):")
        line("congress-blend + bear overlay    ", w_cong_bear, T212_ISA_COSTS, str(holdout_start.date()))
        line("pure momentum (equal-wt)          ", w_strat, T212_ISA_COSTS, str(holdout_start.date()))
    except Exception as e:
        print(f"  Congress blend unavailable: {e}")

    # Phase-aware backtest: simulate starting from £500 + £500/month
    print("\n=== PHASE-AWARE BACKTEST (£500 start, £500/mo contributions) ===")
    from datadesk.backtest.costs import T212_ISA_COSTS
    from datadesk.backtest.phase_backtest import run_phase_backtest
    pb = run_phase_backtest(
        prices=prices,
        cost_model=T212_ISA_COSTS,
        initial_nav_gbp=500.0,
        monthly_contribution_gbp=500.0,
        start=str(warmup.date()),
        quality_universe=eligible,
    )
    print(f"  Final NAV:    £{pb.metrics['final_nav_gbp']:>12,.0f}")
    print(f"  Contributed:  £{pb.metrics['total_contributed_gbp']:>12,.0f}")
    print(f"  Strategy CAGR: {pb.metrics['cagr']:+.3f}  Sharpe {pb.metrics['sharpe']:.2f}  MaxDD {pb.metrics['max_drawdown']:.2f}")
    print(f"  Phase transitions: {pb.metrics['n_transitions']}")
    for t in pb.transitions:
        print(f"    {t.date}  £{t.nav_gbp:>10,.0f}  top-{t.from_top_n} → top-{t.to_top_n}  ({t.new_phase})")

    # Economic regime breakdown over the backtest
    print("\n=== ECONOMIC REGIME (3-state: Expansion / Caution / Stress) ===")
    try:
        from datadesk.strategies.macro_regime import (
            economic_regime_scale, fetch_yield_curve, regime_stats, regime_series
        )
        yc = fetch_yield_curve(start="2012-01-01")
        spy_s = prices["SPY"] if "SPY" in prices.columns else None
        vix_s = prices["^VIX"] if "^VIX" in prices.columns else None
        if spy_s is not None and vix_s is not None:
            reg = regime_series(spy_s, vix_s, yc if not yc.empty else None)
            stats = regime_stats(reg)
            print(f"  Expansion: {stats['EXPANSION']:.1f}%  Caution: {stats['CAUTION']:.1f}%  "
                  f"Stress: {stats['STRESS']:.1f}%  (over full backtest period)")
            # Macro-regime overlay vs pure bear_only
            macro_scale = economic_regime_scale(spy_s, vix_s, yc if not yc.empty else None)
            w_macro = w_eq.mul(macro_scale, axis=0)
            print(" FULL PERIOD (T212 tiered):")
            line("3-state macro + momentum         ", w_macro, T212_TIERED, str(warmup.date()))
            line("bear_only + momentum (baseline)   ", w_strat, T212_TIERED, str(warmup.date()))
            print(" HOLDOUT (last 252d):")
            line("3-state macro + momentum         ", w_macro, T212_TIERED, str(holdout_start.date()))
            line("bear_only + momentum (baseline)   ", w_strat, T212_TIERED, str(holdout_start.date()))
    except Exception as e:
        print(f"  Macro regime unavailable: {e}")

    print("\nSaved to platform store.")
    print("GATE 1: beat SPY on Sharpe AND max-drawdown in the holdout.")
    print(
        "NOTE: universe still survivorship-biased until Tiingo backfill — levels not yet evidence."
    )


def cmd_signal_audit() -> None:
    """Show when momentum first identified each major winner — look-ahead bias analysis."""
    from datadesk.analysis.signal_audit import print_signal_audit, run_signal_audit
    from datadesk.history.store import coverage, load_closes

    cov = coverage()
    tickers = cov[cov["rows"] > 2000]["ticker"].tolist()
    prices = load_closes(tickers=tickers)
    prices = prices[prices.index >= "2014-01-01"].ffill().dropna(axis=1)
    records = run_signal_audit(prices, backtest_start="2016-05-24")
    print_signal_audit(records, top_n=30)

    print("\n=== UNDERSTANDING THE TABLE ===")
    print("'From Sig%' = gain from first momentum signal to today (what the strategy captured)")
    print("'Total%'    = gain from first price bar (includes pre-signal period)")
    print("'Captured'  = From Sig% / Total% (>100% means signal caught it early)")
    print("'Look-ahead?YES*' = ticker added to universe AFTER backtest start (selection bias)")
    print("\nNVDA insight: if first signal was 2016, that means real-time momentum caught it")
    print("at the gaming GPU super-cycle peak — before AI infrastructure was the thesis.")


def cmd_screen() -> None:
    """Print current multi-factor forward screener — find next breakout stocks."""
    from datadesk.analysis.forward_screener import print_forward_screen, rank_universe
    df = rank_universe()
    if df.empty:
        print("No momentum signals — run backfill to populate price history")
        return
    print_forward_screen(df, top_n=20)
    print("\nTO ACTIVATE NEWS SENTIMENT:")
    print("  pip install vaderSentiment")
    print("  Then edit datadesk/analysis/forward_screener.py:news_sentiment_score()")
    print("  Returns a 0-1 score per ticker. Wired to 0.10 weight when news_weight=0.10")
    print("  passed to rank_universe().")


def cmd_universe_expand(theme: str | None = None, dry_run: bool = False) -> None:
    """
    Expand the price universe by fetching constituents of themed ETFs.

    This is the discovery mechanism for unknown breakout candidates.
    Fetches price history for tickers in THEMES that we don't yet track,
    then runs backfill on any that have sufficient history (>252 bars).

    Usage:
      python main.py universe-expand               # all themes
      python main.py universe-expand --theme AI_INFRA
      python main.py universe-expand --dry-run     # list new tickers, don't fetch
    """
    from datadesk.analysis.forward_screener import THEMES
    from datadesk.history.store import coverage

    existing = set(coverage()["ticker"].tolist())

    if theme:
        themes_to_check = {theme: THEMES.get(theme, [])}
        if not themes_to_check[theme]:
            print(f"Unknown theme '{theme}'. Available: {', '.join(THEMES.keys())}")
            return
    else:
        themes_to_check = THEMES

    new_tickers: set[str] = set()
    for t_name, members in themes_to_check.items():
        new_in_theme = [t for t in members if t not in existing]
        if new_in_theme:
            print(f"  {t_name}: {len(new_in_theme)} new tickers: {', '.join(new_in_theme)}")
            new_tickers.update(new_in_theme)

    if not new_tickers:
        print("All theme members already in universe.")
        return

    print(f"\n{len(new_tickers)} new tickers to add: {', '.join(sorted(new_tickers))}")
    if dry_run:
        print("(dry-run mode — not fetching. Remove --dry-run to backfill.)")
        print("\nNOTE: To find truly UNKNOWN stocks beyond these themes:")
        print("  1. Browse ETF constituent pages (SMH, QQQ, QTUM, BOTZ, AIQ)")
        print("  2. Add tickers to THEMES in datadesk/analysis/forward_screener.py")
        print("  3. Run universe-expand to backfill them")
        print("  4. Run screen to see where they rank on the composite signal")
        return

    print("\nFetching price history + fundamentals (this may take a few minutes)...")
    # Filter to US/international stocks only (skip complex suffixes we can't backfill)
    backfillable = [t for t in sorted(new_tickers)
                    if not any(t.endswith(s) for s in [".NS", ".MI", ".DE", ".PA", ".AS"])]
    if len(backfillable) < len(new_tickers):
        skipped = sorted(new_tickers - set(backfillable))
        print(f"  Skipping {len(skipped)} tickers (unsupported exchange suffix): {', '.join(skipped)}")

    if backfillable:
        from datadesk.ingest.backfill import backfill_tickers
        backfill_tickers(backfillable, source="yahoo", skip_fundamentals=False)
        print(f"\nDone. Run 'python main.py screen' to see new tickers in the screener.")
    else:
        print("No backfillable tickers remaining.")


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


def cmd_phase_projection(
    monthly_gbp: float = 500.0,
    initial_gbp: float = 500.0,
    cagr: float = 0.20,
    years: int = 15,
) -> None:
    """
    Project portfolio phase transitions under assumed CAGR + monthly contributions.

    Shows when the strategy shifts from concentrated (Phase 1, top-3)
    through to the full cross-sectional portfolio (Phase 4, top-15).
    """
    from datadesk.strategies.phase import PHASES, _THRESHOLDS, simulate_nav_series

    rows = simulate_nav_series(monthly_gbp, initial_gbp, cagr, years)

    print(f"\nPhase projection — £{initial_gbp:,.0f} start, £{monthly_gbp:,.0f}/mo, {cagr:.0%} CAGR")
    print(f"\n{'Month':>6}  {'NAV':>10}  {'Phase':<35}  top_n")
    print("─" * 65)

    current_phase = None
    last_year = -1
    for month, nav, phase_label in rows:
        year = (month - 1) // 12 + 1
        if phase_label != current_phase or month == 1:
            if phase_label != current_phase:
                print(f"{'':>6}  {'':>10}  *** PHASE CHANGE ***")
            current_phase = phase_label
        if year != last_year or phase_label != current_phase:
            from datadesk.strategies.phase import portfolio_phase
            p = portfolio_phase(nav)
            print(f"  Yr {year:>2}  £{nav:>9,.0f}  {phase_label:<35}  {p.top_n}")
            last_year = year

    print(f"\nPhase thresholds: {[f'£{t:,}' for t in _THRESHOLDS]}")
    print("Phase 1: top-3  |  Phase 2: top-6  |  Phase 3: top-10  |  Phase 4: top-15")
    print("\nTo simulate a different scenario:")
    print("  python main.py phase-projection --monthly 1000 --cagr 0.30 --years 10")


def cmd_event_study(study: str = "congress") -> None:
    """
    Run and print an event study.

      python main.py event-study congress   (default)
      python main.py event-study trump
    """
    if study == "congress":
        from datadesk.analysis.congress_events import run_congress_event_study
        print("Running congress event study (parsing 16k+ disclosures)…")
        s = run_congress_event_study()
        print(f"\n{'─'*60}")
        print(f"  CONGRESS TRADING EVENT STUDY")
        print(f"  {s.n_events} events · {s.n_tickers} tickers")
        print(f"{'─'*60}")
        header = f"  {'Type':<6}" + "".join(f"  +{w}d abn" for w in s.windows)
        print(header)
        for tx in ("buy", "sell"):
            ab = s.avg_abnormal.get(tx, {})
            row = f"  {tx.upper():<6}" + "".join(
                f"  {ab.get(w, 0):+.2%}" for w in s.windows
            )
            print(row)
        print(f"\n  Top tickers (buy, 20d alpha, ≥2 events):")
        for r in s.top_tickers[:10]:
            print(f"    {r['ticker']:<8} avg abn {r['avg_abn_20d']:+.2%}  n={r['n_events']}")
        print(f"\n  Top legislators (avg abnormal, ≥3 buys):")
        for r in s.top_legislators[:8]:
            print(f"    {r['filer']:<30} avg {r['avg_abn']:+.2%}  win {r['win_rate']:.0%}  n={r['n_buys']}")
    elif study == "trump":
        from datadesk.analysis.trump_events import run_trump_event_study
        print("Running Trump post event study (classifying 33k+ posts)…")
        s = run_trump_event_study()
        print(f"\n{'─'*65}")
        print(f"  TRUMP POST EVENT STUDY — {s.n_posts:,} posts, {s.n_actionable} actionable")
        print(f"{'─'*65}")
        header = f"  {'Category':<22}" + "".join(f"  +{w}d abn" for w in s.windows)
        print(header)
        for cat in sorted(s.category_abnormal):
            ab = s.category_abnormal[cat]
            cnt = s.category_counts.get(cat, 0)
            row = f"  {cat:<22}" + "".join(f"  {ab.get(w, 0):+.4f}" for w in s.windows)
            print(f"{row}  (n={cnt})")
        print("\n  Interpretation: values are abnormal vs unconditional SPY baseline.")
        print("  Small magnitudes (<0.5%) are noise-level and not tradeable after costs.")
    else:
        print(f"Unknown study '{study}'. Choose: congress, trump")


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
    sub.add_parser("index-seed")
    sub.add_parser("signal-audit")
    sub.add_parser("screen")
    p_ue = sub.add_parser("universe-expand")
    p_ue.add_argument("--theme", default=None, help="Limit to one theme (e.g. QUANTUM)")
    p_ue.add_argument("--dry-run", action="store_true", help="List new tickers without fetching")
    p_es = sub.add_parser("event-study")
    p_es.add_argument("study", nargs="?", default="congress", choices=["congress", "trump"])
    p_phase = sub.add_parser("phase-projection")
    p_phase.add_argument("--monthly", type=float, default=500.0, help="Monthly contribution (£)")
    p_phase.add_argument("--initial", type=float, default=500.0, help="Starting NAV (£)")
    p_phase.add_argument("--cagr", type=float, default=0.20, help="Assumed annual CAGR (e.g. 0.20)")
    p_phase.add_argument("--years", type=int, default=15, help="Projection horizon (years)")
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
    elif args.command == "event-study":
        cmd_event_study(args.study)
    elif args.command == "phase-projection":
        cmd_phase_projection(args.monthly, args.initial, args.cagr, args.years)
    elif args.command == "index-seed":
        from datadesk.ingest.index_membership import upsert_index_memberships
        n = upsert_index_memberships()
        print(f"index_memberships table seeded: {n} rows")
    elif args.command == "signal-audit":
        cmd_signal_audit()
    elif args.command == "screen":
        cmd_screen()
    elif args.command == "universe-expand":
        cmd_universe_expand(theme=args.theme, dry_run=args.dry_run)
    elif args.command == "universe":
        cmd_universe()
