"""
Cross-sectional momentum: each month, hold the top-N names by trailing
return, skipping the most recent month (the classic 12-1 construction —
short-term reversal would otherwise contaminate the signal).
"""

from collections.abc import Callable
from collections.abc import Collection

import pandas as pd


def month_end_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Last trading day of each month present in the index."""
    s = pd.Series(index=index, data=index)
    return pd.DatetimeIndex(s.groupby([index.year, index.month]).last().values)


def momentum(
    lookback: int = 126,
    top_n: int = 10,
    skip: int = 21,
    quality_universe: Collection[str] | None = None,
    vol_weight: bool = False,
    vol_window: int = 21,
    vol_floor: float = 0.005,
) -> Callable[[pd.DataFrame], pd.DataFrame]:
    """
    Cross-sectional momentum with optional quality filter and vol-targeting.

    quality_universe: if provided, only tickers in this set are eligible at each
    rebalance. Tickers not in the set are zeroed out before ranking. Useful for
    excluding micro-caps or deeply loss-making companies.

    vol_weight: if True, weight positions by inverse realised volatility rather than
    equal-weight. Reduces MaxDD at the cost of some CAGR — positions in low-vol stocks
    get overweighted, high-vol stocks underweighted. Cash is still held for < top_n slots.

    vol_window: rolling window (days) for volatility estimate used in weighting.
    vol_floor: minimum daily vol to prevent division by zero or extreme weights.
    """
    _qset: frozenset[str] | None = (
        frozenset(quality_universe) if quality_universe else None
    )

    def target_weights(prices: pd.DataFrame) -> pd.DataFrame:
        signal = prices.shift(skip) / prices.shift(skip + lookback) - 1
        daily_rets = prices.pct_change()
        rolling_vol = daily_rets.rolling(vol_window).std().clip(lower=vol_floor)
        rebal_dates = month_end_dates(prices.index)

        rows = {}
        for date in rebal_dates:
            scores = signal.loc[date].dropna()
            if _qset:
                scores = scores[scores.index.isin(_qset)]
            scores = scores[scores > 0]  # long-only: don't buy downtrends
            if scores.empty:
                rows[date] = pd.Series(0.0, index=prices.columns)
                continue
            top = scores.nlargest(top_n)
            w = pd.Series(0.0, index=prices.columns)

            if vol_weight:
                vols = rolling_vol.loc[date, top.index].fillna(vol_floor)
                inv_vol = 1.0 / vols
                raw_w = inv_vol / inv_vol.sum()
                # Scale so total portfolio weight matches the fraction we'd have deployed
                # with equal weight (i.e. if top has 8 names out of 10 max, deploy 8/10)
                deploy_frac = len(top) / top_n
                w[raw_w.index] = raw_w * deploy_frac
            else:
                w[top.index] = 1.0 / top_n  # cash remainder if fewer than N qualify

            rows[date] = w

        df = pd.DataFrame(rows).T.sort_index()
        return df.reindex(prices.index).ffill().fillna(0.0)

    return target_weights
