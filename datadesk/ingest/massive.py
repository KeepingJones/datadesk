"""
Massive (massive.com) Historical EOD Data Client.

Used as a Tier-2 fallback source after Tiingo limits are exhausted,
before defaulting to yfinance.
"""

import logging
import os
import time
from datetime import datetime

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# Free tier is 5 calls per minute -> 12 seconds between calls
CALLS_PER_MINUTE_LIMIT = 5
SECONDS_BETWEEN_CALLS = 60.0 / CALLS_PER_MINUTE_LIMIT

class MassiveRateLimitExceeded(Exception):
    pass

def fetch_massive_prices(ticker: str, start_date: str, end_date: str | None = None) -> pd.DataFrame | None:
    """
    Fetch historical EOD prices from Massive for a given ticker.
    Returns a dataframe formatted exactly like our history.db daily_bars table.
    
    If it hits a 429 Too Many Requests, it raises MassiveRateLimitExceeded.
    Enforces a strict 12-second sleep to respect the 5 calls/min limit.
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "DEMO_KEY")
    if api_key == "DEMO_KEY":
        logger.warning("fetch_massive_prices: MASSIVE_API_KEY not set. Using DEMO_KEY.")

    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    url = f"https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}?adjusted=true&sort=asc&apiKey={api_key}"

    max_retries = 3
    data = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=30.0)
            
            if resp.status_code == 429:
                logger.warning(f"Massive rate limit hit on {ticker}. Cooling down for 60s before raising exception.")
                time.sleep(60)
                raise MassiveRateLimitExceeded(f"Massive daily/hourly limit reached at {ticker}.")
                
            resp.raise_for_status()
            data = resp.json()

            status = data.get("status")
            if status == "DELAYED" and "results" not in data:
                wait_time = (attempt + 1) * 30
                logger.warning(f"Massive data DELAYED for {ticker}. Retry {attempt + 1}/{max_retries} in {wait_time}s...")
                time.sleep(wait_time)
                continue
            else:
                break
                
        except MassiveRateLimitExceeded:
            raise
        except Exception as e:
            if attempt == max_retries - 1:
                logger.exception(f"fetch_massive_prices: failed for {ticker}: {e}")
                time.sleep(SECONDS_BETWEEN_CALLS)
                return None
            time.sleep(5.0)

    # Mandatory sleep to respect the 5 requests/minute free-tier limit
    logger.debug(f"Massive API sleeping for {SECONDS_BETWEEN_CALLS}s to respect rate limit...")
    time.sleep(SECONDS_BETWEEN_CALLS)

    if not data or "results" not in data or not data["results"]:
        return None

    df = pd.DataFrame(data["results"])
    # Massive fields: v(volume), o(open), c(close), h(high), l(low), t(timestamp ms)
    df["date"] = pd.to_datetime(df["t"], unit="ms").dt.strftime("%Y-%m-%d")
    df["ticker"] = ticker

    mapped = pd.DataFrame({
        "ticker": df["ticker"],
        "date": df["date"],
        "open": df["o"],
        "high": df["h"],
        "low": df["l"],
        "close": df["c"],
        "volume": df["v"],
    })

    return mapped.dropna(subset=["close"])
