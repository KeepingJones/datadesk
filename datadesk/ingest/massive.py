import logging
import time
import requests
import pandas as pd
from pathlib import Path
from datadesk.history.store import save_bars
import os

logger = logging.getLogger(__name__)

# Free tier is 5 calls per minute
CALLS_PER_MINUTE_LIMIT = 5
SECONDS_BETWEEN_CALLS = 60.0 / CALLS_PER_MINUTE_LIMIT

def backfill_massive(
    tickers: list[str],
    start: str = "2012-01-01",
    end: str = "2026-06-11",
    db_path: Path | None = None,
) -> dict[str, int]:
    """
    Download daily bars using the Massive (massive.com) free tier.
    Handles survivorship bias by supporting active=false tickers (if known).
    """
    written: dict[str, int] = dict.fromkeys(tickers, 0)
    api_key = os.environ.get("MASSIVE_API_KEY", "DEMO_KEY")
    
    if api_key == "DEMO_KEY":
        logger.warning("MASSIVE_API_KEY not set. Using DEMO_KEY, which will likely fail for non-AAPL tickers.")

    for i, ticker in enumerate(tickers):
        logger.info(f"backfill_massive: fetching {ticker} ({i+1}/{len(tickers)})")
        url = f"https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}?adjusted=true&sort=asc&apiKey={api_key}"
        
        try:
            # Massive API might return 'DELAYED' status, so we need to retry
            max_retries = 3
            data = None
            for attempt in range(max_retries):
                resp = requests.get(url)
                if resp.status_code == 429:
                    logger.warning(f"Rate limit hit on {ticker}. Sleeping for 60s...")
                    time.sleep(60)
                    resp = requests.get(url)
                    
                resp.raise_for_status()
                data = resp.json()
                
                status = data.get("status")
                if status == "DELAYED" and "results" not in data:
                    wait_time = (attempt + 1) * 30
                    logger.warning(f"Massive data DELAYED for {ticker}. Retry {attempt+1}/{max_retries} in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    break
            
            if not data or "results" not in data or not data["results"]:
                logger.warning(f"backfill_massive: no data for {ticker}")
                continue
                
            # Parse Massive results (identical to Polygon schema)
            df = pd.DataFrame(data["results"])
            # Massive fields: v(volume), o(open), c(close), h(high), l(low), t(timestamp ms)
            df["date"] = pd.to_datetime(df["t"], unit="ms")
            df["ticker"] = ticker
            
            # Map columns to schema
            bars = pd.DataFrame({
                "ticker": df["ticker"],
                "date": df["date"],
                "open": df["o"],
                "high": df["h"],
                "low": df["l"],
                "close": df["c"],
                "volume": df["v"],
            })
            
            written[ticker] = save_bars(bars, source="massive_backfill", db_path=db_path)
            
            # Respect free tier rate limits (5 per min -> 12s sleep)
            if i < len(tickers) - 1:
                time.sleep(SECONDS_BETWEEN_CALLS)
                
        except Exception as e:
            logger.error(f"backfill_massive: failed for {ticker}: {e}")
            
    logger.info(
        f"massive backfill complete: {sum(written.values())} rows across "
        f"{sum(1 for v in written.values() if v)} of {len(tickers)} tickers"
    )
    return written
