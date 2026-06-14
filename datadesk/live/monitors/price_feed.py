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
from typing import TYPE_CHECKING

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
            logger.error(f"[PRICE_FEED] stream loop crashed: {e}")
        finally:
            self._loop.close()

    async def _stream_loop(self):
        from alpaca.data.live import StockDataStream

        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")

        self._stream = StockDataStream(api_key, secret_key)

        async def _on_trade(trade):
            ticker = trade.symbol
            price = float(trade.price)
            self.oms.update_prices(ticker, price)
            self.last_run = trade.timestamp.strftime("%H:%M:%S") if trade.timestamp else "?"

        # Initial subscription
        await self._reconcile_subscriptions(_on_trade)

        # Launch periodic reconcile as a background task
        asyncio.get_event_loop().create_task(self._reconcile_loop(_on_trade))

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
