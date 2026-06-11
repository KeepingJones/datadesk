"""
OMS Fast-Path.
Bypasses the daily batch rebalance to execute intraday event-driven signals immediately,
while strictly enforcing portfolio-level risk limits (Max Position %, Max Daily Drawdown).
"""

import logging
import uuid
import time
from typing import Literal

logger = logging.getLogger(__name__)

class TickerMapper:
    """
    Standardizes ticker mapping across platforms.
    Internal System uses Yahoo Finance format (e.g., ULVR.L, AAPL, SIE.DE) to share tick/static data.
    """
    @staticmethod
    def to_broker(yf_ticker: str, broker: str) -> str:
        if broker == "Alpaca":
            return yf_ticker.split('.')[0] # AAPL -> AAPL
        elif broker == "Trading212":
            # T212 uses bare tickers mapped to specific exchanges
            return yf_ticker.split('.')[0] # ULVR.L -> ULVR (LSE)
        elif broker == "Massive":
            return yf_ticker.split('.')[0]
        return yf_ticker

    @staticmethod
    def is_us_stock(yf_ticker: str) -> bool:
        if "." in yf_ticker and len(yf_ticker.split(".")[-1]) <= 2:
            return False
        return True

class OMSFastPath:
    def __init__(
        self,
        max_position_pct: float = 0.10,
        max_daily_loss_pct: float = 0.05,
        default_trailing_stop_pct: float = 0.02,
        paper_trading: bool = True
    ):
        self.max_position_pct = max_position_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.default_trailing_stop_pct = default_trailing_stop_pct
        self.paper_trading = paper_trading
        
        # State
        self.active_positions = {}
        self.daily_starting_nav = 100_000.0  # Mock starting NAV
        self.current_nav = 100_000.0
        self.realized_pnl = 0.0
        
    def submit_signal(self, ticker: str, side: Literal["BUY", "SELL"], weight_pct: float, stop_loss_pct: float = None):
        """Processes an intraday event signal."""
        logger.info(f"OMS received FAST-PATH signal: {side} {ticker} (Weight: {weight_pct*100}%)")
        
        # 1. Global Kill Switch Check
        current_daily_loss = (self.current_nav - self.daily_starting_nav) / self.daily_starting_nav
        if current_daily_loss <= -self.max_daily_loss_pct:
            logger.error(f"OMS HALT: Daily loss limit exceeded ({current_daily_loss:.2%}). Signal rejected.")
            return False
            
        # 2. Position Limit Check
        if weight_pct > self.max_position_pct:
            logger.warning(f"OMS Risk: Signal weight {weight_pct:.1%} exceeds max {self.max_position_pct:.1%}. Truncating.")
            weight_pct = self.max_position_pct
            
        # 3. Apply Default Stop Loss if none provided
        sl_pct = stop_loss_pct if stop_loss_pct else self.default_trailing_stop_pct
        
        # 4. Hybrid Broker Routing & Cross-Platform Mapping
        broker = "Alpaca" if TickerMapper.is_us_stock(ticker) else "Trading212"
        execution_ticker = TickerMapper.to_broker(ticker, broker)
        
        # 5. Execute Trade (Mock via Alpaca/T212 Paper)
        order_id = str(uuid.uuid4())[:8]
        if self.paper_trading:
            logger.info(f"[{broker}] PAPER TRADE EXECUTED [{order_id}]: {side} {execution_ticker} (Internal: {ticker}, Alloc: {weight_pct*100}%, SL: {sl_pct*100}%)")
            
            if side == "BUY":
                self.active_positions[ticker] = {
                    "id": order_id,
                    "side": side,
                    "alloc": weight_pct,
                    "entry_price": 100.0, # Mock price
                    "current_price": 100.0,
                    "trailing_stop_pct": sl_pct,
                    "stop_price": 100.0 * (1 - sl_pct),
                    "fundamental_fair_value": None,
                    "broker": broker,
                    "exec_ticker": execution_ticker
                }
            elif side == "SELL" and ticker in self.active_positions:
                del self.active_positions[ticker]
                logger.info(f"Position {ticker} liquidated via {broker}.")
                
        return True
        
    def update_fundamental_target(self, ticker: str, new_fair_value: float, reasoning: str = ""):
        """Called by the AI Background Worker to dynamically update the thesis target."""
        if ticker in self.active_positions:
            pos = self.active_positions[ticker]
            old_value = pos.get("fundamental_fair_value")
            pos["fundamental_fair_value"] = new_fair_value
            logger.info(f"[AI REVALUATION] {ticker} Fair Value updated: {old_value} -> {new_fair_value}. Reason: {reasoning}")
            
            # Fundamental Stop-Loss: If new fair value crashes below current price significantly
            if new_fair_value < pos["current_price"] * 0.95:
                logger.warning(f"[FUNDAMENTAL STOP-LOSS] {ticker} thesis broken! Liquidating.")
                self.submit_signal(ticker, "SELL", pos["alloc"])
        
    def update_prices(self, ticker: str, current_price: float):
        """Simulate tick-by-tick price updates to process trailing stops and fundamental take-profits."""
        if ticker in self.active_positions:
            pos = self.active_positions[ticker]
            pos["current_price"] = current_price
            
            # Fundamental Take-Profit Check
            fair_value = pos.get("fundamental_fair_value")
            if fair_value and current_price >= fair_value:
                logger.info(f"[FUNDAMENTAL TAKE-PROFIT] {ticker} hit AI fair value target of {fair_value}! Liquidating.")
                self.submit_signal(ticker, "SELL", pos["alloc"])
                return
            
            # Update trailing stop if price goes up
            new_stop = current_price * (1 - pos["trailing_stop_pct"])
            if new_stop > pos["stop_price"]:
                pos["stop_price"] = new_stop
                
            # Trigger Trailing Stop Loss
            if current_price <= pos["stop_price"]:
                logger.warning(f"[TRAILING STOP] Triggered for {ticker} at {current_price}")
                self.submit_signal(ticker, "SELL", pos["alloc"])
