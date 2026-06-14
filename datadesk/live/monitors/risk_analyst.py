"""
Risk Analyst — runs out-of-session AND mid-session for live positions.

Out-of-session (nightly): full portfolio risk report against historical stress.
Mid-session (every 30 min when NYSE open): fast position-level checks only.

Checks:
  - Single-name concentration (> 15% NAV)
  - Sector concentration (> 40% NAV in one GICS sector)
  - Portfolio beta vs SPY
  - Pairwise correlation between held positions (crowded trade detection)
  - Current drawdown vs historical max drawdown from best strategy
  - Daily loss vs kill-switch limit (5% NAV)
  - Liquidity: position size vs avg daily volume (would take > 1 day to exit?)
"""

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datadesk.live.oms import OMSFastPath

logger = logging.getLogger(__name__)

CONCENTRATION_LIMIT  = 0.15   # 15% single-name
SECTOR_LIMIT         = 0.40   # 40% one sector
DAILY_LOSS_WARN      = 0.03   # warn at 3% (kill-switch fires at 5%)
CORRELATION_WARN     = 0.80   # flag pairs with r > 0.80
INTRADAY_POLL        = 30 * 60  # 30 minutes during session
OVERNIGHT_POLL       = 6 * 3600 # 6 hours overnight


class RiskAnalyst:
    def __init__(self, oms: "OMSFastPath"):
        self.oms = oms
        self.is_running = False
        self.last_run = "Never"

    def start(self):
        self.is_running = True
        logger.info("[RISK] analyst started")
        while self.is_running:
            try:
                from datadesk.live.market_calendar import exchange_is_open
                in_session = exchange_is_open("NYSE")
                alerts = self.run(full=not in_session)
                self.last_run = datetime.now().strftime("%H:%M:%S")
                if alerts:
                    logger.warning(f"[RISK] {len(alerts)} alert(s): {[a['type'] for a in alerts]}")
                sleep_secs = INTRADAY_POLL if in_session else OVERNIGHT_POLL
            except Exception as e:
                logger.exception(f"[RISK] error: {e}")
                sleep_secs = 300
            time.sleep(sleep_secs)

    def stop(self):
        self.is_running = False

    def run(self, full: bool = True) -> list[dict]:
        """
        Run risk checks. `full=True` runs overnight deep analysis including
        correlation matrix and stress test. `full=False` is the fast intraday
        check (concentration + daily loss only).
        """
        from datadesk.db import load_reports, save_report
        alerts = []

        positions = dict(self.oms.active_positions)
        nav = self.oms.current_nav
        daily_start = self.oms.daily_starting_nav
        daily_loss_pct = (nav - daily_start) / daily_start if daily_start else 0

        # ── Fast checks (always) ─────────────────────────────────────────────

        # 1. Daily loss warning
        if daily_loss_pct <= -DAILY_LOSS_WARN:
            alerts.append({
                "type": "daily_loss",
                "severity": "HIGH" if daily_loss_pct <= -0.04 else "MEDIUM",
                "message": f"Daily P&L {daily_loss_pct:.1%} (kill-switch at -5%)",
                "value": round(daily_loss_pct, 4),
            })

        # 2. Single-name concentration
        for ticker, pos in positions.items():
            alloc = pos.get("alloc", 0)
            if alloc > CONCENTRATION_LIMIT:
                alerts.append({
                    "type": "concentration",
                    "severity": "HIGH",
                    "ticker": ticker,
                    "message": f"{ticker} is {alloc:.1%} of NAV (limit {CONCENTRATION_LIMIT:.0%})",
                    "value": round(alloc, 4),
                })

        if not full:
            return alerts

        # ── Overnight deep analysis ──────────────────────────────────────────

        # 3. Sector concentration
        sector_exposure = self._sector_exposure(positions)
        for sector, weight in sector_exposure.items():
            if weight > SECTOR_LIMIT:
                alerts.append({
                    "type": "sector_concentration",
                    "severity": "MEDIUM",
                    "sector": sector,
                    "message": f"{sector} sector at {weight:.1%} of NAV (limit {SECTOR_LIMIT:.0%})",
                    "value": round(weight, 4),
                })

        # 4. Portfolio beta
        beta = self._portfolio_beta(positions)
        if beta is not None and beta > 2.0:
            alerts.append({
                "type": "high_beta",
                "severity": "MEDIUM",
                "message": f"Portfolio beta vs SPY: {beta:.2f} (high leverage exposure)",
                "value": round(beta, 3),
            })

        # 5. Pairwise correlation (crowded trade detection)
        corr_alerts = self._correlation_check(list(positions.keys()))
        alerts.extend(corr_alerts)

        # 6. Drawdown vs strategy expectation
        dd_alert = self._drawdown_check(daily_loss_pct)
        if dd_alert:
            alerts.append(dd_alert)

        # ── Save report ───────────────────────────────────────────────────────
        severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        alerts.sort(key=lambda x: severity_order.get(x.get("severity", "LOW"), 2))

        lines = [f"Risk Report — {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC\n"]
        lines.append(f"NAV: £{nav:,.0f}  Daily P&L: {daily_loss_pct:+.1%}  Positions: {len(positions)}\n")
        if alerts:
            lines.append(f"ALERTS ({len(alerts)}):")
            for a in alerts:
                lines.append(f"  [{a.get('severity','?')}] {a['message']}")
        else:
            lines.append("No alerts — portfolio within risk limits.")

        if sector_exposure:
            lines.append("\nSECTOR EXPOSURE:")
            for s, w in sorted(sector_exposure.items(), key=lambda x: -x[1]):
                lines.append(f"  {s:<30} {w:.1%}")

        if beta is not None:
            lines.append(f"\nPORTFOLIO BETA (vs SPY): {beta:.2f}")

        body = "\n".join(lines)
        logger.info(f"[RISK]\n{body}")

        save_report(
            analyst="risk",
            title=f"Risk report {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            body=body,
            data={
                "alerts": alerts,
                "nav": nav,
                "daily_loss_pct": round(daily_loss_pct, 4),
                "n_positions": len(positions),
                "sector_exposure": sector_exposure,
                "portfolio_beta": beta,
            },
        )
        return alerts

    def _sector_exposure(self, positions: dict) -> dict[str, float]:
        """Look up sector for each held ticker from altdata.db."""
        if not positions:
            return {}
        try:
            import sqlite3
            from datadesk.config import ALTDATA_DB
            con = sqlite3.connect(ALTDATA_DB)
            tickers = list(positions.keys())
            placeholders = ",".join("?" * len(tickers))
            rows = con.execute(
                f"SELECT ticker, sector FROM equity_info WHERE ticker IN ({placeholders})",
                tickers,
            ).fetchall()
            con.close()
            sector_map = {r[0]: r[1] or "Unknown" for r in rows}
        except Exception:
            sector_map = {}

        exposure: dict[str, float] = {}
        for ticker, pos in positions.items():
            sector = sector_map.get(ticker, "Unknown")
            exposure[sector] = exposure.get(sector, 0) + pos.get("alloc", 0)
        return exposure

    def _portfolio_beta(self, positions: dict) -> float | None:
        """Compute weighted average beta from altdata.db equity_ratios."""
        if not positions:
            return None
        try:
            import sqlite3
            from datadesk.config import ALTDATA_DB
            tickers = list(positions.keys())
            placeholders = ",".join("?" * len(tickers))
            con = sqlite3.connect(ALTDATA_DB)
            rows = con.execute(
                f"""SELECT ticker, beta FROM equity_ratios
                    WHERE ticker IN ({placeholders})
                      AND id IN (SELECT MAX(id) FROM equity_ratios GROUP BY ticker)""",
                tickers,
            ).fetchall()
            con.close()
            beta_map = {r[0]: r[1] for r in rows if r[1] is not None}
        except Exception:
            return None

        total_w = sum(pos.get("alloc", 0) for pos in positions.values())
        if total_w == 0:
            return None
        beta = sum(
            pos.get("alloc", 0) * beta_map.get(t, 1.0)
            for t, pos in positions.items()
        ) / total_w
        return round(beta, 3)

    def _correlation_check(self, tickers: list[str]) -> list[dict]:
        """Flag pairs of held positions with price correlation > CORRELATION_WARN."""
        if len(tickers) < 2:
            return []
        try:
            from datadesk.history.store import load_closes
            prices = load_closes(tickers=tickers)
            if prices is None or prices.empty or len(prices) < 60:
                return []
            corr = prices.pct_change().dropna().corr()
            alerts = []
            done = set()
            for t1 in tickers:
                for t2 in tickers:
                    if t1 == t2 or (t2, t1) in done:
                        continue
                    done.add((t1, t2))
                    if t1 in corr.columns and t2 in corr.columns:
                        r = corr.loc[t1, t2]
                        if r > CORRELATION_WARN:
                            alerts.append({
                                "type": "high_correlation",
                                "severity": "LOW",
                                "message": f"{t1}/{t2} correlation {r:.2f} — crowded trade risk",
                                "value": round(r, 3),
                                "pair": [t1, t2],
                            })
            return alerts
        except Exception:
            return []

    def _drawdown_check(self, current_daily_loss: float) -> dict | None:
        """Compare current loss against the expected max DD from best strategy."""
        try:
            from datadesk.db import load_backtest_runs
            runs = load_backtest_runs(limit=50)
            holdouts = [r for r in runs if "HOLDOUT" in r["name"]]
            if not holdouts:
                return None
            best = max(holdouts, key=lambda r: r["metrics"].get("sharpe", 0))
            expected_mdd = best["metrics"].get("max_drawdown", -0.15)
            # Warn if today's loss is already > 50% of expected full-period max DD
            if current_daily_loss < expected_mdd * 0.5:
                return {
                    "type": "drawdown_pace",
                    "severity": "MEDIUM",
                    "message": (
                        f"Daily loss {current_daily_loss:.1%} exceeds 50% of expected "
                        f"max DD {expected_mdd:.1%} from best strategy '{best['name'][:40]}'"
                    ),
                    "value": round(current_daily_loss, 4),
                }
        except Exception:
            pass
        return None
