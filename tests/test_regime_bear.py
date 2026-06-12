"""Bear-only overlay — fires only on joint index+VIX stress."""

import numpy as np
import pandas as pd

from datadesk.strategies.regime import bear_only_scale


def _idx(n):
    return pd.bdate_range("2020-01-01", periods=n)


def _rise_then_crash(n=260):
    """First ~80% steady rise, last ~20% sharp crash below the 200d MA."""
    cut = int(n * 0.8)
    rise = np.linspace(100, 300, cut)
    crash = np.linspace(300, 180, n - cut)
    return pd.Series(np.concatenate([rise, crash]), index=_idx(n))


def test_full_exposure_in_calm_uptrend():
    n = 260
    prices = pd.Series(np.linspace(100, 360, n), index=_idx(n))
    vix = pd.Series(15.0, index=_idx(n))
    scale = bear_only_scale(prices, vix)
    assert (scale.iloc[210:] == 1.0).all()  # never de-risks in a calm bull


def test_derisk_only_on_joint_stress():
    n = 260
    prices = _rise_then_crash(n)
    vix = pd.Series(15.0, index=_idx(n))
    vix.iloc[-30:] = 40.0  # panic coincides with the breakdown
    scale = bear_only_scale(prices, vix, de_risk_to=0.4)
    assert scale.iloc[-1] == 0.4  # below MA AND panic → de-risk
    assert scale.iloc[100] == 1.0  # mid-uptrend, calm → full


def test_below_ma_but_calm_stays_full():
    n = 260
    prices = _rise_then_crash(n)
    vix = pd.Series(15.0, index=_idx(n))  # never panics
    scale = bear_only_scale(prices, vix)
    assert (scale == 1.0).all()  # MA breach alone is not enough — needs VIX panic too
