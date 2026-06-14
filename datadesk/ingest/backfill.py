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

DEFAULT_START = "1980-01-01"


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
    from datadesk.ingest.tiingo import fetch_tiingo_prices, TiingoRateLimitExceeded
    from datadesk.ingest.massive import fetch_massive_prices, MassiveRateLimitExceeded
    
    tiingo_limit_hit = False
    massive_limit_hit = False
    
    written: dict[str, int] = dict.fromkeys(tickers, 0)

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        missing_from_yf = []
        
        # 1. Try yfinance first (fastest, bulk download, max history)
        try:
            raw = yf.download(
                batch,
                start=start,
                end=end,
                auto_adjust=True,
                progress=False,
                group_by="ticker",
            )
            
            if raw is not None and not raw.empty:
                for ticker in batch:
                    frame = _extract_ticker_frame(raw, ticker, single=len(batch) == 1)
                    if frame is None or frame.empty:
                        missing_from_yf.append(ticker)
                        continue
                        
                    rows = frame.reset_index()
                    df = pd.DataFrame(
                        {
                            "ticker": ticker,
                            "date": rows.iloc[:, 0],
                            "open": rows.get("Open"),
                            "high": rows.get("High"),
                            "low": rows.get("Low"),
                            "close": rows["Close"],
                            "volume": rows.get("Volume"),
                        }
                    ).dropna(subset=["close"])
                    
                    if not df.empty:
                        written[ticker] = save_bars(df, source="yahoo_primary", db_path=db_path)
                    else:
                        missing_from_yf.append(ticker)
            else:
                missing_from_yf = batch.copy()
        except Exception as e:
            logger.exception(f"backfill: yfinance batch download failed ({batch}): {e}")
            missing_from_yf = batch.copy()

        # 2. Fallback to Tiingo/Massive for any tickers yfinance missed
        for ticker in missing_from_yf:
            df = None
            if not tiingo_limit_hit:
                try:
                    df = fetch_tiingo_prices(ticker, start)
                except TiingoRateLimitExceeded:
                    logger.warning(f"Tiingo limit hit on {ticker}. Falling back to Massive.")
                    tiingo_limit_hit = True
                    
            if df is None and not massive_limit_hit:
                try:
                    df = fetch_massive_prices(ticker, start, end)
                except MassiveRateLimitExceeded:
                    logger.warning(f"Massive limit hit on {ticker}.")
                    massive_limit_hit = True
            
            if df is not None and not df.empty:
                written[ticker] = save_bars(df, source="tier_1_2_fallback", db_path=db_path)
            else:
                logger.warning(f"backfill: no data found for {ticker} across all sources")

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
    Groups tickers by start_date and leverages the bulk yfinance engine.
    """
    cov = coverage(db_path=db_path)
    cov_dict = {}
    if not cov.empty:
        cov_dict = cov.set_index("ticker")["last"].to_dict()

    written: dict[str, int] = {}
    
    # Group tickers by their required start date to maximize bulk download efficiency
    from collections import defaultdict
    groups = defaultdict(list)
    for ticker in tickers:
        last_date = cov_dict.get(ticker)
        start_date = last_date if pd.notna(last_date) else DEFAULT_START
        groups[start_date].append(ticker)

    for start_date, group_tickers in groups.items():
        logger.info(f"Smart backfill: grouping {len(group_tickers)} tickers starting from {start_date}")
        w = backfill_history(group_tickers, start=start_date, db_path=db_path)
        written.update(w)

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
