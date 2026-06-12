"""
OMS Fast-Path.
Bypasses the daily batch rebalance to execute intraday event-driven signals immediately,
while strictly enforcing portfolio-level risk limits (Max Position %, Max Daily Drawdown).
"""

import logging
import uuid
from datetime import datetime
from typing import Literal

logger = logging.getLogger(__name__)

# Global store for historic trades (closed positions)
HISTORIC_TRADES = []

CLOSED_POSITIONS = {}
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
        
        # Alpaca Client — requires EXPLICIT arming (DATADESK_ARM_BROKER=1) on top of
        # keys being present. Default is shadow mode: signals are logged, no broker
        # calls. This is the DESIGN §6.2 shadow-first rule; monitors are experimental
        # and must not place real (even paper) orders until Ewan arms them.
        import os
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        armed = os.getenv("DATADESK_ARM_BROKER", "0") == "1"
        self.alpaca = None
        if api_key and secret_key and not armed:
            logger.warning(
                "OMS in SHADOW MODE: Alpaca keys found but DATADESK_ARM_BROKER != 1. "
                "Signals will be logged, not executed."
            )
        if api_key and secret_key and armed:
            from alpaca.trading.client import TradingClient
            self.alpaca = TradingClient(api_key, secret_key, paper=True)
            logger.info("Alpaca Paper Trading Client Initialized successfully.")
            self._adopt_alpaca_positions()
        else:
            logger.warning("ALPACA_API_KEY / SECRET_KEY not found in .env. Falling back to internal mock execution.")
            
    def _adopt_alpaca_positions(self):
        try:
            positions = self.alpaca.get_all_positions()
            account = self.alpaca.get_account()
            equity = float(account.equity)
            for p in positions:
                ticker = p.symbol
                qty = float(p.qty)
                pos_side = "BUY" if qty > 0 else "SELL"
                current_price = float(p.current_price)
                entry_price = float(p.avg_entry_price)
                market_value = float(p.market_value)
                
                alloc = abs(market_value) / equity if equity > 0 else 0.0
                sl_pct = self.default_trailing_stop_pct
                
                if pos_side == "SELL":
                    stop_price = current_price * (1 + sl_pct)
                else:
                    stop_price = current_price * (1 - sl_pct)

                self.active_positions[ticker] = {
                    "id": p.asset_id,
                    "side": pos_side,
                    "alloc": alloc,
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "trailing_stop_pct": sl_pct,
                    "stop_price": stop_price,
                    "fundamental_fair_value": None,
                    "broker": "Alpaca",
                    "exec_ticker": ticker,
                    "is_adopted": True
                }
            if positions:
                logger.info(f"OMS adopted {len(positions)} pre-existing Alpaca positions into internal memory.")
        except Exception as e:
            logger.error(f"Failed to adopt Alpaca positions: {e}")
        
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
        
        # 5. Execute Trade (Alpaca Paper or Mock)
        if self.paper_trading:
            if broker == "Alpaca" and self.alpaca:
                from alpaca.trading.enums import OrderSide, TimeInForce
                from alpaca.trading.requests import MarketOrderRequest
                
                # Are we liquidating an existing position?
                if side == "SELL" and ticker in self.active_positions:
                    try:
                        # Cancel any pending take-profit/stop-loss orders for this symbol first!
                        from alpaca.trading.enums import QueryOrderStatus
                        from alpaca.trading.requests import GetOrdersRequest
                        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[execution_ticker])
                        open_orders = self.alpaca.get_orders(filter=req)
                        for o in open_orders:
                            self.alpaca.cancel_order_by_id(o.id)
                            
                        self.alpaca.close_position(symbol_or_asset_id=execution_ticker)
                        logger.info(f"[Alpaca LIVE PAPER] EXECUTED [LIQUIDATE]: Closed position and cancelled orders for {execution_ticker}")
                    except Exception as e:
                        logger.error(f"[Alpaca LIVE PAPER] Liquidate Failed: {e}")
                        return False
                else:
                    # New position entry
                    try:
                        account = self.alpaca.get_account()
                        equity = float(account.equity)
                        notional = round(equity * weight_pct, 2)
                        
                        alpaca_side = OrderSide.BUY if side == "BUY" else OrderSide.SELL
                        
                        req = MarketOrderRequest(
                            symbol=execution_ticker,
                            notional=notional,
                            side=alpaca_side,
                            time_in_force=TimeInForce.DAY
                        )
                        
                        order = self.alpaca.submit_order(req)
                        order_id = str(order.id)
                        logger.info(f"[Alpaca LIVE PAPER] EXECUTED [{order_id}]: {side} {execution_ticker} (${notional})")
                    except Exception as e:
                        logger.error(f"[Alpaca LIVE PAPER] Order Failed: {e}")
                        return False
            else:
                # Mock execution fallback
                order_id = str(uuid.uuid4())[:8]
                logger.info(f"[{broker}] MOCK PAPER EXECUTED [{order_id}]: {side} {execution_ticker} (Internal: {ticker}, Alloc: {weight_pct*100}%, SL: {sl_pct*100}%)")
            
            # Record state
            if side == "BUY":
                self.active_positions[ticker] = {
                    "id": order_id,
                    "side": side,
                    "alloc": weight_pct,
                    "entry_price": 100.0, # Mocked internally for now
                    "current_price": 100.0,
                    "trailing_stop_pct": sl_pct,
                    "stop_price": 100.0 * (1 - sl_pct),
                    "fundamental_fair_value": None,
                    "broker": broker,
                    "exec_ticker": execution_ticker
                }
                # Record that this position is now active (override any prior closed entry)
                CLOSED_POSITIONS.pop(ticker, None)
            elif side == "SELL" and ticker in self.active_positions:
                # Capture trade details before removal
                pos = self.active_positions[ticker]
                cp = pos.get("current_price", 0.0)
                pnl = (cp - pos.get("entry_price", 0.0)) * pos.get("alloc", 0.0)
                HISTORIC_TRADES.append({
                    "ticker": ticker,
                    "side": "SELL",
                    "close_price": cp,
                    "pnl": pnl,
                    "timestamp": datetime.now().isoformat(),
                })
                del self.active_positions[ticker]
                # Record closure reason for UI status
                CLOSED_POSITIONS[ticker] = "CLOSE (Stop Loss)"
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
            side = pos["side"]
            
            # Fundamental Take-Profit Check
            fair_value = pos.get("fundamental_fair_value")
            if fair_value:
                if (side == "BUY" and current_price >= fair_value) or \
                   (side == "SELL" and current_price <= fair_value):
                    logger.info(f"[FUNDAMENTAL TAKE-PROFIT] {ticker} hit AI fair value target of {fair_value}! Liquidating.")
                    self.submit_signal(ticker, "SELL", pos["alloc"])
                    return
            
            # Update trailing stop
            if side == "BUY":
                new_stop = current_price * (1 - pos["trailing_stop_pct"])
                if new_stop > pos["stop_price"]:
                    pos["stop_price"] = new_stop
                if current_price <= pos["stop_price"]:
                    logger.warning(f"[TRAILING STOP] Triggered for {ticker} at {current_price}")
                    self.submit_signal(ticker, "SELL", pos["alloc"])
            else: # SELL (Short)
                new_stop = current_price * (1 + pos["trailing_stop_pct"])
                if new_stop < pos["stop_price"]:
                    pos["stop_price"] = new_stop
                if current_price >= pos["stop_price"]:
                    logger.warning(f"[TRAILING STOP] Triggered for {ticker} at {current_price}")
                    self.submit_signal(ticker, "SELL", pos["alloc"])
