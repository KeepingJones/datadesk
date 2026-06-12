"""
Historical backfill: pull daily OHLCV for any tickers we want to trade and
upsert into the canonical history store.

Used by the discovery funnel (new candidate → backfill before shadow tracking)
and for one-off universe expansion.

KNOWN LIMITATION — survivorship bias: yfinance only serves currently-listed
tickers. A 10y backfill through it sees only the survivors, which inflates
backtest returns. Good enough for shadow tracking and live-universe work;
the honest long-history holdout needs a delisted-inclusive source
(EODHD/Tiingo — pending decision, see DESIGN.md §3).
"""

import logging
from pathlib import Path

import pandas as pd
import yfinance as yf

from datadesk.history.store import coverage, save_bars

logger = logging.getLogger(__name__)

DEFAULT_START = "2012-01-01"


def backfill_history(
    tickers: list[str],
    start: str = DEFAULT_START,
    end: str | None = None,
    db_path: Path | None = None,
    batch_size: int = 25,
) -> dict[str, int]:
    """
    Download daily bars for tickers and upsert into the history store.
    Returns {ticker: rows_written}. Tickers that return nothing map to 0 —
    caller decides whether that's an error.
    """
    written: dict[str, int] = dict.fromkeys(tickers, 0)

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            raw = yf.download(
                batch,
                start=start,
                end=end,
                auto_adjust=True,
                progress=False,
                group_by="ticker",
            )
        except Exception as e:
            logger.error(f"backfill: batch download failed ({batch}): {e}")
            continue
        if raw is None or raw.empty:
            logger.warning(f"backfill: no data for batch {batch}")
            continue

        for ticker in batch:
            frame = _extract_ticker_frame(raw, ticker, single=len(batch) == 1)
            if frame is None or frame.empty:
                logger.warning(f"backfill: no data for {ticker}")
                continue
            rows = frame.reset_index()
            df = pd.DataFrame(
                {
                    "ticker": ticker,
                    "date": rows.iloc[:, 0],  # index column: Date
                    "open": rows.get("Open"),
                    "high": rows.get("High"),
                    "low": rows.get("Low"),
                    "close": rows["Close"],
                    "volume": rows.get("Volume"),
                }
            ).dropna(subset=["close"])
            written[ticker] = save_bars(df, source="yahoo_backfill", db_path=db_path)

    logger.info(
        f"backfill complete: {sum(written.values())} rows across "
        f"{sum(1 for v in written.values() if v)} of {len(tickers)} tickers"
    )
    return written


def backfill_missing(
    tickers: list[str],
    min_rows: int = 1000,
    start: str = DEFAULT_START,
    db_path: Path | None = None,
) -> dict[str, int]:
    """
    Backfill only the tickers whose history-store coverage is below min_rows.
    The discovery-funnel entry point: candidates get history before shadow tracking.
    """
    cov = coverage(db_path=db_path)
    have_enough = set(cov[cov["rows"] >= min_rows]["ticker"]) if not cov.empty else set()
    todo = [t for t in tickers if t not in have_enough]
    if not todo:
        logger.info("backfill_missing: all tickers already covered")
        return {}
    logger.info(f"backfill_missing: {len(todo)} of {len(tickers)} tickers need history")
    return backfill_smart(todo, db_path=db_path)

def backfill_smart(
    tickers: list[str],
    db_path: Path | None = None,
) -> dict[str, int]:
    """
    Smart backfill: For each ticker, check the last date we have in the DB.
    Only fetch from that date forward to save bandwidth and time.
    If the ticker has no data, fetch from DEFAULT_START.
    """
    cov = coverage(db_path=db_path)
    cov_dict = {}
    if not cov.empty:
        cov_dict = cov.set_index("ticker")["last"].to_dict()

    written: dict[str, int] = {}
    
    # Group tickers by their required start date to minimize API calls
    # Or for simplicity and robust gap filling, we can fetch individually since 
    # it's just catching up.
    for ticker in tickers:
        last_date = cov_dict.get(ticker)
        start_date = last_date if pd.notna(last_date) else DEFAULT_START
        
        try:
            logger.info(f"Smart backfill for {ticker} starting from {start_date}")
            raw = yf.download(
                ticker,
                start=start_date,
                auto_adjust=True,
                progress=False,
            )
            
            if raw is None or raw.empty:
                logger.warning(f"backfill_smart: no data for {ticker}")
                written[ticker] = 0
                continue
                
            raw = raw.dropna(how="all")
            if raw.empty:
                written[ticker] = 0
                continue
                
            # Normalise MultiIndex columns to flat field names — yfinance returns
            # (field, ticker) for single downloads, (ticker, field) for grouped ones
            if isinstance(raw.columns, pd.MultiIndex):
                if "Close" in raw.columns.get_level_values(0):
                    raw.columns = raw.columns.get_level_values(0)
                else:
                    raw = raw[ticker]
            rows = raw.reset_index()
            close_col = rows["Close"]
            open_col = rows.get("Open")
            high_col = rows.get("High")
            low_col = rows.get("Low")
            vol_col = rows.get("Volume")
            date_col = rows.iloc[:, 0]

            df = pd.DataFrame(
                {
                    "ticker": ticker,
                    "date": date_col,
                    "open": open_col,
                    "high": high_col,
                    "low": low_col,
                    "close": close_col,
                    "volume": vol_col,
                }
            ).dropna(subset=["close"])
            
            w = save_bars(df, source="yahoo_smart_backfill", db_path=db_path)
            written[ticker] = w
            
        except Exception as e:
            logger.error(f"backfill_smart failed for {ticker}: {e}")
            written[ticker] = 0

    return written


def _extract_ticker_frame(raw: pd.DataFrame, ticker: str, single: bool) -> pd.DataFrame | None:
    try:
        if single and not isinstance(raw.columns, pd.MultiIndex):
            return raw.dropna(how="all")
        if ticker in raw.columns.get_level_values(0):
            return raw[ticker].dropna(how="all")
        return None
    except Exception as e:
        logger.warning(f"backfill: parse failed for {ticker}: {e}")
        return None
