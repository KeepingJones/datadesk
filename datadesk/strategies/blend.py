"""
Blended portfolio construction.

Inverse-volatility weighting across selector strategies, rebalanced monthly.
Floors/caps applied to ensure diversification.
"""

import pandas as pd
import numpy as np

def inverse_volatility_blend(
    strategy_weights: list[pd.DataFrame],
    prices: pd.DataFrame,
    lookback: int = 63,  # ~3 months
    min_weight: float = 0.10,
    max_weight: float = 0.40,
) -> pd.DataFrame:
    """
    Blends multiple strategy weight matrices into a single portfolio weight matrix
    using inverse-volatility weighting.
    """
    if not strategy_weights:
        return pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        
    if len(strategy_weights) == 1:
        return strategy_weights[0]

    # 1. Compute daily simulated returns for each strategy
    daily_returns = prices.pct_change()
    
    strat_returns = []
    for w in strategy_weights:
        # Shift weights by 1 to align with returns (T weights applied to T+1 returns)
        ret = (w.shift(1) * daily_returns).sum(axis=1)
        strat_returns.append(ret)
        
    strat_returns_df = pd.concat(strat_returns, axis=1)
    
    # 2. Compute rolling volatility
    rolling_vol = strat_returns_df.rolling(lookback).std() * np.sqrt(252)
    
    # Fallback to equal weight where vol is NaN (e.g. startup period)
    inv_vol = 1.0 / rolling_vol.replace(0, np.nan)
    raw_weights = inv_vol.div(inv_vol.sum(axis=1), axis=0)
    raw_weights = raw_weights.fillna(1.0 / len(strategy_weights))
    
    # 3. Apply floor and cap 
    # Simplified clipping (not a perfect solver, but functional for 4-5 strategies)
    clipped = raw_weights.clip(lower=min_weight, upper=max_weight)
    norm_weights = clipped.div(clipped.sum(axis=1), axis=0)
    
    # Rebalance monthly (end of month)
    # We find the last trading day of each month
    month_ends = pd.Series(norm_weights.index).dt.to_period("M").drop_duplicates(keep="last").index
    
    monthly_mask = pd.Series(False, index=norm_weights.index)
    monthly_mask.iloc[month_ends] = True
    
    sleeve_weights = norm_weights[monthly_mask].reindex(norm_weights.index).ffill()
    # Backfill the initial warmup period
    sleeve_weights = sleeve_weights.bfill()

    # 4. Blend the underlying asset weights
    blended = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for i, w in enumerate(strategy_weights):
        # Broadcast sleeve weight across all columns
        sw = sleeve_weights.iloc[:, i].values[:, np.newaxis]
        blended += w * sw
        
    return blended.fillna(0.0)
