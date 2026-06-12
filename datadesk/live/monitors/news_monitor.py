import logging
import random
import time
from datetime import datetime

from datadesk.live.universe import get_active_universe

logger = logging.getLogger(__name__)

class NewsMonitor:
    def __init__(self):
        self.running = False
        self.last_run = "Never"
        
    def start(self):
        self.running = True
        logger.info("NewsMonitor started. Polling global geopolitics and universe news...")
        while self.running:
            universe = get_active_universe()
            # Simulated news fetching
            events = [
                "Geopolitics: Tensions in South China Sea impacting semiconductor supply lines.",
                "Macro: Federal Reserve hints at 50bps rate cut.",
                f"{random.choice(universe)}: Q3 Earnings show massive AI infrastructure cap-ex."
            ]
            
            for event in events:
                if not self.running: break
                logger.info(f"[NEWS] {event}")
                # Here we would normally pass this to Phi-3.5 for sentiment scoring
                time.sleep(2)
                self.last_run = datetime.now().strftime("%H:%M:%S")
                
            time.sleep(15)
            
    def stop(self):
        self.running = False
        logger.info("NewsMonitor stopped.")
