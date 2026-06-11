"""Every branch of break root-cause classification."""

from datadesk.quality.classifier import classify_break


def test_zero_price_is_data_quality_critical(make_quote):
    q_a = make_quote(price=0.0)
    q_b = make_quote(price=100.0)
    cause, severity = classify_break("AAPL", "equity", q_a, q_b, 100.0, 0.5)
    assert (cause, severity) == ("DATA_QUALITY", "CRITICAL")


def test_negative_price_is_data_quality_critical(make_quote):
    q_a = make_quote(price=-5.0)
    q_b = make_quote(price=100.0)
    cause, severity = classify_break("AAPL", "equity", q_a, q_b, 100.0, 0.5)
    assert (cause, severity) == ("DATA_QUALITY", "CRITICAL")


def test_stale_quote_is_stale_price_warning(make_quote):
    q_a = make_quote(is_stale=True)
    q_b = make_quote(price=101.0)
    cause, severity = classify_break("AAPL", "equity", q_a, q_b, 1.0, 0.5)
    assert (cause, severity) == ("STALE_PRICE", "WARNING")


def test_tiny_diff_is_within_spread_info(make_quote):
    q_a = make_quote(price=100.00)
    q_b = make_quote(price=100.02)
    cause, severity = classify_break("AAPL", "equity", q_a, q_b, 0.02, 0.5)
    assert (cause, severity) == ("SPREAD_WITHIN_NORMAL", "INFO")


def test_fx_break_is_always_critical(make_quote):
    q_a = make_quote(ticker="GBPUSD=X", asset_class="fx", price=1.27)
    q_b = make_quote(ticker="GBPUSD=X", asset_class="fx", source="ecb", price=1.29)
    cause, severity = classify_break("GBPUSD=X", "fx", q_a, q_b, 1.56, 0.1)
    assert (cause, severity) == ("GENUINE_DISCREPANCY", "CRITICAL")


def test_huge_equity_diff_is_corporate_action(make_quote):
    q_a = make_quote(price=100.0)
    q_b = make_quote(price=50.0)  # looks like an unadjusted 2:1 split
    cause, severity = classify_break("AAPL", "equity", q_a, q_b, 66.7, 0.5)
    assert (cause, severity) == ("CORPORATE_ACTION", "WARNING")


def test_within_tolerance_is_info(make_quote):
    q_a = make_quote(price=100.0)
    q_b = make_quote(price=100.4)
    cause, severity = classify_break("AAPL", "equity", q_a, q_b, 0.4, 0.5)
    assert (cause, severity) == ("SPREAD_WITHIN_NORMAL", "INFO")


def test_moderate_bond_diff_is_fx_conversion_warning(make_quote):
    q_a = make_quote(ticker="TLT", asset_class="govt_bond", price=100.0)
    q_b = make_quote(ticker="TLT", asset_class="govt_bond", source="fred", price=101.0)
    cause, severity = classify_break("TLT", "govt_bond", q_a, q_b, 1.0, 0.05)
    assert (cause, severity) == ("FX_CONVERSION", "WARNING")


def test_breach_over_tolerance_is_genuine_warning(make_quote):
    q_a = make_quote(price=100.0)
    q_b = make_quote(price=101.0)
    # diff 1.0% > tolerance 0.5% but <= 3x tolerance → WARNING
    cause, severity = classify_break("AAPL", "equity", q_a, q_b, 1.0, 0.5)
    assert (cause, severity) == ("GENUINE_DISCREPANCY", "WARNING")


def test_breach_over_3x_tolerance_is_genuine_critical(make_quote):
    q_a = make_quote(price=100.0)
    q_b = make_quote(price=110.0)
    cause, severity = classify_break("AAPL", "equity", q_a, q_b, 9.5, 0.5)
    assert (cause, severity) == ("GENUINE_DISCREPANCY", "CRITICAL")
