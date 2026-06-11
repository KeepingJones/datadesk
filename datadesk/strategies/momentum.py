"""
Cross-sectional momentum: each month, hold the top-N names by trailing
return, skipping the most recent month (the classic 12-1 construction —
short-term reversal would otherwise contaminate the signal).
"""

from collections.abc import Callable

import pandas as pd


def month_end_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Last trading day of each month present in the index."""
    s = pd.Series(index=index, data=index)
    return pd.DatetimeIndex(s.groupby([index.year, index.month]).last().values)


def momentum(
    lookback: int = 126, top_n: int = 10, skip: int = 21
) -> Callable[[pd.DataFrame], pd.DataFrame]:
    def target_weights(prices: pd.DataFrame) -> pd.DataFrame:
        signal = prices.shift(skip) / prices.shift(skip + lookback) - 1
        rebal_dates = month_end_dates(prices.index)

        rows = {}
        for date in rebal_dates:
            scores = signal.loc[date].dropna()
            scores = scores[scores > 0]  # long-only: don't buy downtrends
            if scores.empty:
                rows[date] = pd.Series(0.0, index=prices.columns)
                continue
            top = scores.nlargest(top_n)
            w = pd.Series(0.0, index=prices.columns)
            w[top.index] = 1.0 / top_n  # cash remainder if fewer than N qualify
            rows[date] = w

        df = pd.DataFrame(rows).T.sort_index()
        return df.reindex(prices.index).ffill().fillna(0.0)

    return target_weights
