"""
Volatility regime overlay: scale gross exposure by VIX level.

calm (< calm_below) → 1.0 · elevated → mid_scale · stressed (> stress_above) → stress_scale
Applied multiplicatively, same shape as the trend filter.
"""

import pandas as pd


def vix_scale(
    vix: pd.Series,
    calm_below: float = 20.0,
    stress_above: float = 30.0,
    mid_scale: float = 0.6,
    stress_scale: float = 0.3,
) -> pd.Series:
    scale = pd.Series(mid_scale, index=vix.index)
    scale[vix < calm_below] = 1.0
    scale[vix > stress_above] = stress_scale
    return scale.ffill().fillna(1.0)


def apply_vix_overlay(weights: pd.DataFrame, vix: pd.Series, **kwargs) -> pd.DataFrame:
    scale = vix_scale(vix, **kwargs).reindex(weights.index).ffill().fillna(1.0)
    return weights.mul(scale, axis=0)
