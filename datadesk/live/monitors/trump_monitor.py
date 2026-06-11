import time
import logging
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datadesk.live.oms import OMSFastPath

logger = logging.getLogger(__name__)

class TrumpMonitor:
    """
    Polls the CNN Truth Social archive or simulates live event detection.
    Triggers instantaneous fast-path trades.
    """
    def __init__(self, oms: 'OMSFastPath'):
        self.oms = oms
        self.is_running = False

    def start(self):
        self.is_running = True
        logger.info("[TRUMP MONITOR] Starting event polling on truth_archive.json...")

    def stop(self):
        self.is_running = False

    def poll(self):
        """Simulates polling the CNN API and finding a target keyword."""
        # For simulation, we randomly "find" a post about AAPL or TSLA
        tickers = ["AAPL", "TSLA"]
        target = random.choice(tickers)
        
        logger.warning(f"[TRUMP MONITOR] 🚨 BREAKING: Detected mention of '{target}' in Truth Social post!")
        
        # Simulate simple heuristic sentiment analysis
        sentiment = random.choice(["POSITIVE", "NEGATIVE"])
        side = "BUY" if sentiment == "POSITIVE" else "SELL"
        weight = 0.08  # 8% aggressive allocation for event trade
        
        logger.info(f"[TRUMP MONITOR] Sentiment classified as {sentiment}. Firing FAST-PATH {side} {target}.")
        self.oms.submit_signal(target, side, weight_pct=weight)
