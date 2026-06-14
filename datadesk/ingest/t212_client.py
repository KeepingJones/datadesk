"""
T212 REST client — read and order-placement.

Reads T212_MODE (demo/live), T212_{MODE}_API_KEY from the environment.
Demo mode uses paper money (demo.trading212.com) so it is safe to test against.

T212 ticker format: {SYMBOL}_{COUNTRY_ISO}_EQ  e.g. ULVR_GB_EQ, AAPL_US_EQ
Use resolve_ticker() to convert from yfinance-style symbols.
"""

import logging
import os
import time
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_CACHE_TTL = 60  # seconds


# yfinance suffix → T212 country code
_SUFFIX_COUNTRY: dict[str, str] = {
    ".L":  "GB",
    ".DE": "DE",
    ".PA": "FR",
    ".AS": "NL",
    ".MI": "IT",
    ".MC": "ES",
    ".BR": "BE",
    ".HK": "HK",
    ".T":  "JP",
    ".KS": "KR",
    ".AX": "AU",
}


def resolve_ticker(yf_ticker: str) -> str:
    """Convert yfinance ticker to T212 instrument ticker.

    AAPL        → AAPL_US_EQ
    ULVR.L      → ULVR_GB_EQ
    BMW.DE      → BMW_DE_EQ
    SAP.PA      → SAP_FR_EQ

    This is a best-effort heuristic. If T212 uses a different code for a
    specific instrument, add it to the override dict below.
    """
    _OVERRIDES: dict[str, str] = {
        # Add manual overrides if T212 uses a non-standard code
    }
    if yf_ticker in _OVERRIDES:
        return _OVERRIDES[yf_ticker]

    for suffix, country in _SUFFIX_COUNTRY.items():
        if yf_ticker.upper().endswith(suffix.upper()):
            symbol = yf_ticker[: -len(suffix)].upper()
            return f"{symbol}_{country}_EQ"

    # No suffix → assume US equity
    return f"{yf_ticker.upper()}_US_EQ"


def _build_client() -> tuple[httpx.Client, str]:
    mode = os.getenv("T212_MODE", "demo").lower()
    if mode == "live":
        api_key = os.getenv("T212_LIVE_API_KEY", "")
        base_url = "https://live.trading212.com/api/v0"
    else:
        api_key = os.getenv("T212_DEMO_API_KEY", "")
        base_url = "https://demo.trading212.com/api/v0"

    if not api_key:
        raise RuntimeError(f"T212_{mode.upper()}_API_KEY not set")

    client = httpx.Client(
        base_url=base_url,
        headers={"Authorization": api_key},
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

    def _post(self, path: str, body: dict) -> dict:
        r = self._client.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> None:
        r = self._client.delete(path)
        r.raise_for_status()

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

    def get_equity(self) -> float:
        """Total portfolio value (cash + invested)."""
        cash = self.get_cash()
        return cash.total

    def place_market_order(self, yf_ticker: str, notional_gbp: float) -> dict:
        """
        Buy `notional_gbp` worth of `yf_ticker` at market price.
        Converts yfinance ticker to T212 format (ULVR.L → ULVR_GB_EQ).
        T212 market orders are fractional — no need to round to whole shares.
        """
        t212_ticker = resolve_ticker(yf_ticker)
        body = {
            "ticker": t212_ticker,
            "value": round(notional_gbp, 2),
            "timeValidity": "DAY",
        }
        result = self._post("/equity/orders/value", body)
        logger.info(f"[T212 {self.mode.upper()}] BUY {t212_ticker} £{notional_gbp:.2f} → {result}")
        return result

    def close_position(self, yf_ticker: str) -> None:
        """Close (sell) entire position in `yf_ticker`."""
        t212_ticker = resolve_ticker(yf_ticker)
        try:
            self._delete(f"/equity/portfolio/{t212_ticker}")
            logger.info(f"[T212 {self.mode.upper()}] CLOSED {t212_ticker}")
        except Exception as e:
            logger.error(f"[T212 {self.mode.upper()}] close failed for {t212_ticker}: {e}")
            raise

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
