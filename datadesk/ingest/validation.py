import logging

import pandas as pd

from datadesk.history.store import load_closes

logger = logging.getLogger(__name__)

def check_completeness(ticker: str) -> dict:
    """Scans the history store for gap and NaN issues."""
    df = load_closes([ticker])
    if df.empty or ticker not in df.columns:
        return {"status": "FAIL", "warnings": [f"No historical data found for {ticker}"]}
    
    series = df[ticker].dropna()
    if series.empty:
        return {"status": "FAIL", "warnings": [f"All data is NaN for {ticker}"]}
    
    warnings = []
    # Check for NaN gaps in the middle of the series
    full_date_range = pd.date_range(start=series.index.min(), end=series.index.max(), freq='B')
    missing_dates = full_date_range.difference(series.index)
    
    # It's normal to miss market holidays, but long streaks of missing business days (> 3) are suspicious.
    if not missing_dates.empty:
        # Group consecutive missing days
        missing_series = pd.Series(1, index=missing_dates)
        # Calculate diffs to find consecutive blocks
        gaps = missing_series.index.to_series().diff().dt.days
        max_gap = gaps.max()
        if max_gap and max_gap > 4:
            warnings.append(f"Significant data gap detected: maximum missing consecutive days = {max_gap}")
    
    # Check history length
    days_total = (series.index.max() - series.index.min()).days
    if days_total < 365:
        warnings.append(f"Short history: only {days_total} days of data available.")
        
    if warnings:
        return {"status": "WARN", "warnings": warnings}
    
    return {"status": "PASS", "warnings": []}

def validate_universe() -> dict:
    from datadesk.live.universe import get_active_universe
    universe = get_active_universe()
    report = {}
    for ticker in universe:
        report[ticker] = check_completeness(ticker)
    return report
