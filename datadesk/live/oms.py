"""
OMS Fast-Path.

Executes intraday event-driven signals immediately while enforcing portfolio
risk limits (max position %, max daily loss, trailing stops).

SHADOW-FIRST (DESIGN §6.2): broker calls require BOTH Alpaca keys AND the
explicit DATADESK_ARM_BROKER=1 env flag. Default is shadow mode — every signal
is recorded to the shadow store (would-have audit trail), nothing is executed.
Every signal passes the same risk checks in both modes.
"""

import logging
import os
import threading
import uuid
from datetime import UTC, datetime
from typing import Literal

from datadesk.live import shadow

logger = logging.getLogger(__name__)

# Global stores read by the dashboard
HISTORIC_TRADES = []
CLOSED_POSITIONS = {}


class TickerMapper:
    """Maps internal Yahoo-format tickers (AAPL, ULVR.L) to broker symbols."""

    @staticmethod
    def to_broker(yf_ticker: str, broker: str) -> str:
        if broker in ("Alpaca", "Trading212", "Massive"):
            return yf_ticker.split(".")[0]
        return yf_ticker

    @staticmethod
    def is_us_stock(yf_ticker: str) -> bool:
        return not ("." in yf_ticker and len(yf_ticker.split(".")[-1]) <= 2)


