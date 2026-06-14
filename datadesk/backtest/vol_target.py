"""
Volatility targeting — scale the portfolio weight vector so the portfolio
targets a constant annualised volatility (default 15%).

Implementation:
  - Compute the ex-ante portfolio vol at each day as:
        port_vol_t = rolling_std(port_return, window) * sqrt(252)
    where port_return is the unscaled gross return = (held * rets).sum(axis=1).
  - Scale factor = TARGET_VOL / port_vol_t, capped at MAX_LEVERAGE.
  - Applied to the weight matrix BEFORE costs are computed in the engine,
    which means costs also reflect the scaled turnover.

Usage:
    from datadesk.backtest.vol_target import vol_target_weights
    w_scaled = vol_target_weights(w, prices, target_vol=0.15, window=63)
"""

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def vol_target_weights(
    weights: pd.DataFrame,
    prices: pd.DataFrame,
    target_vol: float = 0.15,
    window: int = 63,
    max_leverage: float = 2.0,
) -> pd.DataFrame:
    """
    Return a scaled version of `weights` that targets `target_vol` annualised vol.

    Parameters
    ----------
    weights     : strategy weights frame (same index/columns as prices)
    prices      : close prices frame
    target_vol  : annualised portfolio volatility target (e.g. 0.15 = 15%)
    window      : lookback in trading days for realised vol estimate
    max_leverage: cap on the scale factor (prevents huge leverage in low-vol regimes)
    """
    rets = prices.pct_change(fill_method=None)

    w_aligned = weights.reindex(prices.index).ffill().reindex(columns=prices.columns).fillna(0.0)
    held = w_aligned.shift(1).fillna(0.0)

    # Unscaled gross daily return of the strategy
    port_ret = (held * rets.fillna(0.0)).sum(axis=1)

    # Rolling realised vol (annualised)
    rolling_vol = port_ret.rolling(window, min_periods=max(10, window // 4)).std() * np.sqrt(TRADING_DAYS)

    # Avoid division by tiny/zero vol — use a floor of 0.5% annual
    rolling_vol = rolling_vol.clip(lower=0.005)

    scale = (target_vol / rolling_vol).clip(upper=max_leverage)
    scale = scale.fillna(1.0)

    # Broadcast scalar scale to weight matrix
    w_scaled = w_aligned.mul(scale, axis=0)
    return w_scaled
