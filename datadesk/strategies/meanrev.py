"""
Short-term mean reversion on liquid names: buy z-score dips vs the 20-day
mean, exit when the dip normalises or after max_hold days. Long-only.
"""

from collections.abc import Callable

import numpy as np
import pandas as pd


def mean_reversion(
    z_entry: float = 2.0,
    z_exit: float = 0.5,
    max_hold: int = 10,
    max_positions: int = 5,
    ma_window: int = 20,
) -> Callable[[pd.DataFrame], pd.DataFrame]:
    def target_weights(prices: pd.DataFrame) -> pd.DataFrame:
        ma = prices.rolling(ma_window).mean()
        sd = prices.rolling(ma_window).std()
        z = (prices - ma) / sd

        weights = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        holding: dict[str, int] = {}  # ticker → days held

        z_vals = z.to_numpy()
        tickers = list(prices.columns)

        for i in range(len(prices.index)):
            # age out / exit existing positions
            for t in list(holding):
                col = tickers.index(t)
                zv = z_vals[i, col]
                holding[t] += 1
                if (not np.isnan(zv) and zv > -z_exit) or holding[t] > max_hold:
                    del holding[t]

            # new entries, deepest dips first
            if len(holding) < max_positions:
                row = z_vals[i]
                candidates = [
                    (row[c], tickers[c])
                    for c in range(len(tickers))
                    if not np.isnan(row[c]) and row[c] < -z_entry and tickers[c] not in holding
                ]
                for _, t in sorted(candidates)[: max_positions - len(holding)]:
                    holding[t] = 0

            if holding:
                weights.iloc[i, [tickers.index(t) for t in holding]] = 1.0 / max_positions

        return weights

    return target_weights
