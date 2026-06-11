"""
Trading cost model.

Cost per unit of turnover = half-spread (by liquidity tier) + commission + FX fee.
Expressed in basis points of traded notional, charged on |Δweight| each day.
"""

from dataclasses import dataclass, field

TIER_HALF_SPREAD_BPS = {"L1": 5.0, "L2": 15.0, "L3": 40.0}


@dataclass
class CostModel:
    tier_by_ticker: dict[str, str] = field(default_factory=dict)
    commission_bps: float = 0.0  # Alpaca/T212: zero, kept as a parameter
    fx_fee_bps: float = 0.0  # 15.0 for the T212 ISA book on non-GBP trades
    default_tier: str = "L2"
    flat_bps: float | None = None  # overrides everything — flat_bps=0 for cost-free runs

    def cost_bps(self, ticker: str) -> float:
        if self.flat_bps is not None:
            return self.flat_bps
        tier = self.tier_by_ticker.get(ticker, self.default_tier)
        half_spread = TIER_HALF_SPREAD_BPS.get(tier, TIER_HALF_SPREAD_BPS["L2"])
        return half_spread + self.commission_bps + self.fx_fee_bps

    def cost_rate(self, ticker: str) -> float:
        """Fractional cost per unit notional traded (bps → decimal)."""
        return self.cost_bps(ticker) / 10_000.0


ZERO_COSTS = CostModel(flat_bps=0.0)
