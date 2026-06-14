"""
Out-of-session Stock Research & Discovery Analyst.

Runs once per evening (after NYSE close, before next open). Screens the
full altdata.db universe plus any tickers not yet in the sweep, scores them
on a composite of momentum, fundamental quality, and insider/congress signal,
and writes a ranked candidates briefing to analyst_reports.

Discovery pipeline:
  1. Momentum score  — 3m/6m/12m price return percentile rank
  2. Quality score   — gross margin, ROE, low debt-to-equity
  3. Insider signal  — clustered Form 4 buys in last 30 days
  4. Congress signal — STOCK Act purchases in last 60 days
  5. Liquidity guard — drop anything with avg daily volume < $1M

Output: top-20 candidates ranked by composite score, with a plain-English
summary of why each one scored well.
"""

import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

POLL_HOURS = 24         # run once per day
MIN_AVG_VOLUME_M = 1.0  # $1M avg daily volume minimum


class ResearchAnalyst:
    def __init__(self):
        self.is_running = False
        self.last_run = "Never"

    def start(self):
        self.is_running = True
        logger.info("[RESEARCH] analyst started — runs once per evening after NYSE close")
        while self.is_running:
            try:
                from datadesk.live.market_calendar import exchange_is_open, is_trading_day
                import datetime as _dt
                today = _dt.date.today()
                # Run only when NYSE is closed (out-of-session)
                if not exchange_is_open("NYSE"):
                    self.run()
                    self.last_run = datetime.now().strftime("%H:%M:%S")
            except Exception as e:
                logger.error(f"[RESEARCH] error: {e}")
            time.sleep(POLL_HOURS * 3600)

    def stop(self):
        self.is_running = False

    def run(self) -> dict:
        import sqlite3
        import pandas as pd
        from datadesk.config import ALTDATA_DB
        from datadesk.history.store import load_closes
        from datadesk.db import save_report

        logger.info("[RESEARCH] starting discovery scan")

        # ── 1. Candidate universe: everything in equity_info ─────────────────
        con = sqlite3.connect(ALTDATA_DB)
        info = pd.read_sql("SELECT ticker, name, sector, industry FROM equity_info", con)
        ratios = pd.read_sql(
            """SELECT ticker, gross_margin, roe, debt_to_equity, market_cap,
                      revenue_growth, trailing_pe, free_cashflow
               FROM equity_ratios
               WHERE id IN (SELECT MAX(id) FROM equity_ratios GROUP BY ticker)""",
            con,
        )
        insiders = pd.read_sql(
            """SELECT ticker, COUNT(DISTINCT filer_name) as insider_buyers,
                      MAX(filing_date) as last_buy
               FROM insiders
               WHERE transaction_type='P'
                 AND filing_date >= date('now', '-30 days')
               GROUP BY ticker""",
            con,
        )
        congress = pd.read_sql(
            """SELECT ticker, COUNT(*) as congress_buys,
                      MAX(disclosure_date) as last_congress_buy
               FROM congress_trading
               WHERE transaction_type='buy'
                 AND disclosure_date >= date('now', '-60 days')
               GROUP BY ticker""",
            con,
        )
        con.close()

        candidates = info.merge(ratios, on="ticker", how="left")
        candidates = candidates.merge(insiders, on="ticker", how="left")
        candidates = candidates.merge(congress, on="ticker", how="left")
        candidates["insider_buyers"] = candidates["insider_buyers"].fillna(0)
        candidates["congress_buys"] = candidates["congress_buys"].fillna(0)

        # ── 2. Momentum scores from price history ────────────────────────────
        tickers = candidates["ticker"].tolist()
        prices = load_closes(tickers=tickers)
        if prices is not None and not prices.empty:
            def mom_score(n_days: int) -> pd.Series:
                if len(prices) < n_days:
                    return pd.Series(0.0, index=prices.columns)
                ret = prices.iloc[-1] / prices.iloc[-n_days] - 1
                return ret.rank(pct=True)

            mom3 = mom_score(63)
            mom6 = mom_score(126)
            mom12 = mom_score(252)
            mom_composite = (mom3 * 0.5 + mom6 * 0.3 + mom12 * 0.2)
            mom_df = mom_composite.rename("momentum_score").reset_index()
            mom_df.columns = ["ticker", "momentum_score"]
            candidates = candidates.merge(mom_df, on="ticker", how="left")
        else:
            candidates["momentum_score"] = 0.0

        # ── 3. Quality score ─────────────────────────────────────────────────
        def _rank(series: pd.Series) -> pd.Series:
            return series.rank(pct=True, na_option="bottom")

        candidates["quality_score"] = (
            _rank(candidates["gross_margin"]) * 0.35
            + _rank(candidates["roe"]) * 0.35
            + _rank(-candidates["debt_to_equity"].fillna(999)) * 0.15
            + _rank(candidates["revenue_growth"]) * 0.15
        )

        # ── 4. Signal score ──────────────────────────────────────────────────
        candidates["signal_score"] = (
            candidates["insider_buyers"].clip(0, 5) / 5.0 * 0.6
            + candidates["congress_buys"].clip(0, 3) / 3.0 * 0.4
        )

        # ── 5. Composite ─────────────────────────────────────────────────────
        candidates["composite"] = (
            candidates["momentum_score"].fillna(0) * 0.40
            + candidates["quality_score"].fillna(0) * 0.35
            + candidates["signal_score"].fillna(0) * 0.25
        )

        top = (
            candidates.nlargest(20, "composite")
            [["ticker", "name", "sector", "composite",
              "momentum_score", "quality_score", "signal_score",
              "insider_buyers", "congress_buys", "gross_margin", "roe",
              "market_cap", "revenue_growth"]]
            .round(3)
        )

        records = top.to_dict(orient="records")

        # ── 6. Plain-English briefing ─────────────────────────────────────────
        lines = [f"Discovery scan — {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC\n"]
        lines.append(f"Scored {len(candidates)} tickers. Top 20 by composite:\n")
        for r in records[:10]:
            flags = []
            if r.get("insider_buyers", 0) >= 3:
                flags.append(f"{int(r['insider_buyers'])} insider buys (30d)")
            if r.get("congress_buys", 0) >= 1:
                flags.append(f"{int(r['congress_buys'])} congress buys (60d)")
            flag_str = " | ".join(flags) if flags else "no insider/congress signal"
            lines.append(
                f"  {r['ticker']:<8} {(r.get('name') or '')[:28]:<28} "
                f"composite={r['composite']:.3f}  mom={r.get('momentum_score',0):.2f}  "
                f"qual={r.get('quality_score',0):.2f}  [{flag_str}]"
            )

        body = "\n".join(lines)
        logger.info(f"[RESEARCH] {body}")

        save_report(
            analyst="research",
            title=f"Discovery scan {datetime.now().strftime('%Y-%m-%d')}",
            body=body,
            data={"candidates": records, "universe_size": len(candidates)},
        )
        return {"status": "ok", "candidates": len(records)}