class OMSFastPath:
    def __init__(
        self,
        max_position_pct: float = 0.10,
        max_daily_loss_pct: float = 0.05,
        default_trailing_stop_pct: float = 0.02,
        paper_trading: bool = True,
    ):
        self.max_position_pct = max_position_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.default_trailing_stop_pct = default_trailing_stop_pct
        self.paper_trading = paper_trading

        self._lock = threading.RLock()  # daemons mutate positions from multiple threads
        self.active_positions: dict[str, dict] = {}
        self.daily_starting_nav = 100_000.0
        self.current_nav = 100_000.0
        self.realized_pnl = 0.0

        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        armed = os.getenv("DATADESK_ARM_BROKER", "0") == "1"
        self.alpaca = None
        if api_key and secret_key and armed:
            from alpaca.trading.client import TradingClient

            self.alpaca = TradingClient(api_key, secret_key, paper=True)
            logger.info("OMS ARMED: Alpaca paper client initialized.")
            self._adopt_alpaca_positions()
        elif api_key and secret_key:
            logger.warning(
                "OMS in SHADOW MODE: Alpaca keys found but DATADESK_ARM_BROKER != 1. "
                "Signals will be recorded, not executed."
            )
        else:
            logger.warning("OMS in SHADOW MODE: no Alpaca keys. Signals recorded only.")

    @property
    def is_armed(self) -> bool:
        return self.alpaca is not None

    # ── Position adoption ───────────────────────────────────────────────────

    def _adopt_alpaca_positions(self):
        try:
            positions = self.alpaca.get_all_positions()
            account = self.alpaca.get_account()
            equity = float(account.equity)
            with self._lock:
                for p in positions:
                    qty = float(p.qty)
                    side = "BUY" if qty > 0 else "SELL"
                    current_price = float(p.current_price)
                    sl = self.default_trailing_stop_pct
                    stop = current_price * (1 + sl) if side == "SELL" else current_price * (1 - sl)
                    self.active_positions[p.symbol] = {
                        "id": p.asset_id,
                        "side": side,
                        "alloc": abs(float(p.market_value)) / equity if equity > 0 else 0.0,
                        "entry_price": float(p.avg_entry_price),
                        "current_price": current_price,
                        "trailing_stop_pct": sl,
                        "stop_price": stop,
                        "fundamental_fair_value": None,
                        "broker": "Alpaca",
                        "exec_ticker": p.symbol,
                        "is_adopted": True,
                    }
            if positions:
                logger.info(f"OMS adopted {len(positions)} pre-existing Alpaca positions.")
        except Exception as e:
            logger.error(f"Failed to adopt Alpaca positions: {e}")

    # ── Signal handling ─────────────────────────────────────────────────────

    def submit_signal(
        self,
        ticker: str,
        side: Literal["BUY", "SELL"],
        weight_pct: float,
        stop_loss_pct: float | None = None,
        price: float | None = None,
        reason: str = "",
        source: str = "manual",
    ) -> bool:
        """Process an intraday event signal. `price` is the reference price at
        signal time — required for honest shadow P&L; pass it whenever known."""
        logger.info(f"OMS signal [{source}]: {side} {ticker} weight={weight_pct:.1%} {reason}")

        # 1. Daily-loss kill switch
        daily_loss = (self.current_nav - self.daily_starting_nav) / self.daily_starting_nav
        if daily_loss <= -self.max_daily_loss_pct:
            logger.error(f"OMS HALT: daily loss {daily_loss:.2%} beyond limit. Signal rejected.")
            return False

        # 2. Position size cap
        if weight_pct > self.max_position_pct:
            logger.warning(
                f"OMS risk: weight {weight_pct:.1%} > max {self.max_position_pct:.1%}; truncating."
            )
            weight_pct = self.max_position_pct

        sl_pct = stop_loss_pct if stop_loss_pct else self.default_trailing_stop_pct
        broker = "Alpaca" if TickerMapper.is_us_stock(ticker) else "Trading212"
        execution_ticker = TickerMapper.to_broker(ticker, broker)

        # 3. ALWAYS record to the shadow store — armed or not (audit trail)
        executed = self.is_armed and broker == "Alpaca"
        shadow.record_signal(
            source=source,
            ticker=ticker,
            side=side,
            weight=weight_pct,
            ref_price=price,
            reason=reason,
            executed=executed,
        )

        # 4. Broker execution — armed mode only
        order_id = str(uuid.uuid4())[:8]
        if executed:
            if not self._execute_alpaca(ticker, execution_ticker, side, weight_pct):
                return False
        else:
            logger.info(
                f"[SHADOW] {side} {execution_ticker} via {broker} recorded "
                f"(alloc {weight_pct:.1%}, SL {sl_pct:.1%}, ref_price {price})"
            )

        # 5. Internal book-keeping (both modes — shadow tracks would-have positions)
        with self._lock:
            if side == "BUY":
                self.active_positions[ticker] = {
                    "id": order_id,
                    "side": side,
                    "alloc": weight_pct,
                    "entry_price": price,  # None when unknown — never faked
                    "current_price": price,
                    "trailing_stop_pct": sl_pct,
                    "stop_price": price * (1 - sl_pct) if price else None,
                    "fundamental_fair_value": None,
                    "broker": broker,
                    "exec_ticker": execution_ticker,
                }
                CLOSED_POSITIONS.pop(ticker, None)
            elif side == "SELL" and ticker in self.active_positions:
                pos = self.active_positions[ticker]
                cp, ep = pos.get("current_price"), pos.get("entry_price")
                if cp is not None and ep is not None:
                    direction = 1.0 if pos["side"] == "BUY" else -1.0  # side-aware P&L
                    pnl = direction * (cp - ep) * pos.get("alloc", 0.0)
                else:
                    pnl = None  # unknown entry — no fabricated P&L
                HISTORIC_TRADES.append(
                    {
                        "ticker": ticker,
                        "side": "SELL",
                        "close_price": cp,
                        "pnl": pnl,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
                del self.active_positions[ticker]
                CLOSED_POSITIONS[ticker] = f"CLOSED ({reason or 'signal'})"
                logger.info(f"Position {ticker} closed ({'armed' if executed else 'shadow'}).")

        return True

    def _execute_alpaca(self, ticker, execution_ticker, side, weight_pct) -> bool:
        from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
        from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest

        try:
            if side == "SELL" and ticker in self.active_positions:
                req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[execution_ticker])
                for o in self.alpaca.get_orders(filter=req):
                    self.alpaca.cancel_order_by_id(o.id)
                self.alpaca.close_position(symbol_or_asset_id=execution_ticker)
                logger.info(f"[Alpaca PAPER] LIQUIDATED {execution_ticker}")
            else:
                account = self.alpaca.get_account()
                notional = round(float(account.equity) * weight_pct, 2)
                order = self.alpaca.submit_order(
                    MarketOrderRequest(
                        symbol=execution_ticker,
                        notional=notional,
                        side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
                        time_in_force=TimeInForce.DAY,
                    )
                )
                logger.info(f"[Alpaca PAPER] {side} {execution_ticker} ${notional} ({order.id})")
            return True
        except Exception as e:
            logger.error(f"[Alpaca PAPER] order failed for {execution_ticker}: {e}")
            return False

    # ── Continuous updates ──────────────────────────────────────────────────

    def update_fundamental_target(self, ticker: str, new_fair_value: float, reasoning: str = ""):
        """Called by the AI worker to update a position's thesis target."""
        with self._lock:
            pos = self.active_positions.get(ticker)
            if pos is None:
                return
            old = pos.get("fundamental_fair_value")
            pos["fundamental_fair_value"] = new_fair_value
        logger.info(f"[REVALUATION] {ticker}: {old} -> {new_fair_value}. {reasoning}")

        cp = pos.get("current_price")
        if cp is None:
            return
        # Side-aware thesis break: long broken when FV drops below price,
        # short broken when FV rises above price
        long_broken = pos["side"] == "BUY" and new_fair_value < cp * 0.95
        short_broken = pos["side"] == "SELL" and new_fair_value > cp * 1.05
        if long_broken or short_broken:
            logger.warning(f"[FUNDAMENTAL STOP] {ticker} thesis broken — liquidating.")
            self.submit_signal(
                ticker,
                "SELL",
                pos["alloc"],
                price=cp,
                reason="fundamental stop",
                source="agent_worker",
            )

    def update_prices(self, ticker: str, current_price: float):
        """Tick update: trailing stops + fundamental take-profit."""
        with self._lock:
            pos = self.active_positions.get(ticker)
            if pos is None:
                return
            pos["current_price"] = current_price
            if pos.get("entry_price") is None:
                pos["entry_price"] = current_price  # first observed price after blind entry
            if pos.get("stop_price") is None:
                mult = (
                    1 + pos["trailing_stop_pct"]
                    if pos["side"] == "SELL"
                    else 1 - pos["trailing_stop_pct"]
                )
                pos["stop_price"] = current_price * mult
            side = pos["side"]
            fair_value = pos.get("fundamental_fair_value")

        if fair_value and (
            (side == "BUY" and current_price >= fair_value)
            or (side == "SELL" and current_price <= fair_value)
        ):
            logger.info(f"[TAKE-PROFIT] {ticker} hit fair value {fair_value}.")
            self.submit_signal(
                ticker,
                "SELL",
                pos["alloc"],
                price=current_price,
                reason="take profit",
                source="oms",
            )
            return

        with self._lock:
            pos = self.active_positions.get(ticker)
            if pos is None:
                return
            if side == "BUY":
                new_stop = current_price * (1 - pos["trailing_stop_pct"])
                if new_stop > pos["stop_price"]:
                    pos["stop_price"] = new_stop
                triggered = current_price <= pos["stop_price"]
            else:
                new_stop = current_price * (1 + pos["trailing_stop_pct"])
                if new_stop < pos["stop_price"]:
                    pos["stop_price"] = new_stop
                triggered = current_price >= pos["stop_price"]

        if triggered:
            logger.warning(f"[TRAILING STOP] {ticker} at {current_price}")
            self.submit_signal(
                ticker,
                "SELL",
                pos["alloc"],
                price=current_price,
                reason="trailing stop",
                source="oms",
            )
