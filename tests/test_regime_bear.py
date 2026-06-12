"""Bear-only overlay — fires only on joint index+VIX stress."""

import pandas as pd

from datadesk.strategies.regime import bear_only_scale


def _idx(n):
    return pd.bdate_range("2020-01-01", periods=n)


def test_full_exposure_in_calm_uptrend():
    n = 260
    idx = _idx(n)
    prices = pd.Series(range(100, 100 + n), index=idx, dtype=float)  # steady uptrend
    vix = pd.Series(15.0, index=idx)
    scale = bear_only_scale(prices, vix)
    assert (scale.iloc[210:] == 1.0).all()  # never de-risks in a calm bull


def test_derisk_only_on_joint_stress():
    n = 260
    idx = _idx(n)
    # rise then crash below the 200d MA
    vals = list(range(100, 300)) + list(range(300, 300 - 60, -3))
    prices = pd.Series(vals[:n], index=idx, dtype=float)
    vix = pd.Series(15.0, index=idx)
    vix.iloc[-30:] = 40.0  # panic coincides with the breakdown
    scale = bear_only_scale(prices, vix, de_risk_to=0.4)
    assert scale.iloc[-1] == 0.4  # below MA AND panic → de-risk
    assert scale.iloc[100] == 1.0  # mid-uptrend, calm → full


def test_below_ma_but_calm_stays_full():
    n = 260
    idx = _idx(n)
    vals = list(range(100, 300)) + list(range(300, 300 - 60, -3))
    prices = pd.Series(vals[:n], index=idx, dtype=float)
    vix = pd.Series(15.0, index=idx)  # never panics
    scale = bear_only_scale(prices, vix)
    assert (scale == 1.0).all()  # MA breach alone is not enough — needs VIX panic too
