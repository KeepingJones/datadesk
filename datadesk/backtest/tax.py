"""
After-tax return simulation for UK higher-rate taxpayers.

Two account modes:
  'alpaca' — taxable account. CGT at 24% applied annually on net gains above £3,000 exempt
             amount. Losses carried forward to offset future gains. FX cost: none (USD base).
  'isa'    — ISA wrapper. CGT = 0, dividend tax = 0. FX cost: 15bps per side (0.30% round-
             trip) on non-GBP tickers — pass T212_ISA_COSTS to the engine to capture this;
             this module handles only the tax layer (nothing to apply for ISA).

CGT is applied as a lump-sum at the end of each UK tax year (April 5). In practice the
payment is due Jan 31 the following year, but we deduct it from equity at year-end for
simplicity. This slightly understates the ISA benefit (cash stays invested longer in
reality) — conservative and correct in direction.

The equity reduction from CGT compounds into all subsequent years, so strategies with high
turnover / high gross return in a taxable account correctly show more CGT drag.

Usage:
    from datadesk.backtest.tax import TaxParams, apply_uk_cgt, compare_tax_wrappers

    uk_higher_rate = TaxParams(cgt_rate=0.24, annual_exempt=3_000)

    # post-process a BacktestResult from run_backtest():
    after_tax_returns = apply_uk_cgt(result.returns, uk_higher_rate)

    # or run a full side-by-side comparison:
    comparison = compare_tax_wrappers(
        target_weights=weights,
        prices=prices,
        alpaca_cost=ALPACA_COSTS,
        isa_cost=T212_ISA_COSTS,
        tax_params=uk_higher_rate,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from datadesk.backtest.costs import ALPACA_COSTS, T212_ISA_COSTS, CostModel
from datadesk.backtest.engine import BacktestResult, run_backtest
from datadesk.backtest.metrics import summarize


@dataclass
class TaxParams:
    cgt_rate: float = 0.24          # higher-rate UK taxpayer 2025/26
    annual_exempt: float = 3_000.0  # CGT annual exempt amount
    initial_portfolio: float = 10_000.0  # notional starting value in GBP

    # UK tax year ends April 5. We anchor on April 6 start — pandas YearEnd offset:
    tax_year_freq: str = field(default="YE-APR", repr=False)


UK_HIGHER_RATE = TaxParams(cgt_rate=0.24, annual_exempt=3_000.0)
UK_BASIC_RATE = TaxParams(cgt_rate=0.18, annual_exempt=3_000.0)


def apply_uk_cgt(
    returns: pd.Series,
    tax: TaxParams = UK_HIGHER_RATE,
    portfolio_start: float | None = None,
) -> pd.Series:
    """
    Apply UK CGT drag to a daily returns series.

    Returns a new daily returns series with CGT deducted at the end of each UK tax
    year (April 5). Losses are carried forward to offset future gains. The annual exempt
    amount is used once per tax year.

    This does NOT model: bed-and-ISA, share identification rules (Section 104 pooling),
    or intra-year loss harvesting. It operates at the portfolio level, treating the entire
    year's net return as a single disposal event — appropriate for strategy comparison.
    """
    if len(returns) == 0:
        return returns.copy()

    pv = portfolio_start or tax.initial_portfolio
    carry_loss: float = 0.0

    adjusted_daily: list[tuple[pd.Timestamp, float]] = []

    # Group by UK tax year end (April 5 = YearEnd on April period)
    for year_end, year_returns in returns.groupby(pd.Grouper(freq=tax.tax_year_freq)):
        if year_returns.empty:
            continue

        # Compute the year's gain in £ terms
        year_factor = float((1 + year_returns).prod())
        gain_gbp = pv * (year_factor - 1.0)

        # Offset with any carried-forward loss, then apply exempt amount
        net_gain = gain_gbp + carry_loss  # carry_loss is negative when losses exist
        taxable = max(0.0, net_gain - tax.annual_exempt)
        cgt_gbp = taxable * tax.cgt_rate

        # Update carry-forward loss (only losses carry; gains don't)
        if net_gain < 0:
            carry_loss = net_gain  # loss carries to next year
        else:
            carry_loss = 0.0

        # Distribute: apply all year's returns normally, then deduct CGT on the last day
        for dt, r in year_returns.items():
            adjusted_daily.append((dt, r))

        if cgt_gbp > 0 and not year_returns.empty:
            # Replace the last day's return with one that includes the CGT cash outflow
            last_dt, last_r = adjusted_daily[-1]
            # CGT as fraction of portfolio at that point
            pv_at_year_end = pv * year_factor
            cgt_drag = cgt_gbp / pv_at_year_end
            adjusted_daily[-1] = (last_dt, last_r - cgt_drag)

        # Carry equity forward (after CGT payment)
        pv = pv * year_factor - cgt_gbp

    result = pd.Series(dict(adjusted_daily))
    result.index.name = returns.index.name
    return result


@dataclass
class TaxComparisonResult:
    alpaca_pretax: dict
    alpaca_aftertax: dict
    isa: dict   # ISA has no CGT — isa metrics == post-tax by definition
    alpaca_at_returns: pd.Series
    isa_returns: pd.Series


def compare_tax_wrappers(
    target_weights: pd.DataFrame,
    prices: pd.DataFrame,
    tax_params: TaxParams = UK_HIGHER_RATE,
    alpaca_cost: CostModel = ALPACA_COSTS,
    isa_cost: CostModel = T212_ISA_COSTS,
    start: str | None = None,
    end: str | None = None,
) -> TaxComparisonResult:
    """
    Run the same strategy twice — once with Alpaca cost model (taxable), once with
    T212 ISA cost model (ISA FX costs, no CGT). Return a side-by-side comparison.
    """
    alpaca_result: BacktestResult = run_backtest(
        target_weights, prices, cost_model=alpaca_cost, start=start, end=end
    )
    isa_result: BacktestResult = run_backtest(
        target_weights, prices, cost_model=isa_cost, start=start, end=end
    )

    alpaca_at = apply_uk_cgt(alpaca_result.returns, tax_params)

    return TaxComparisonResult(
        alpaca_pretax=alpaca_result.metrics,
        alpaca_aftertax=summarize(alpaca_at, alpaca_result.turnover),
        isa=isa_result.metrics,
        alpaca_at_returns=alpaca_at,
        isa_returns=isa_result.returns,
    )


def print_tax_comparison(comp: TaxComparisonResult, label: str = "Strategy") -> None:
    """Print a three-column after-tax comparison table."""
    rows = [
        ("CAGR", "cagr", "{:.1%}"),
        ("Sharpe", "sharpe", "{:.2f}"),
        ("Max DD", "max_drawdown", "{:.1%}"),
        ("Calmar", "calmar", "{:.2f}"),
        ("Avg Ann Turnover", "avg_annual_turnover", "{:.1f}x"),
    ]
    print(f"\n{'─'*62}")
    print(f"  {label} — After-Tax Comparison")
    print(f"{'─'*62}")
    print(f"  {'Metric':<22} {'Alpaca (pre-tax)':>12} {'Alpaca (post-tax)':>14} {'T212 ISA':>10}")
    print(f"{'─'*62}")
    for label_str, key, fmt in rows:
        a_pre = comp.alpaca_pretax.get(key)
        a_post = comp.alpaca_aftertax.get(key)
        isa_v = comp.isa.get(key)
        def _f(v: float | None) -> str:
            if v is None:
                return "  —"
            try:
                return fmt.format(v)
            except (ValueError, TypeError):
                return str(v)
        print(f"  {label_str:<22} {_f(a_pre):>12} {_f(a_post):>14} {_f(isa_v):>10}")
    print(f"{'─'*62}")

    # Winner summary
    a_at = comp.alpaca_aftertax.get("cagr", 0.0) or 0.0
    i_v = comp.isa.get("cagr", 0.0) or 0.0
    winner = "T212 ISA" if i_v > a_at else "Alpaca (after-tax)"
    diff = abs(i_v - a_at)
    print(f"  Winner by CAGR: {winner} (+{diff:.1%})")
    print(f"{'─'*62}\n")
