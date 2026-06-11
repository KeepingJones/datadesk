"""
Vectorised daily-bar backtest engine.

Contract:
- A strategy produces a target-weights frame (index = dates, columns = tickers,
  values = portfolio weight at that date's close). Sparse is fine — the engine
  forward-fills between rebalances.
- Weights set at the close of day t earn day t+1's return (no lookahead).
- Costs are charged on |Δweight| at the cost model's per-ticker rate.
"""

from dataclasses import dataclass

import pandas as pd

from datadesk.backtest.costs import CostModel
from datadesk.backtest.metrics import equity_curve, summarize


@dataclass
class BacktestResult:
    returns: pd.Series  # net daily returns
    gross_returns: pd.Series
    weights: pd.DataFrame  # effective (ffilled) daily weights
    turnover: pd.Series  # sum |Δweight| per day
    costs: pd.Series  # fractional cost per day
    metrics: dict

    @property
    def equity(self) -> pd.Series:
        return equity_curve(self.returns)


def run_backtest(
    target_weights: pd.DataFrame,
    prices: pd.DataFrame,
    cost_model: CostModel | None = None,
    start: str | None = None,
    end: str | None = None,
) -> BacktestResult:
    cost_model = cost_model or CostModel()

    prices = prices.sort_index()
    rets = prices.pct_change(fill_method=None)

    w = target_weights.reindex(prices.index).ffill()
    w = w.reindex(columns=prices.columns).fillna(0.0)

    # weight held INTO day t is the target set at t-1's close
    held = w.shift(1).fillna(0.0)
    gross = (held * rets).fillna(0.0).sum(axis=1)

    dw = (w - held).abs()
    turnover = dw.sum(axis=1)
    cost_rates = pd.Series({t: cost_model.cost_bps(t) / 10_000.0 for t in prices.columns})
    costs = dw.mul(cost_rates, axis=1).sum(axis=1)

    net = gross - costs

    if start:
        net, gross = net.loc[start:], gross.loc[start:]
        w, turnover, costs = w.loc[start:], turnover.loc[start:], costs.loc[start:]
    if end:
        net, gross = net.loc[:end], gross.loc[:end]
        w, turnover, costs = w.loc[:end], turnover.loc[:end], costs.loc[:end]

    return BacktestResult(
        returns=net,
        gross_returns=gross,
        weights=w,
        turnover=turnover,
        costs=costs,
        metrics=summarize(net, turnover),
    )
