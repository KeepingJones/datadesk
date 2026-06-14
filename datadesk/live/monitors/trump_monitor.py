"""
Trump Truth Social monitor — REAL data path, no simulation.

Polls the CNN archive via the existing collector (datadesk.ingest.trump), which
only inserts posts it hasn't seen. New posts go through the deterministic
classifier; actionable classes emit signals to the OMS, which records them to
the shadow store (and only executes if the broker is explicitly armed).
"""

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from datadesk.ai.post_classifier import classify_post
from datadesk.ingest.trump import collect, load_posts

if TYPE_CHECKING:
    from datadesk.live.oms import OMSFastPath

logger = logging.getLogger(__name__)

POLL_SECONDS = 300  # archive is ~18MB; the collector is delta-aware but be polite
EVENT_WEIGHT = 0.05  # 5% per event signal — below the 10% OMS cap
POST_BATCH_CAP = 0.10  # max total allocation committed from a single post


class TrumpMonitor:
    def __init__(self, oms: "OMSFastPath"):
        self.oms = oms
        self.is_running = False
        self.last_run = "Never"
        self._seen_ids: set[str] = set()
        self._primed = False

    def start(self):
        self.is_running = True
        logger.info("[TRUMP MONITOR] polling CNN archive every %ss", POLL_SECONDS)
        while self.is_running:
            try:
                self.poll()
            except Exception as e:
                logger.exception(f"[TRUMP MONITOR] poll failed: {e}")
            self.last_run = datetime.now().strftime("%H:%M:%S")
            for _ in range(POLL_SECONDS):
                if not self.is_running:
                    return
                time.sleep(1)

    def stop(self):
        self.is_running = False

    def poll(self) -> int:
        """Fetch archive, classify NEW posts only, emit signals. Returns signals fired."""
        new_count = collect()
        posts = load_posts()
        if posts.empty:
            return 0

        if not self._primed:
            # First poll: prime on existing corpus — never trade historical posts
            self._seen_ids = set(posts["id"])
            self._primed = True
            logger.info(f"[TRUMP MONITOR] primed on {len(self._seen_ids)} historical posts")
            return 0

        fresh = posts[~posts["id"].isin(self._seen_ids)]
        self._seen_ids.update(posts["id"])
        if fresh.empty:
            return 0

        fired = 0
        for _, post in fresh.iterrows():
            c = classify_post(post["content"])
            if c.impact_class == "NOISE":
                continue
            logger.warning(
                f"[TRUMP MONITOR] {c.impact_class} ({c.sentiment}, conf {c.confidence}): "
                f"{post['content'][:120]}"
            )
            if c.impact_class == "MACRO_COMMENTARY":
                continue  # index/vol overlay territory, not a single-stock fast-path trade
            side = "BUY" if c.sentiment == "POSITIVE" else "SELL"
            batch_used = 0.0
            for ticker in c.actionable_tickers:
                remaining = POST_BATCH_CAP - batch_used
                if remaining <= 0:
                    logger.warning(
                        f"[TRUMP MONITOR] post batch cap reached ({POST_BATCH_CAP:.0%}); "
                        f"skipping remaining tickers: {c.actionable_tickers[c.actionable_tickers.index(ticker):]}"
                    )
                    break
                signal_weight = min(EVENT_WEIGHT, remaining)
                self.oms.submit_signal(
                    ticker,
                    side,
                    weight_pct=signal_weight,
                    reason=f"{c.impact_class}: {post['content'][:80]}",
                    source="trump_monitor",
                )
                batch_used += signal_weight
                fired += 1
        logger.info(f"[TRUMP MONITOR] {new_count} new posts, {fired} signals")
        return fired
