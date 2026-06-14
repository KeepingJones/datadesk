"""
Background AI worker.

Two real jobs, zero fabrication:
1. validate_adopted_positions — quantitative check of positions adopted from the
   broker (long-only rule + momentum screen). Pure rules, no LLM.
2. revaluation loop — asks the LOCAL LLM (Ollama) to revalue active positions
   from recent context. If Ollama is unreachable or returns garbage, NOTHING
   happens: no random numbers, no hardcoded multipliers (the old "Situational
   Awareness" fair values were random.uniform in a trench coat — removed,
   claude-review-2026-06-11).
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from datadesk.live.oms import OMSFastPath

logger = logging.getLogger(__name__)

REVALUE_INTERVAL_SECONDS = 600

_REVALUE_PROMPT = """You are a cautious institutional analyst. Estimate a fair-value
multiple for {ticker} relative to its current price, given only this context:
- current price: {price}
- position thesis: {reason}
Respond with ONLY valid JSON: {{"fair_value_multiple": <float between 0.5 and 1.5>,
"reasoning": "<one sentence>"}}. If you cannot justify an estimate, use 1.0."""


class AgentWorker:
    def __init__(self, oms: "OMSFastPath"):
        self.oms = oms
        self.is_running = False
        self.last_run = "Never"
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.ollama_model = os.getenv("ANALYST_MODEL", "llama3.2")

    def start(self):
        self.is_running = True
        logger.info("[AGENT WORKER] started (LLM: %s @ %s)", self.ollama_model, self.ollama_url)
        self.validate_adopted_positions()
        while self.is_running:
            try:
                self.process_live_filings()
            except Exception as e:
                logger.exception(f"[AGENT WORKER] revaluation pass failed: {e}")
            self.last_run = datetime.now().strftime("%H:%M:%S")
            for _ in range(REVALUE_INTERVAL_SECONDS):
                if not self.is_running:
                    return
                time.sleep(1)

    def stop(self):
        self.is_running = False

    # ── Rule-based validation of adopted broker positions ───────────────────

    def validate_adopted_positions(self):
        adopted = [t for t, p in self.oms.active_positions.items() if p.get("is_adopted")]
        if not adopted:
            return
        logger.info(f"[AGENT WORKER] validating {len(adopted)} adopted positions...")

        from datadesk.history.store import load_closes
        from datadesk.strategies.momentum import momentum

        weights = None
        try:
            prices = load_closes(tickers=adopted)
            if not prices.empty:
                prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.9)).ffill()
                weights = momentum(lookback=126, top_n=10, skip=21)(prices)
        except Exception as e:
            logger.exception(f"[AGENT WORKER] quantitative validation unavailable: {e}")

        for t in adopted:
            pos = self.oms.active_positions.get(t)
            if not pos:
                continue
            if pos["side"] == "SELL":
                logger.warning(f"[AGENT WORKER] adopted SHORT {t} rejected (long-only).")
                self.oms.submit_signal(
                    t, "SELL", pos["alloc"],
                    price=pos.get("current_price"),
                    reason="adopted short rejected",
                    source="agent_worker",
                )
                continue
            if weights is not None and t in weights.columns and weights[t].iloc[-1] <= 0:
                logger.warning(f"[AGENT WORKER] adopted LONG {t} failed momentum screen.")
                self.oms.submit_signal(
                    t, "SELL", pos["alloc"],
                    price=pos.get("current_price"),
                    reason="failed momentum screen",
                    source="agent_worker",
                )
                continue
            logger.info(f"[AGENT WORKER] adopted position {t} passed validation.")
            pos["is_adopted"] = False

    # ── LLM revaluation (real inference or nothing) ─────────────────────────

    def process_live_filings(self):
        """Revalue active positions via the local LLM. No LLM → no changes."""
        positions = list(self.oms.active_positions.items())
        if not positions:
            return
        for ticker, pos in positions:
            price = pos.get("current_price")
            if price is None:
                continue
            result = self._llm_revalue(ticker, price, reason=pos.get("broker", ""))
            if result is None:
                logger.info(f"[AGENT WORKER] no LLM verdict for {ticker} — leaving target alone.")
                continue
            multiple, reasoning = result
            self.oms.update_fundamental_target(
                ticker, round(price * multiple, 2), reasoning=f"[LLM] {reasoning}"
            )

    def _llm_revalue(self, ticker: str, price: float, reason: str) -> tuple[float, str] | None:
        """One Ollama call. Returns (fair_value_multiple, reasoning) or None."""
        import requests

        try:
            r = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": _REVALUE_PROMPT.format(ticker=ticker, price=price, reason=reason),
                    "stream": False,
                    "format": "json",
                },
                timeout=60,
            )
            r.raise_for_status()
            parsed = json.loads(r.json().get("response", "{}"))
            multiple = float(parsed["fair_value_multiple"])
            if not 0.5 <= multiple <= 1.5:
                logger.warning(f"[AGENT WORKER] LLM multiple {multiple} out of bounds — ignored.")
                return None
            return multiple, str(parsed.get("reasoning", ""))[:200]
        except Exception as e:
            logger.debug(f"[AGENT WORKER] LLM unavailable/invalid for {ticker}: {e}")
            return None
