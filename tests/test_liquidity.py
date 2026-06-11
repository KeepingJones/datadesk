"""Liquidity tier assignment, tolerance scaling, liquidation maths — no network."""

from datadesk.quality.liquidity import (
    days_to_liquidate,
    get_liquidity_tier,
    get_tolerance_for_tier,
    max_position_adv_pct,
)


def test_high_adv_is_l1():
    assert get_liquidity_tier("AAPL", "equity", adv_usd=500_000_000) == "L1"


def test_l1_boundary_exact():
    assert get_liquidity_tier("X", "equity", adv_usd=100_000_000) == "L1"


def test_mid_adv_is_l2():
    assert get_liquidity_tier("X", "equity", adv_usd=50_000_000) == "L2"


def test_low_adv_is_l3():
    assert get_liquidity_tier("X", "equity", adv_usd=1_000_000) == "L3"


def test_volatility_hardcoded_l2_without_network():
    # must not attempt an ADV fetch — vol indices have no meaningful ADV
    assert get_liquidity_tier("^VIX", "volatility", adv_usd=None) == "L2"


def test_option_hardcoded_l2():
    assert get_liquidity_tier("SPY240621C00500000", "option", adv_usd=None) == "L2"


def test_unknown_adv_defaults_conservative_l3(monkeypatch):
    monkeypatch.setattr("datadesk.quality.liquidity.get_adv_usd", lambda t: None)
    assert get_liquidity_tier("OBSCURE.L", "equity", adv_usd=None) == "L3"


def test_tolerance_scaling():
    assert get_tolerance_for_tier(0.5, "L1") == 0.5
    assert get_tolerance_for_tier(0.5, "L2") == 0.75
    assert get_tolerance_for_tier(0.5, "L3") == 1.5


def test_unknown_tier_uses_base_tolerance():
    assert get_tolerance_for_tier(0.5, "L9") == 0.5


def test_max_position_adv_pct_per_tier():
    assert max_position_adv_pct("L1") == 0.10
    assert max_position_adv_pct("L2") == 0.05
    assert max_position_adv_pct("L3") == 0.02


def test_days_to_liquidate():
    # £10M position, $100M ADV, L1 → max $10M/day → 1 day
    assert days_to_liquidate(10_000_000, 100_000_000, "L1") == 1.0
    # same position at L3 → max $2M/day → 5 days
    assert days_to_liquidate(10_000_000, 100_000_000, "L3") == 5.0


def test_days_to_liquidate_zero_adv_is_sentinel():
    assert days_to_liquidate(1_000_000, 0, "L1") == 999.0
