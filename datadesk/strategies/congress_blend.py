"""
Congress-momentum blend strategy.

Runs the standard 12-1 cross-sectional momentum, then at each rebalance
multiplies each ticker's score by a congress-buy boost if a Congressional
member disclosed a purchase within the last `congress_window` trading days.

The boost is multiplicative: a ticker already in positive momentum territory
with a recent congress buy gets its score inflated, making it more likely to
land in top_n. Tickers with negative momentum still rank below zero and are
excluded (long-only guard is preserved).

congress_boost=2.0 means "count a congress-backed name twice as strongly in
the momentum ranking". Set to 1.0 to disable (pure momentum).

No price lookahead: boost for date T uses disclosures where disc_date <= T.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Collection
from pathlib import Path

import pandas as pd

from datadesk.config import ALTDATA_DB
from datadesk.strategies.momentum import month_end_dates


def _load_congress_buys(db_path: Path | None = None) -> pd.DataFrame:
    """Return DataFrame of (disc_date, ticker) for all congress buy disclosures."""
    import re

    def _parse(s: str) -> pd.Timestamp | None:
        if not s or not isinstance(s, str):
            return None
        s = s.strip()
        s = re.sub(r"([A-Za-z])(\d{4})$", r"\1 \2", s)
        from datetime import datetime
        for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                return pd.Timestamp(datetime.strptime(s, fmt))
            except ValueError:
                continue
        return None

    db = db_path or ALTDATA_DB
    con = sqlite3.connect(db)
    df = pd.read_sql(
        "SELECT ticker, disclosure_date FROM congress_trading WHERE transaction_type='buy'",
        con,
    )
    con.close()
    df["disc_dt"] = df["disclosure_date"].apply(_parse)
    df = df.dropna(subset=["disc_dt"])
    df["disc_dt"] = pd.to_datetime(df["disc_dt"]).dt.normalize()
    return df[["ticker", "disc_dt"]].reset_index(drop=True)


def congress_momentum(
    lookback: int = 126,
    top_n: int = 10,
    skip: int = 21,
    congress_boost: float = 2.0,
    congress_window: int = 45,
    quality_universe: Collection[str] | None = None,
    db_path: Path | None = None,
) -> Callable[[pd.DataFrame], pd.DataFrame]:
    """
    Momentum with congress-buy tilt.

    congress_boost: multiplier applied to momentum score for tickers with a
        recent congressional buy disclosure (default 2.0 = double weight in
        the ranking).
    congress_window: number of calendar days lookback for "recent" congress
        buy signals (default 45 — covers the maximum disclosure lag).
    """
    _qset: frozenset[str] | None = (
        frozenset(quality_universe) if quality_universe else None
    )
    congress_buys = _load_congress_buys(db_path)

    def target_weights(prices: pd.DataFrame) -> pd.DataFrame:
        signal = prices.shift(skip) / prices.shift(skip + lookback) - 1
        rebal_dates = month_end_dates(prices.index)

        rows: dict[pd.Timestamp, pd.Series] = {}
        for date in rebal_dates:
            scores = signal.loc[date].dropna()
            if _qset:
                scores = scores[scores.index.isin(_qset)]
            scores = scores[scores > 0]
            if scores.empty:
                rows[date] = pd.Series(0.0, index=prices.columns)
                continue

            # Congress-boost: tickers disclosed within the last congress_window calendar days
            cutoff = date - pd.Timedelta(days=congress_window)
            recent_buys = set(
                congress_buys.loc[
                    (congress_buys["disc_dt"] >= cutoff) & (congress_buys["disc_dt"] <= date),
                    "ticker",
                ]
            )
            if recent_buys:
                scores = scores.copy()
                boosted = scores.index.isin(recent_buys)
                scores[boosted] *= congress_boost

            top = scores.nlargest(top_n)
            w = pd.Series(0.0, index=prices.columns)
            w[top.index] = 1.0 / top_n
            rows[date] = w

        df = pd.DataFrame(rows).T.sort_index()
        return df.reindex(prices.index).ffill().fillna(0.0)

    return target_weights
