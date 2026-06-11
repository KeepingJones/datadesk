import numpy as np
import pandas as pd

from datadesk.backtest.metrics import cagr, calmar, max_drawdown, sharpe, sortino, summarize


def _series(values):
    idx = pd.bdate_range("2020-01-01", periods=len(values))
    return pd.Series(values, index=idx)


def test_cagr_of_steady_returns():
    # 252 days of ~0.0297% daily ≈ 7.77% over the year
    r = _series([0.000297] * 252)
    assert abs(cagr(r) - ((1.000297**252) - 1)) < 1e-6


def test_cagr_empty_is_zero():
    assert cagr(pd.Series(dtype=float)) == 0.0


def test_sharpe_zero_vol_is_zero():
    assert sharpe(_series([0.001] * 100)) == 0.0  # constant returns → sd 0


def test_sharpe_sign_follows_mean():
    rng = np.random.default_rng(7)
    # drift 3x the standard error of the mean — sign is robust to the seed
    up = _series(rng.normal(0.003, 0.01, 500))
    down = _series(rng.normal(-0.003, 0.01, 500))
    assert sharpe(up) > 0 > sharpe(down)


def test_sortino_exceeds_sharpe_when_vol_is_upside():
    # big varied ups, small varied downs: downside std < total std → sortino > sharpe
    r = _series([0.05, -0.01, 0.04, -0.012] * 50)
    assert sortino(r) > sharpe(r) > 0


def test_max_drawdown_known_case():
    # +10%, then -50%: peak 1.1 → trough 0.55 = -50%
    r = _series([0.10, -0.50])
    assert abs(max_drawdown(r) - (-0.50)) < 1e-9


def test_max_drawdown_monotonic_up_is_zero():
    assert max_drawdown(_series([0.01] * 50)) == 0.0


def test_calmar_consistency():
    r = _series([0.01, -0.02, 0.015, -0.005] * 60)
    expected = cagr(r) / abs(max_drawdown(r))
    assert abs(calmar(r) - expected) < 1e-9


def test_summarize_keys():
    r = _series([0.001] * 300)
    s = summarize(r, turnover=_series([0.05] * 300))
    assert set(s) >= {"cagr", "sharpe", "sortino", "max_drawdown", "calmar", "days"}
    assert s["days"] == 300
    assert s["avg_annual_turnover"] == round(0.05 * 252, 2)
