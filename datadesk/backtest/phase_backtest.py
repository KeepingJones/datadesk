"""
Phase-aware backtest.

Simulates a real investor journey starting from a small account (default £500)
with monthly contributions of £500/month. The momentum strategy top_n dynamically
adjusts as the portfolio NAV crosses phase thresholds.

Unlike the standard backtest (which uses a fixed top_n throughout), this reflects
how the strategy would actually be run from day one of a small account.

Returns a PhaseBacktestResult with equity curve, metrics per phase, and transition log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from datadesk.backtest.costs import CostModel
from datadesk.backtest.metrics import cagr, max_drawdown, sharpe
from datadesk.strategies.phase import portfolio_phase


@dataclass
class PhaseTransition:
    date: str
    nav_gbp: float
    from_top_n: int
    to_top_n: int
    new_phase: str


@dataclass
class PhaseBacktestResult:
    equity: pd.Series                      # portfolio equity (indexed by date)
    nav_gbp: pd.Series                     # NAV in £ including contributions
    returns: pd.Series
    metrics: dict
    transitions: list[PhaseTransition] = field(default_factory=list)


def run_phase_backtest(
    prices: pd.DataFrame,
    cost_model: CostModel,
    initial_nav_gbp: float = 500.0,
    monthly_contribution_gbp: float = 500.0,
    start: Optional[str] = None,
    quality_universe: Optional[set] = None,
    lookback: int = 126,
    skip: int = 21,
) -> PhaseBacktestResult:
    """
    Run a phase-aware backtest.

    Each month-end the phase is checked and top_n updated. Monthly contributions
    are added to the NAV before rebalancing. The equity curve tracks £ value.

    No lookahead: weights at close of day t earn return of day t+1.
    """
    if start:
        prices = prices[prices.index >= start]

    daily_rets = prices.pct_change().fillna(0.0)

    nav = initial_nav_gbp
    nav_series: dict[pd.Timestamp, float] = {}
    equity_series: dict[pd.Timestamp, float] = {}
    transitions: list[PhaseTransition] = []

    current_top_n = portfolio_phase(nav).top_n
    current_weights: pd.Series = pd.Series(0.0, index=prices.columns)

    # Pre-compute signals for all possible top_n values (3,6,10,15)
    signal = prices.shift(skip) / prices.shift(skip + lookback) - 1

    _qset = frozenset(quality_universe) if quality_universe else None

    from datadesk.strategies.momentum import month_end_dates
    rebal_dates = set(month_end_dates(prices.index))

    prev_top_n = current_top_n

    for i, date in enumerate(prices.index):
        # Add monthly contribution on first trading day of each month
        if i > 0 and date.month != prices.index[i - 1].month:
            nav += monthly_contribution_gbp

        # Re-evaluate phase only at rebalance dates to prevent daily threshold chatter
        if date in rebal_dates:
            phase = portfolio_phase(nav)
            if phase.top_n != prev_top_n:
                transitions.append(PhaseTransition(
                    date=str(date.date()),
                    nav_gbp=round(nav, 2),
                    from_top_n=prev_top_n,
                    to_top_n=phase.top_n,
                    new_phase=phase.label,
                ))
                prev_top_n = phase.top_n
        else:
            phase = portfolio_phase(nav)

        # Rebalance on month-end dates
        if date in rebal_dates:
            scores = signal.loc[date].dropna()
            if _qset:
                scores = scores[scores.index.isin(_qset)]
            scores = scores[scores > 0]
            top_n = phase.top_n
            top = scores.nlargest(top_n)
            new_w = pd.Series(0.0, index=prices.columns)
            if not top.empty:
                new_w[top.index] = 1.0 / top_n
            # Transaction costs: default cost_bps × total |Δweight| turnover
            turnover = (new_w - current_weights).abs().sum()
            cost_rate = cost_model.cost_bps("_default") / 10_000.0
            nav *= (1 - cost_rate * turnover)
            current_weights = new_w

        # Earn returns with current weights
        day_ret = float((current_weights * daily_rets.loc[date]).sum())
        nav *= (1 + day_ret)
        nav_series[date] = nav
        equity_series[date] = nav

    equity = pd.Series(equity_series)
    nav_s     = pd.Series(nav_series)
    final_nav = equity.iloc[-1]
    pure_rets = equity.pct_change().dropna()
    metrics = {
        "cagr":         float(cagr(pure_rets)),
        "sharpe":       float(sharpe(pure_rets)),
        "max_drawdown": float(max_drawdown(pure_rets)),
        "final_nav_gbp": round(final_nav, 2),
        "total_contributed_gbp": round(
            initial_nav_gbp + monthly_contribution_gbp * (len(prices) / 21),  # approx months
            2,
        ),
        "n_transitions": len(transitions),
    }

    return PhaseBacktestResult(
        equity=equity,
        nav_gbp=nav_s,
        returns=pure_rets,
        metrics=metrics,
        transitions=transitions,
    )
