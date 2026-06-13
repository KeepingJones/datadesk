"""Tests for congress_momentum blend strategy."""

from unittest.mock import patch

import pandas as pd
import pytest

from datadesk.strategies.congress_blend import congress_momentum


def _make_prices(n_days=300, tickers=("A", "B", "C", "SPY")):
    idx = pd.bdate_range("2020-01-01", periods=n_days)
    import numpy as np
    rng = np.random.default_rng(42)
    data = {t: 100 * (1 + rng.normal(0.0005, 0.015, n_days)).cumprod() for t in tickers}
    return pd.DataFrame(data, index=idx)


def _empty_buys():
    return pd.DataFrame(columns=["ticker", "disc_dt"])


def _buys_for(ticker, date):
    return pd.DataFrame({"ticker": [ticker], "disc_dt": [pd.Timestamp(date)]})


class TestCongressMomentum:
    def test_returns_dataframe(self):
        prices = _make_prices()
        with patch("datadesk.strategies.congress_blend._load_congress_buys", return_value=_empty_buys()):
            fn = congress_momentum(lookback=126, top_n=3)
            w = fn(prices)
        assert isinstance(w, pd.DataFrame)
        assert w.shape[0] == len(prices)
        assert set(w.columns) == {"A", "B", "C", "SPY"}

    def test_weights_sum_leq_one(self):
        prices = _make_prices()
        with patch("datadesk.strategies.congress_blend._load_congress_buys", return_value=_empty_buys()):
            fn = congress_momentum(lookback=126, top_n=3)
            w = fn(prices)
        row_sums = w.sum(axis=1)
        assert (row_sums <= 1.0 + 1e-6).all()

    def test_congress_boost_changes_weights(self):
        """Congress buy for ticker A should increase A's presence relative to no-boost."""
        prices = _make_prices()
        # Disclose a buy for "A" 30 days before the last rebalance date
        last_day = prices.index[-1]
        buy_date = last_day - pd.Timedelta(days=30)
        buys = _buys_for("A", buy_date)

        with patch("datadesk.strategies.congress_blend._load_congress_buys", return_value=buys):
            fn_boost  = congress_momentum(lookback=126, top_n=2, congress_boost=5.0)
            fn_noboos = congress_momentum(lookback=126, top_n=2, congress_boost=1.0)
            w_boost  = fn_boost(prices)
            w_noboos = fn_noboos(prices)

        # With a huge boost A should appear in the last rebalance weight more often
        # than without boost (at least one date differs, or totals differ)
        assert not w_boost.equals(w_noboos)

    def test_no_lookahead_buys_after_date_ignored(self):
        """Buy disclosed after the rebalance date must not affect weights on that date."""
        prices = _make_prices()
        # Disclose a buy in the future (after all price data)
        future_date = prices.index[-1] + pd.Timedelta(days=10)
        buys = _buys_for("A", future_date)

        with patch("datadesk.strategies.congress_blend._load_congress_buys", return_value=buys):
            fn_boost  = congress_momentum(lookback=126, top_n=2, congress_boost=10.0)
            fn_noboos = congress_momentum(lookback=126, top_n=2, congress_boost=1.0)
            w_boost  = fn_boost(prices)
            w_noboos = fn_noboos(prices)

        assert w_boost.equals(w_noboos), "Future buy should not affect current weights"

    def test_quality_filter_respected(self):
        """Tickers not in quality_universe are excluded from selection."""
        prices = _make_prices()
        with patch("datadesk.strategies.congress_blend._load_congress_buys", return_value=_empty_buys()):
            fn = congress_momentum(lookback=126, top_n=3, quality_universe={"A", "SPY"})
            w = fn(prices)
        # B and C must always be zero
        assert (w["B"] == 0).all()
        assert (w["C"] == 0).all()
