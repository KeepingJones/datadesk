"""
Minimal read-only T212 REST client.

Reads T212_MODE (demo/live), T212_{MODE}_API_KEY and T212_{MODE}_API_SECRET
from the environment. All calls are GET-only — no order placement here.
"""

import base64
import logging
import os
import time
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_CACHE_TTL = 60  # seconds


def _build_client() -> tuple[httpx.Client, str]:
    mode = os.getenv("T212_MODE", "demo").lower()
    if mode == "live":
        api_key = os.getenv("T212_LIVE_API_KEY", "")
        api_secret = os.getenv("T212_LIVE_API_SECRET", "")
        base_url = "https://live.trading212.com/api/v0"
    else:
        api_key = os.getenv("T212_DEMO_API_KEY", "")
        api_secret = os.getenv("T212_DEMO_API_SECRET", "")
        base_url = "https://demo.trading212.com/api/v0"

    if not api_key:
        raise RuntimeError(f"T212_{mode.upper()}_API_KEY not set")

    auth = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
    client = httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Basic {auth}"},
        timeout=15.0,
    )
    return client, mode


@dataclass
class T212Cash:
    free: float
    invested: float
    ppl: float
    result: float
    total: float


@dataclass
class T212Position:
    ticker: str
    quantity: float
    avg_price: float
    current_price: float
    ppl: float
    fx_ppl: float | None = None


class T212Client:
    def __init__(self):
        self._client, self.mode = _build_client()
        self._cash_cache: T212Cash | None = None
        self._positions_cache: list[T212Position] | None = None
        self._cash_ts: float = 0.0
        self._positions_ts: float = 0.0

    def _get(self, path: str) -> dict | list:
        r = self._client.get(path)
        r.raise_for_status()
        return r.json()

    def get_cash(self) -> T212Cash:
        now = time.time()
        if self._cash_cache and now - self._cash_ts < _CACHE_TTL:
            return self._cash_cache
        data = self._get("/equity/account/cash")
        self._cash_cache = T212Cash(
            free=float(data.get("free", 0)),
            invested=float(data.get("invested", 0)),
            ppl=float(data.get("ppl", 0)),
            result=float(data.get("result", 0)),
            total=float(data.get("total", 0)),
        )
        self._cash_ts = now
        return self._cash_cache

    def get_portfolio(self) -> list[T212Position]:
        now = time.time()
        if self._positions_cache is not None and now - self._positions_ts < _CACHE_TTL:
            return self._positions_cache
        data = self._get("/equity/portfolio")
        items = data.get("items", data) if isinstance(data, dict) else data
        self._positions_cache = [
            T212Position(
                ticker=p["ticker"],
                quantity=float(p.get("quantity", 0)),
                avg_price=float(p.get("averagePrice", 0)),
                current_price=float(p.get("currentPrice", 0)),
                ppl=float(p.get("ppl", 0)),
                fx_ppl=float(p["fxPpl"]) if p.get("fxPpl") is not None else None,
            )
            for p in items
        ]
        self._positions_ts = now
        return self._positions_cache
