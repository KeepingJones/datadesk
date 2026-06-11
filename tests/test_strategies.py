import numpy as np
import pandas as pd

from datadesk.strategies.meanrev import mean_reversion
from datadesk.strategies.momentum import momentum, month_end_dates
from datadesk.strategies.regime import vix_scale
from datadesk.strategies.trend import trend_signal


def trending_prices(n_days=300):
    """WINNER rises 0.2%/day, LOSER falls 0.2%/day, FLAT flat."""
    idx = pd.bdate_range("2022-01-03", periods=n_days)
    return pd.DataFrame(
        {
            "WINNER": 100 * 1.002 ** np.arange(n_days),
            "LOSER": 100 * 0.998 ** np.arange(n_days),
            "FLAT": np.full(n_days, 100.0),
        },
        index=idx,
    )


# ── momentum ────────────────────────────────────────────────────────────────


def test_momentum_picks_the_winner():
    prices = trending_prices()
    weights = momentum(lookback=126, top_n=1, skip=21)(prices)
    final = weights.iloc[-1]
    assert final["WINNER"] == 1.0
    assert final["LOSER"] == 0.0


def test_momentum_long_only_no_negative_weights():
    weights = momentum(lookback=126, top_n=2)(trending_prices())
    assert (weights.fillna(0) >= 0).all().all()


def test_momentum_stays_in_cash_when_everything_falls():
    n = 300
    idx = pd.bdate_range("2022-01-03", periods=n)
    prices = pd.DataFrame(
        {"A": 100 * 0.998 ** np.arange(n), "B": 100 * 0.997 ** np.arange(n)}, index=idx
    )
    weights = momentum(lookback=126, top_n=2)(prices)
    assert float(weights.iloc[-1].sum()) == 0.0  # nothing has positive momentum


def test_month_end_dates_one_per_month():
    idx = pd.bdate_range("2022-01-03", "2022-06-30")
    ends = month_end_dates(idx)
    assert len(ends) == 6
    assert all(d in idx for d in ends)


# ── trend filter ────────────────────────────────────────────────────────────


def test_trend_signal_on_in_uptrend_off_in_downtrend():
    n = 600
    idx = pd.bdate_range("2020-01-01", periods=n)
    up_then_down = np.concatenate(
        [100 * 1.002 ** np.arange(300), 100 * 1.002**300 * 0.997 ** np.arange(300)]
    )
    sig = trend_signal(pd.Series(up_then_down, index=idx), window=200, band=0.02)
    assert sig.iloc[290] == 1.0  # late uptrend: risk-on
    assert sig.iloc[-1] == 0.0  # deep downtrend: cash


def test_trend_hysteresis_no_flipflop_inside_band():
    n = 300
    idx = pd.bdate_range("2020-01-01", periods=n)
    prices = pd.Series(100.0, index=idx)  # dead flat → always inside the band
    sig = trend_signal(prices, window=50, band=0.02)
    assert sig.nunique() == 1  # state never flips


# ── mean reversion ──────────────────────────────────────────────────────────


def test_meanrev_buys_the_dip_and_exits():
    n = 80
    idx = pd.bdate_range("2022-01-03", periods=n)
    flat = np.full(n, 100.0)
    flat[40] = 90.0  # one-day 10% crash, then recovery
    prices = pd.DataFrame({"A": flat, "NOISE": 100 + 0.01 * np.arange(n)}, index=idx)

    weights = mean_reversion(z_entry=2.0, z_exit=0.5, max_hold=10, max_positions=5)(prices)
    assert weights.loc[idx[40], "A"] > 0  # entered on the dip day
    assert weights.loc[idx[60], "A"] == 0.0  # exited after normalisation


def test_meanrev_respects_max_positions():
    rng = np.random.default_rng(3)
    n = 200
    idx = pd.bdate_range("2022-01-03", periods=n)
    prices = pd.DataFrame(
        {f"T{i}": 100 * np.cumprod(1 + rng.normal(0, 0.03, n)) for i in range(20)}, index=idx
    )
    weights = mean_reversion(max_positions=3)(prices)
    assert (weights > 0).sum(axis=1).max() <= 3


# ── vix regime ──────────────────────────────────────────────────────────────


def test_vix_scale_three_regimes():
    idx = pd.bdate_range("2022-01-03", periods=3)
    vix = pd.Series([15.0, 25.0, 40.0], index=idx)
    scale = vix_scale(vix, calm_below=20, stress_above=30, mid_scale=0.6, stress_scale=0.3)
    assert list(scale) == [1.0, 0.6, 0.3]


def test_compose_scales_takes_min_not_product():
    from datadesk.strategies.regime import compose_scales

    idx = pd.bdate_range("2022-01-03", periods=3)
    trend = pd.Series([1.0, 0.0, 1.0], index=idx)
    vix = pd.Series([0.6, 0.3, 1.0], index=idx)
    combined = compose_scales(trend, vix)
    # min, not product: stressed day is 0.0 (trend) not 0.0*0.3, calm day stays 1.0
    assert list(combined) == [0.6, 0.0, 1.0]
