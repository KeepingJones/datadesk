import logging
import random
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datadesk.live.oms import OMSFastPath

logger = logging.getLogger(__name__)

class AgentWorker:
    """
    Background AI worker that runs continuous local inference (e.g., via Ollama/Phi-3.5)
    to re-evaluate the fundamental targets of active positions.
    Incorporates the 'Situational Awareness' framework for AGI infrastructure stocks.
    """
    def __init__(self, oms: 'OMSFastPath'):
        self.oms = oms
        self.is_running = False
        self.last_run = "Never"
        
        # Situational Awareness / AGI Infrastructure Universe
        self.agi_supply_chain = {
            "NVDA": {"type": "compute_bottleneck", "multiplier": 1.25},
            "TSM": {"type": "fabrication_bottleneck", "multiplier": 1.15},
            "VST": {"type": "energy_constraint", "multiplier": 1.40},
            "CEG": {"type": "energy_constraint", "multiplier": 1.40},
            "MSFT": {"type": "hyperscaler_capex", "multiplier": 1.10},
            "GOOGL": {"type": "hyperscaler_capex", "multiplier": 1.10},
        }

    def start(self):
        self.is_running = True
        logger.info("AgentWorker (Phi-3.5) started. Polling fundamental context...")
        from datetime import datetime

        from datadesk.live.universe import get_active_universe
        
        self.validate_adopted_positions()
        
        while self.is_running:
            focal_stocks = get_active_universe()
            # Randomly pick a stock to re-evaluate based on simulated SEC/News drops
            if random.random() < 0.3:
                self.process_live_filings()
                self.last_run = datetime.now().strftime("%H:%M:%S")
            time.sleep(10)

    def stop(self):
        self.is_running = False

    def validate_adopted_positions(self):
        """Asynchronously validates orphaned Alpaca positions against the historical universe and quantitative models."""
        adopted_tickers = [t for t, p in self.oms.active_positions.items() if p.get("is_adopted")]
        if not adopted_tickers:
            return
            
        logger.info(f"[AGENT WORKER] Validating {len(adopted_tickers)} adopted positions...")
        
        from datadesk.history.store import load_closes
        from datadesk.live.universe import add_ticker, get_active_universe
        from datadesk.strategies.momentum import momentum
        
        universe = set(get_active_universe())
        for t in adopted_tickers:
            if t not in universe:
                logger.info(f"[AGENT WORKER] Adopted ticker {t} not in universe. Adding and backfilling...")
                add_ticker(t)
                
        # Load historical prices for the newly updated universe
        try:
            prices = load_closes(tickers=adopted_tickers)
            if prices.empty:
                logger.warning("[AGENT WORKER] Could not load historical prices for adopted positions.")
                weights = None
            else:
                prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.9)).ffill()
                weights = momentum(lookback=126, top_n=10, skip=21)(prices)
        except Exception as e:
            logger.error(f"[AGENT WORKER] Error running quantitative validation: {e}")
            weights = None
            
        for t in adopted_tickers:
            pos = self.oms.active_positions.get(t)
            if not pos: continue
            
            # 1. Reject all Short positions natively since core strategy is Long-only
            if pos["side"] == "SELL":
                logger.warning(f"[AGENT WORKER] Rejecting adopted SHORT position in {t} (Strategy is Long-Only). Liquidating.")
                self.oms.submit_signal(t, "SELL", pos["alloc"])
                continue
                
            # 2. Reject Longs with negative/flat quantitative momentum
            if weights is not None and t in weights.columns:
                current_weight = weights[t].iloc[-1]
                if current_weight <= 0:
                    logger.warning(f"[AGENT WORKER] Rejecting adopted LONG position in {t} (Failed Momentum check). Liquidating.")
                    self.oms.submit_signal(t, "SELL", pos["alloc"])
                    continue
                    
            # Passed! Assign a baseline fair value so it stays in the portfolio.
            logger.info(f"[AGENT WORKER] Adopted position {t} passed quantitative validation!")
            pos["is_adopted"] = False
            pos["fundamental_fair_value"] = pos["current_price"] * 1.10
    def process_live_filings(self):
        """Simulates the AI parsing an incoming 10-Q or news report."""
        if not self.oms.active_positions:
            return
            
        for ticker, pos in list(self.oms.active_positions.items()):
            # Simulate Ollama Phi-3.5 inference analyzing current price action vs fundamentals
            logger.info(f"[AGENT WORKER] Analyzing unstructured data for {ticker}...")
            time.sleep(1.0) # simulate inference delay
            
            current_price = pos["current_price"]
            
            # Situational Awareness Override
            if ticker in self.agi_supply_chain:
                framework = self.agi_supply_chain[ticker]
                logger.info(f"[AGENT WORKER] Applying Situational Awareness override for {ticker} ({framework['type']})")
                
                # In the Leopold Aschenbrenner framework, AGI infrastructure is structurally mispriced.
                # We project massive out-year EPS growth, so fair value is structurally higher.
                new_fair_value = current_price * framework["multiplier"]
                
                # Add some simulated noise from the "filings"
                noise = random.uniform(-0.05, 0.05)
                new_fair_value *= (1 + noise)
                
                reasoning = f"AGI timeline compression. Structural constraint in {framework['type']}."
                self.oms.update_fundamental_target(ticker, round(new_fair_value, 2), reasoning)
            else:
                # Standard DCF / P/E Revaluation for non-AI stocks
                # Simulate a normal fundamental update (e.g. slight beat or slight miss)
                shift = random.uniform(-0.15, 0.10)
                new_fair_value = current_price * (1 + shift)
                reasoning = "Standard quarterly EPS and margin analysis."
                self.oms.update_fundamental_target(ticker, round(new_fair_value, 2), reasoning)
