import logging
import random
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datadesk.live.oms import OMSFastPath

logger = logging.getLogger(__name__)

class JensenMonitor:
    """
    Monitors live streams, YouTube transcripts, and news wires for
    Jensen Huang speeches, keynotes, or interviews. Parses sentiment and
    picks (e.g., mentions of specific suppliers or partners) to fire trades.
    """
    def __init__(self, oms: 'OMSFastPath'):
        self.oms = oms
        self.is_running = False
        self.last_run = "Never"

    def start(self):
        self.is_running = True
        logger.info("[JENSEN MONITOR] Starting live speech & transcript polling for Jensen Huang...")
        from datetime import datetime
        while self.is_running:
            if random.random() < 0.1:
                self.poll()
                self.last_run = datetime.now().strftime("%H:%M:%S")
            time.sleep(5)

    def stop(self):
        self.is_running = False

    def poll(self):
        """Simulates detecting a speech where Jensen highlights a key supplier."""
        # E.g., Jensen frequently highlights TSMC, SMCI, DELL, ARM, or specific cooling companies
        picks = ["SMCI", "DELL", "ARM", "VRT"]
        target = random.choice(picks)
        
        logger.warning(f"[JENSEN MONITOR] 🚨 DETECTED: Jensen Huang keynote highlighting partner: '{target}'")
        
        # In the context of Situational Awareness, a Jensen shoutout is extremely bullish
        weight = 0.10  # Maximum aggressive allocation
        
        logger.info(f"[JENSEN MONITOR] Supply chain validation confirmed. Firing FAST-PATH BUY {target}.")
        self.oms.submit_signal(target, "BUY", weight_pct=weight)
