"""
Jensen Huang keynote monitor — PARKED (claude-review-2026-06-11, open issue 4).

There is no wired data source for live keynote/transcript detection, and the
previous implementation fired random trades. Until a real source exists
(e.g., YouTube transcript polling with timestamps), this monitor does nothing.
The class is kept so the dashboard's daemon panel stays stable.
"""

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datadesk.live.oms import OMSFastPath

logger = logging.getLogger(__name__)


class JensenMonitor:
    def __init__(self, oms: "OMSFastPath"):
        self.oms = oms
        self.is_running = False
        self.last_run = "PARKED"

    def start(self):
        self.is_running = True
        logger.info("[JENSEN MONITOR] PARKED — no real data source wired; emitting nothing.")
        while self.is_running:
            self.last_run = f"PARKED ({datetime.now().strftime('%H:%M:%S')})"
            for _ in range(60):
                if not self.is_running:
                    return
                time.sleep(1)

    def stop(self):
        self.is_running = False

    def poll(self) -> int:
        """No data source — never emits signals."""
        logger.info("[JENSEN MONITOR] poll ignored — monitor is parked (no data source).")
        return 0
