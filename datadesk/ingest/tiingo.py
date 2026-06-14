"""
Tiingo Historical EOD Data Client.

Handles pulling historical daily prices from Tiingo and mapping
them to our history.db format to correct survivorship bias.
"""

import logging
import time

import httpx
import pandas as pd

from datadesk.config import TIINGO_API_KEY

logger = logging.getLogger(__name__)

class TiingoRateLimitExceeded(Exception):
    pass

def fetch_tiingo_prices(ticker: str, start_date: str) -> pd.DataFrame | None:
    """
    Fetch historical EOD prices from Tiingo for a given ticker.
    Returns a dataframe formatted exactly like our history.db daily_bars table.
    
    If it hits a 429 Too Many Requests, it raises TiingoRateLimitExceeded.
    """
    if not TIINGO_API_KEY:
        logger.warning("fetch_tiingo_prices: TIINGO_API_KEY not configured.")
        return None

    url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices"
    params = {
        "startDate": start_date,
        "token": TIINGO_API_KEY,
        "format": "json",
    }
    
    # Simple retry for transient network issues, but fast-fail on 429 to trigger fallback
    for attempt in range(3):
        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.get(url, params=params)
                
            if r.status_code == 429:
                raise TiingoRateLimitExceeded(f"Tiingo hourly rate limit reached at {ticker}.")
            
            if r.status_code == 404:
                logger.warning(f"Tiingo: {ticker} not found.")
                return None
                
            r.raise_for_status()
            data = r.json()
            
            if not data:
                return None
                
            df = pd.DataFrame(data)
            
            # Map Tiingo's adjusted fields to our canonical fields
            # Tiingo returns date like "2021-01-04T00:00:00.000Z"
            # We must map adjClose, adjHigh, adjLow, adjOpen, adjVolume
            mapped = pd.DataFrame({
                "ticker": ticker,
                "date": pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
                "open": df.get("adjOpen", df.get("open")),
                "high": df.get("adjHigh", df.get("high")),
                "low": df.get("adjLow", df.get("low")),
                "close": df.get("adjClose", df.get("close")),
                "volume": df.get("adjVolume", df.get("volume", 0)),
            })
            
            return mapped.dropna(subset=["close"])
            
        except TiingoRateLimitExceeded:
            raise
        except Exception as e:
            if attempt == 2:
                logger.exception(f"Tiingo fetch failed for {ticker}: {e}")
                return None
            time.sleep(1.0)
            
    return None
