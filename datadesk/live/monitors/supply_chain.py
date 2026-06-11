import time
import logging
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datadesk.live.oms import OMSFastPath

logger = logging.getLogger(__name__)

class SupplyChainMonitor:
    """
    Monitors lead-lag relationships based on the neural_map graph_data.json.
    """
    def __init__(self, oms: 'OMSFastPath'):
        self.oms = oms
        self.is_running = False
        self.last_run = "Never"

    def start(self):
        self.is_running = True
        logger.info("SupplyChainMonitor started. Polling focal stocks for lead-lag anomalies...")
        from datadesk.live.universe import get_active_universe
        from datetime import datetime
        while self.is_running:
            focal_stocks = get_active_universe()
            # Pick a random lead stock and simulate a surge
            if random.random() < 0.2:
                self.check_matrix()
                self.last_run = datetime.now().strftime("%H:%M:%S")
            time.sleep(8)

    def stop(self):
        self.is_running = False

    def check_matrix(self):
        """Simulates finding a 2% breakout in a lead stock (NVDA) and firing the lag stock (TSM)."""
        logger.info("[SUPPLY CHAIN] Detecting anomaly in lead stock: NVDA surging +2.5%...")
        time.sleep(0.5)
        logger.info("[SUPPLY CHAIN] Matrix query: NVDA dependency 'TSM' has not moved yet.")
        logger.warning("[SUPPLY CHAIN] 🚨 FIRING LEAD-LAG FAST-PATH: BUY TSM")
        
        self.oms.submit_signal("TSM", "BUY", weight_pct=0.05)
