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


def bear_only_scale(
    index_prices: pd.Series,
    vix: pd.Series,
    de_risk_to: float = 0.4,
    ma_window: int = 200,
    vix_panic: float = 30.0,
) -> pd.Series:
    """
    Bear-ONLY de-risk: full exposure (1.0) almost always; cut to `de_risk_to`
    ONLY when the index is below its long MA AND VIX is in panic territory.

    Attribution (test-and-improvement-2026-06-12) showed always-on overlays bleed
    ~18pts of CAGR over a bull decade. This fires only in a genuine joint-stress
    regime, so it keeps return in normal/choppy markets and only insures the tail.
    """
    ma = index_prices.rolling(ma_window).mean()
    below = index_prices < ma
    panic = vix.reindex(index_prices.index).ffill() > vix_panic
    scale = pd.Series(1.0, index=index_prices.index)
    scale[below & panic] = de_risk_to
    return scale


def compose_scales(*scales: pd.Series) -> pd.Series:
    """
    Combine de-risk overlays (trend, VIX regime, event risk, drawdown control)
    by element-wise MIN — the most cautious overlay wins outright.

    Multiplying them instead double-counts the same market stress (a crash
    trips trend AND vix AND drawdown: 0.6 × 0.3 × 0.5 = liquidated three
    times over, whipsawed into cash at the exact bottom).
    """
    if not scales:
        raise ValueError("compose_scales needs at least one scale series")
    combined = scales[0]
    for s in scales[1:]:
        combined = combined.combine(s.reindex(combined.index).ffill(), min)
    return combined
