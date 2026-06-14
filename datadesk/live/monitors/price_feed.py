"""
Live Price Feed — Alpaca websocket → OMS trailing stops.

Subscribes to Alpaca's real-time stock data stream for all tickers
currently held in the OMS. When a new trade (last-price) arrives,
it calls oms.update_prices(ticker, price) which checks trailing stops
and fundamental take-profits.

Subscription is dynamic: every 60s the feed reconciles its active
subscriptions against the current OMS positions, adding/dropping as needed.

Requires: ALPACA_API_KEY + ALPACA_SECRET_KEY environment variables.
Falls back gracefully if keys are missing — OMS just won't receive ticks.

Non-US tickers (anything with a ".L", ".DE" etc. suffix) are skipped;
Alpaca SIP/IEX only covers US-listed equities.
"""

import asyncio
import logging
import os
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from datadesk.db import save_live_prices

if TYPE_CHECKING:
    from datadesk.live.oms import OMSFastPath

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL = 60  # seconds between subscription reconcile


class PriceFeed:
    def __init__(self, oms: "OMSFastPath"):
        self.oms = oms
        self.is_running = False
        self.last_run = "Never"
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream = None
        self._subscribed: set[str] = set()
        
        # 1-minute snapshot queue for saving live prices
        self._last_saved: dict[str, float] = {}
        self._price_queue: asyncio.Queue | None = None

    def start(self):
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            logger.warning("[PRICE_FEED] no Alpaca keys — price feed disabled")
            return

        self.is_running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="price_feed")
        self._thread.start()
        logger.info("[PRICE_FEED] started — live Alpaca websocket")

    def stop(self):
        self.is_running = False
        if self._loop and self._stream:
            asyncio.run_coroutine_threadsafe(self._stream.stop_ws(), self._loop)
        logger.info("[PRICE_FEED] stopped")

    def _run_loop(self):
        """Runs the asyncio event loop in its own thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._stream_loop())
        except Exception as e:
            logger.exception(f"[PRICE_FEED] stream loop crashed: {e}")
        finally:
            self._loop.close()

    async def _stream_loop(self):
        from alpaca.data.live import StockDataStream

        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")

        self._stream = StockDataStream(api_key, secret_key)
        self._price_queue = asyncio.Queue()

        async def _on_trade(trade):
            ticker = trade.symbol
            price = float(trade.price)
            self.oms.update_prices(ticker, price)
            
            # Throttle DB snapshots to 1 minute per ticker
            now = time.time()
            if now - self._last_saved.get(ticker, 0) >= 60:
                self._last_saved[ticker] = now
                ts_iso = trade.timestamp.isoformat() if trade.timestamp else datetime.now(UTC).isoformat()
                self._price_queue.put_nowait((ts_iso, ticker, price))
                
            self.last_run = trade.timestamp.strftime("%H:%M:%S") if trade.timestamp else "?"

        # Initial subscription
        await self._reconcile_subscriptions(_on_trade)

        # Launch background tasks
        loop = asyncio.get_event_loop()
        loop.create_task(self._reconcile_loop(_on_trade))
        loop.create_task(self._db_writer_loop())
        
        if self.oms.t212 is not None:
            loop.create_task(self._t212_polling_loop())

        # Block until stream closes
        await self._stream._run_forever()

    async def _reconcile_loop(self, handler):
        """Periodically sync websocket subscriptions to OMS positions."""
        while self.is_running:
            await asyncio.sleep(RECONCILE_INTERVAL)
            try:
                await self._reconcile_subscriptions(handler)
            except Exception as e:
                logger.debug(f"[PRICE_FEED] reconcile error: {e}")

    async def _reconcile_subscriptions(self, handler):
        """Add/remove subscriptions to match current OMS positions (US only)."""
        with self.oms._lock:
            held = set(self.oms.active_positions.keys())

        # Only subscribe to US tickers (no exchange suffix)
        us_tickers = {t for t in held if "." not in t}

        to_add = us_tickers - self._subscribed
        to_drop = self._subscribed - us_tickers

        if to_add:
            logger.info(f"[PRICE_FEED] subscribing: {sorted(to_add)}")
            self._stream.subscribe_trades(handler, *to_add)
            self._subscribed |= to_add

        if to_drop:
            logger.info(f"[PRICE_FEED] unsubscribing: {sorted(to_drop)}")
            self._stream.unsubscribe_trades(*to_drop)
            self._subscribed -= to_drop

    async def _db_writer_loop(self):
        """Drains the snapshot queue in batches and writes to SQLite."""
        batch = []
        while self.is_running:
            try:
                # Wait for at least one item
                item = await asyncio.wait_for(self._price_queue.get(), timeout=5.0)
                batch.append(item)
                
                # Drain the rest of the queue if available
                while not self._price_queue.empty() and len(batch) < 1000:
                    batch.append(self._price_queue.get_nowait())
                
                # Write batch to DB
                if batch:
                    # Run DB I/O in a thread to not block the asyncio loop
                    await asyncio.to_thread(save_live_prices, batch)
                    batch.clear()
            except asyncio.TimeoutError:
                if batch:
                    await asyncio.to_thread(save_live_prices, batch)
                    batch.clear()
            except Exception as e:
                logger.exception(f"[PRICE_FEED] db writer error: {e}")
                await asyncio.sleep(1)

    async def _t212_polling_loop(self):
        """Polls T212 portfolio every 60s for non-US stock 1-min snapshots."""
        logger.info("[PRICE_FEED] T212 60s polling loop started.")
        while self.is_running:
            try:
                # We do the HTTP request in a thread so we don't block the Alpaca websocket!
                portfolio = await asyncio.to_thread(self.oms.t212.get_portfolio)
                
                with self.oms._lock:
                    active_t212_positions = {
                        ticker: pos 
                        for ticker, pos in self.oms.active_positions.items()
                        if pos["broker"] == "Trading212"
                    }
                
                if portfolio:
                    now_iso = datetime.now(UTC).isoformat()
                    
                    # 1. Store ALL portfolio items as a generic market data feed
                    for p in portfolio:
                        self._price_queue.put_nowait((now_iso, p.ticker, p.current_price))
                    
                    # 2. Update OMS for active positions (Original Logic)
                    if active_t212_positions:
                        t212_ticker_to_price = {p.ticker: p.current_price for p in portfolio}
                        
                        for ticker, pos in active_t212_positions.items():
                            exec_ticker = pos["exec_ticker"]
                            if exec_ticker in t212_ticker_to_price:
                                price = t212_ticker_to_price[exec_ticker]
                                self.oms.update_prices(ticker, price)
                                # Put directly into DB snapshot queue under the internal Yahoo ticker
                                self._price_queue.put_nowait((now_iso, ticker, price))
                                
                    self.last_run = datetime.now().strftime("%H:%M:%S")
            except Exception as e:
                logger.exception(f"[PRICE_FEED] T212 polling error: {e}")
            
            # T212 cache is 60s, no point polling faster.
            await asyncio.sleep(60)
