"""
Trend filter overlay: risk-on while the index trades above its long moving
average (with a hysteresis band to stop whipsaw flip-flopping), otherwise cash.

Applied multiplicatively to another strategy's weights.
"""

import pandas as pd


def trend_signal(index_prices: pd.Series, window: int = 200, band: float = 0.02) -> pd.Series:
    """1.0 = risk-on, 0.0 = cash. Hysteresis: enter above MA*(1+band), exit below MA*(1-band)."""
    ma = index_prices.rolling(window).mean()
    upper, lower = ma * (1 + band), ma * (1 - band)

    state = pd.Series(float("nan"), index=index_prices.index)
    state[index_prices > upper] = 1.0
    state[index_prices < lower] = 0.0
    return state.ffill().fillna(1.0)  # start risk-on until the MA is warm


def apply_trend_filter(
    weights: pd.DataFrame,
    index_prices: pd.Series,
    window: int = 200,
    band: float = 0.02,
) -> pd.DataFrame:
    signal = trend_signal(index_prices, window, band)
    daily = weights.reindex(index_prices.index).ffill().fillna(0.0)
    return daily.mul(signal, axis=0)
