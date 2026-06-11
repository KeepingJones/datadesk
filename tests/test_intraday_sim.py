"""
Advanced Intraday AI & Event-Driven Trading Test
Simulates live tick data, live event monitors, and background AI fundamental revaluation.
"""
import time
import logging
from datadesk.live.oms import OMSFastPath
from datadesk.live.monitors.agent_worker import AgentWorker
from datadesk.live.monitors.trump_monitor import TrumpMonitor
from datadesk.live.monitors.supply_chain import SupplyChainMonitor
from datadesk.live.monitors.jensen_monitor import JensenMonitor

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

def run_simulation():
    logger.info("Initializing OMS Fast-Path & AI Workers (Hybrid Paper Trading)")
    oms = OMSFastPath(max_position_pct=0.10, default_trailing_stop_pct=0.03)
    
    agent = AgentWorker(oms)
    trump = TrumpMonitor(oms)
    supply = SupplyChainMonitor(oms)
    jensen = JensenMonitor(oms)
    
    agent.start()
    trump.start()
    supply.start()
    jensen.start()
    
    logger.info("\n=== [1] TRUMP SENTIMENT OVERRIDE & AI REVALUATION ===")
    trump.poll() # Fires a random AAPL/TSLA trade
    
    time.sleep(1)
    
    # Simulate price action for the active position
    for t in list(oms.active_positions.keys()):
        oms.update_prices(t, 101.5)
        
    # Run the AI worker loop once to evaluate the new holdings
    logger.info("-> Background AI waking up to process new holdings...")
    agent.process_live_filings()
    
    # Now simulate the price hitting the new fundamental fair value!
    for t, pos in list(oms.active_positions.items()):
        if pos.get("fundamental_fair_value"):
            target = pos["fundamental_fair_value"]
            logger.info(f"-> Simulating massive price spike for {t} to hit AI target of {target}!")
            oms.update_prices(t, target + 0.10)
            
    logger.info("\n=== [2] CROSS-PLATFORM ROUTING & SUPPLY CHAIN ANOMALY ===")
    supply.check_matrix() # Fires TSM (non-US, T212)
    
    time.sleep(1)
    logger.info("\n=== [3] JENSEN HUANG SPEECH MONITOR ===")
    jensen.poll() # Fires a Jensen pick
    
    time.sleep(1)
    
    logger.info("\n=== SIMULATION COMPLETE ===")

if __name__ == "__main__":
    run_simulation()
