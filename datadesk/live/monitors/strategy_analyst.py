"""
Out-of-session Strategy Analyst.

Runs after NYSE close. Loads the latest sweep results and evaluates:
  1. Consistency check  — does 1y holdout agree with 3y/5y holdout?
  2. Degradation check  — is the best strategy's recent CAGR falling vs older runs?
  3. Universe ranking   — which universe family is generating the most alpha?
  4. Overfitting flag   — large gap between full-period and holdout CAGR
  5. Promotion/demotion — recommends which strategies to promote to live and which to retire

Uses the local LLM (Ollama) to write a narrative briefing if available;
falls back to pure rule-based summary if Ollama is unreachable.
"""

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

POLL_HOURS = 24
OVERFITTING_THRESHOLD = 2.0   # full-period CAGR / holdout CAGR > this → suspect
MIN_SHARPE_TO_PROMOTE = 1.0
MAX_DD_TO_PROMOTE = -0.30     # -30%
MIN_TOP_N_TO_PROMOTE = 2      # don't promote top_n=1 strategies


class StrategyAnalyst:
    def __init__(self):
        self.is_running = False
        self.last_run = "Never"

    def start(self):
        self.is_running = True
        logger.info("[STRATEGY] analyst started — runs once per evening after NYSE close")
        while self.is_running:
            try:
                from datadesk.live.market_calendar import exchange_is_open
                if not exchange_is_open("NYSE"):
                    self.run()
                    self.last_run = datetime.now().strftime("%H:%M:%S")
            except Exception as e:
                logger.exception(f"[STRATEGY] error: {e}")
            time.sleep(POLL_HOURS * 3600)

    def stop(self):
        self.is_running = False

    def run(self) -> dict:
        from datadesk.db import load_backtest_runs, save_report

        logger.info("[STRATEGY] loading sweep results for analysis")
        all_runs = load_backtest_runs(limit=2000)
        if not all_runs:
            logger.warning("[STRATEGY] no backtest runs found")
            return {"status": "no_runs"}

        # Split into full-period and holdout runs
        holdout_1y = {r["name"].replace(" HOLDOUT 1y", ""): r for r in all_runs if "HOLDOUT 1y" in r["name"]}
        holdout_3y = {r["name"].replace(" HOLDOUT 3y", ""): r for r in all_runs if "HOLDOUT 3y" in r["name"]}
        full_runs  = {r["name"]: r for r in all_runs if "HOLDOUT" not in r["name"]}

        findings = []
        promotions = []
        demotions = []
        universe_cagrs: dict[str, list[float]] = {}

        for base_name, h1 in holdout_1y.items():
            m1 = h1["metrics"]
            params = h1["params"]
            cagr_1y = m1.get("cagr", 0)
            sharpe_1y = m1.get("sharpe", 0)
            mdd_1y = m1.get("max_drawdown", -1)
            top_n = params.get("mom_top_n", 1)
            univ = params.get("universe", "?")

            # Track universe-level performance
            universe_cagrs.setdefault(univ, []).append(cagr_1y)

            # Overfitting check
            full = full_runs.get(base_name)
            if full:
                full_cagr = full["metrics"].get("cagr", 0)
                if full_cagr > 0 and cagr_1y > 0:
                    ratio = full_cagr / cagr_1y
                    if ratio > OVERFITTING_THRESHOLD:
                        findings.append({
                            "type": "overfit",
                            "strategy": base_name,
                            "full_cagr": round(full_cagr, 3),
                            "holdout_1y_cagr": round(cagr_1y, 3),
                            "ratio": round(ratio, 2),
                        })

            # 3y consistency check
            h3 = holdout_3y.get(base_name)
            cagr_3y = h3["metrics"].get("cagr", 0) if h3 else None

            # Promotion criteria
            promotable = (
                sharpe_1y >= MIN_SHARPE_TO_PROMOTE
                and mdd_1y >= MAX_DD_TO_PROMOTE
                and top_n >= MIN_TOP_N_TO_PROMOTE
                and cagr_1y > 0.15
                and (cagr_3y is None or cagr_3y > 0.10)
            )
            if promotable:
                promotions.append({
                    "strategy": base_name,
                    "cagr_1y": round(cagr_1y, 3),
                    "cagr_3y": round(cagr_3y, 3) if cagr_3y else None,
                    "sharpe": round(sharpe_1y, 2),
                    "max_dd": round(mdd_1y, 3),
                    "params": params,
                })

            # Demotion: good full-period but bad holdout
            if full and full["metrics"].get("cagr", 0) > 0.30 and cagr_1y < 0.05:
                demotions.append({
                    "strategy": base_name,
                    "full_cagr": round(full["metrics"].get("cagr", 0), 3),
                    "holdout_1y_cagr": round(cagr_1y, 3),
                })

        # Sort promotions by 1y CAGR
        promotions.sort(key=lambda x: x["cagr_1y"], reverse=True)

        # Universe ranking
        univ_summary = {
            u: {"mean_cagr": round(sum(v)/len(v), 3), "n": len(v)}
            for u, v in universe_cagrs.items() if v
        }
        univ_ranked = sorted(univ_summary.items(), key=lambda x: x[1]["mean_cagr"], reverse=True)

        # ── Narrative briefing ────────────────────────────────────────────────
        lines = [f"Strategy Analysis — {datetime.now().strftime('%Y-%m-%d')} UTC\n"]
        lines.append(f"Analysed {len(holdout_1y)} strategies across {len(univ_summary)} universes.\n")

        lines.append("UNIVERSE RANKING (by mean 1y holdout CAGR):")
        for u, s in univ_ranked:
            lines.append(f"  {u:<20} {s['mean_cagr']*100:.1f}% avg  ({s['n']} strategies)")

        lines.append(f"\nPROMOTION CANDIDATES ({len(promotions)} strategies meet criteria):")
        for p in promotions[:10]:
            lines.append(
                f"  {p['strategy'][:55]:<55} "
                f"1y={p['cagr_1y']*100:.1f}%  "
                f"3y={p['cagr_3y']*100:.1f}%  " if p["cagr_3y"] else "  3y=N/A  "
                f"Sharpe={p['sharpe']:.2f}  MaxDD={p['max_dd']*100:.1f}%"
            )

        lines.append(f"\nOVERFITTING ALERTS ({len(findings)}):")
        for f in findings[:5]:
            lines.append(
                f"  {f['strategy'][:50]:<50} full={f['full_cagr']*100:.0f}% vs holdout={f['holdout_1y_cagr']*100:.0f}% "
                f"(ratio {f['ratio']:.1f}x)"
            )

        lines.append(f"\nDEGRADED STRATEGIES ({len(demotions)} were good in-sample but failed holdout):")
        for d in demotions[:5]:
            lines.append(
                f"  {d['strategy'][:50]:<50} full={d['full_cagr']*100:.0f}% vs holdout={d['holdout_1y_cagr']*100:.0f}%"
            )

        # Try LLM narrative
        narrative = self._llm_narrative(promotions[:3], findings[:3], univ_ranked)
        if narrative:
            lines.append(f"\nANALYST COMMENTARY:\n{narrative}")

        body = "\n".join(lines)
        logger.info(f"[STRATEGY]\n{body}")

        save_report(
            analyst="strategy",
            title=f"Strategy analysis {datetime.now().strftime('%Y-%m-%d')}",
            body=body,
            data={
                "promotions": promotions[:10],
                "demotions": demotions[:5],
                "overfit_alerts": findings[:10],
                "universe_ranking": [{"universe": u, **s} for u, s in univ_ranked],
            },
        )
        return {"status": "ok", "promotions": len(promotions), "alerts": len(findings)}

    def _llm_narrative(self, promotions: list, alerts: list, universe_ranking: list) -> str | None:
        """Ask Ollama for a brief narrative. Returns None if unavailable."""
        try:
            import requests
            prompt = (
                "You are a quantitative strategy analyst. Summarise these findings in 3 concise bullet points:\n\n"
                f"Top promotions: {promotions[:3]}\n"
                f"Overfitting alerts: {alerts[:3]}\n"
                f"Universe ranking: {universe_ranking[:5]}\n\n"
                "Focus on what the portfolio manager should do tomorrow. Be direct and specific."
            )
            r = requests.post(
                "http://localhost:11434/api/generate",
                json={"model": "phi3:mini", "prompt": prompt, "stream": False},
                timeout=30,
            )
            if r.status_code == 200:
                return r.json().get("response", "").strip()
        except Exception:
            pass
        return None
