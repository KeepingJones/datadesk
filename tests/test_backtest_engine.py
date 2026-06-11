import numpy as np
import pandas as pd

from datadesk.backtest.costs import CostModel
from datadesk.backtest.engine import run_backtest


def make_prices(n_days=100, tickers=("A", "B"), seed=1, drift=0.0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n_days)
    data = {t: 100 * np.cumprod(1 + rng.normal(drift, 0.01, n_days)) for i, t in enumerate(tickers)}
    return pd.DataFrame(data, index=idx)


def hold_forever(prices, ticker="A", weight=1.0):
    w = pd.DataFrame(0.0, index=[prices.index[0]], columns=prices.columns)
    w.loc[prices.index[0], ticker] = weight
    return w


def test_buy_and_hold_matches_asset_return_zero_costs():
    prices = make_prices(drift=0.001)
    weights = hold_forever(prices)
    result = run_backtest(weights, prices, CostModel(flat_bps=0.0))
    # weights set at close of day 0 → equity compounds p1/p0 ... pn/pn-1 = pn/p0
    expected = float(prices["A"].iloc[-1] / prices["A"].iloc[0])
    assert abs(float(result.equity.iloc[-1]) - expected) / expected < 1e-9


def test_no_lookahead_first_day_return_is_zero():
    prices = make_prices()
    result = run_backtest(hold_forever(prices), prices, CostModel())
    assert result.gross_returns.iloc[0] == 0.0  # can't earn day 1 with weights set day 1


def test_costs_reduce_returns():
    prices = make_prices(drift=0.001)
    weights = hold_forever(prices)
    free = run_backtest(weights, prices, None)
    # entry trade is charged; L3 default makes it visible
    costly = run_backtest(weights, prices, CostModel(default_tier="L3", fx_fee_bps=15))
    assert float(costly.equity.iloc[-1]) < float(free.equity.iloc[-1])


def test_turnover_charged_on_rebalance():
    prices = make_prices(n_days=10)
    w = pd.DataFrame(0.0, index=prices.index[[0, 5]], columns=prices.columns)
    w.loc[w.index[0], "A"] = 1.0
    w.loc[w.index[1], "B"] = 1.0  # full switch on day 6 → turnover 2.0
    result = run_backtest(w, prices, CostModel(default_tier="L1"))
    assert abs(result.turnover.iloc[5] - 2.0) < 1e-9
    # cost that day = 2.0 * 5bp
    assert abs(result.costs.iloc[5] - 2.0 * 0.0005) < 1e-12


def test_weights_ffill_between_rebalances():
    prices = make_prices(n_days=20)
    result = run_backtest(hold_forever(prices, weight=0.5), prices, CostModel())
    assert (result.weights["A"].iloc[1:] == 0.5).all()


def test_unknown_ticker_in_weights_is_dropped():
    prices = make_prices()
    w = hold_forever(prices)
    w["ZZZ"] = 0.5  # not in prices
    result = run_backtest(w, prices, CostModel())
    assert "ZZZ" not in result.weights.columns


def test_start_end_slicing():
    prices = make_prices(n_days=100)
    result = run_backtest(
        hold_forever(prices),
        prices,
        CostModel(),
        start=str(prices.index[50].date()),
        end=str(prices.index[59].date()),
    )
    assert len(result.returns) == 10
