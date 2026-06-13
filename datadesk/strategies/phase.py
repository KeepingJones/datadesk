"""
Phase-aware portfolio parameters.

The strategy adapts as the portfolio grows from a small starting sum (£500–£2k)
fed by £500/month contributions toward a mature portfolio (£50k+).

Phase  NAV range         top_n  rebal_freq  rationale
-----  ----------------  -----  ----------  -----------------------------------------
1      < £5,000          3      monthly     Concentrate — small size, FX costs bite;
                                            fractional shares help but slippage matters
2      £5,000–£25,000    6      monthly     Diversify gradually; still contribution-driven
3      £25,000–£100,000  10     monthly     Normal cross-sectional momentum
4      > £100,000        15     monthly     Wider cross-section; tax efficiency priority
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PortfolioPhase:
    label: str
    top_n: int
    rebal_freq: str      # pandas offset alias ("ME" = month-end)
    min_position_gbp: float
    description: str


PHASES = [
    PortfolioPhase(
        label="Phase 1 — Accumulation",
        top_n=3,
        rebal_freq="ME",
        min_position_gbp=50.0,
        description="<£5k: concentrate top-3, minimise transaction count",
    ),
    PortfolioPhase(
        label="Phase 2 — Growth",
        top_n=6,
        rebal_freq="ME",
        min_position_gbp=100.0,
        description="£5k–£25k: expand to top-6 as contributions fill the book",
    ),
    PortfolioPhase(
        label="Phase 3 — Compounding",
        top_n=10,
        rebal_freq="ME",
        min_position_gbp=500.0,
        description="£25k–£100k: full cross-sectional momentum, ISA headroom matters",
    ),
    PortfolioPhase(
        label="Phase 4 — Scale",
        top_n=15,
        rebal_freq="ME",
        min_position_gbp=2_000.0,
        description=">£100k: wider diversification, prioritise tax efficiency over CAGR",
    ),
]

_THRESHOLDS = [5_000, 25_000, 100_000]


def portfolio_phase(nav_gbp: float) -> PortfolioPhase:
    """Return the PortfolioPhase appropriate for the given portfolio NAV in GBP."""
    for i, threshold in enumerate(_THRESHOLDS):
        if nav_gbp < threshold:
            return PHASES[i]
    return PHASES[-1]


def top_n_for_nav(nav_gbp: float) -> int:
    """Convenience: return just the top_n for the given NAV."""
    return portfolio_phase(nav_gbp).top_n


def simulate_nav_series(
    monthly_contribution_gbp: float = 500.0,
    initial_nav_gbp: float = 500.0,
    annual_cagr: float = 0.20,
    years: int = 15,
) -> list[tuple[int, float, str]]:
    """
    Project portfolio NAV month by month with contributions + assumed CAGR.
    Returns list of (month, nav_gbp, phase_label).

    Useful for understanding when phase transitions occur under different return assumptions.
    """
    monthly_return = (1 + annual_cagr) ** (1 / 12) - 1
    nav = initial_nav_gbp
    result = []
    for month in range(years * 12):
        nav = nav * (1 + monthly_return) + monthly_contribution_gbp
        phase = portfolio_phase(nav)
        result.append((month + 1, round(nav, 2), phase.label))
    return result
