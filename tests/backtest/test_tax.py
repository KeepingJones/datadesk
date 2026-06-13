"""Tests for the UK CGT tax wrapper module."""

import pandas as pd
from datadesk.backtest.tax import UK_HIGHER_RATE, UK_BASIC_RATE, apply_uk_cgt


def _flat_returns(daily_rate: float, n_days: int, start: str = "2020-01-02") -> pd.Series:
    dates = pd.date_range(start, periods=n_days, freq="B")
    return pd.Series([daily_rate] * n_days, index=dates)


class TestApplyUKCGT:
    def test_no_gain_no_tax(self):
        """Zero return series should stay zero."""
        r = _flat_returns(0.0, 252)
        at = apply_uk_cgt(r, UK_HIGHER_RATE)
        assert abs(float((1 + at).prod() - 1.0)) < 1e-9

    def test_gain_below_exempt_no_tax(self):
        """Annual gain below £3k exempt → no CGT deduction."""
        # ~1% total return on £10k starting portfolio = £100 gain < £3k exempt
        daily = (1.01 ** (1 / 252)) - 1
        r = _flat_returns(daily, 252)
        at = apply_uk_cgt(r, UK_HIGHER_RATE, portfolio_start=10_000)
        pre = float((1 + r).prod())
        post = float((1 + at).prod())
        assert abs(pre - post) < 1e-6, "No CGT should be taken below the exempt amount"

    def test_gain_above_exempt_reduces_return(self):
        """Annual gain above £3k exempt → after-tax return should be lower.

        Use a large portfolio so that even partial-year gain (series crosses April boundary)
        clearly exceeds the £3k exempt in every segment.
        """
        daily_40pct = (1.40 ** (1 / 252)) - 1
        r = _flat_returns(daily_40pct, 252, start="2020-01-02")
        # £100k portfolio: 40% annual ≈ £40k gain — even partial year >> £3k exempt
        at = apply_uk_cgt(r, UK_HIGHER_RATE, portfolio_start=100_000)
        pre = float((1 + r).prod())
        post = float((1 + at).prod())
        assert post < pre, "After-tax equity must be less than pre-tax when CGT applies"
        # Total after-tax growth must be materially less than pre-tax (CGT on ~£37k+ taxable)
        assert (pre - post) > 0.02, "CGT drag should be significant on a £100k portfolio"

    def test_basic_rate_less_cgt_than_higher(self):
        """Basic-rate taxpayer pays less CGT than higher-rate."""
        daily = (1.50 ** (1 / 252)) - 1
        r = _flat_returns(daily, 252, start="2020-01-02")
        at_higher = apply_uk_cgt(r, UK_HIGHER_RATE, portfolio_start=10_000)
        at_basic = apply_uk_cgt(r, UK_BASIC_RATE, portfolio_start=10_000)
        assert float((1 + at_basic).prod()) > float((1 + at_higher).prod())

    def test_loss_carried_forward(self):
        """Loss in year 1 reduces taxable gain in year 2."""
        # Year 1: big loss. Year 2: big gain. Without carry-forward, year 2 gets full exempt.
        # With carry-forward, some of year 2 gain is offset by year 1 loss.
        daily_loss = (0.70 ** (1 / 252)) - 1  # -30% in year 1
        daily_gain = (1.50 ** (1 / 252)) - 1  # +50% in year 2
        r_loss = _flat_returns(daily_loss, 252, start="2020-01-02")
        r_gain = _flat_returns(daily_gain, 252, start="2021-01-04")
        r = pd.concat([r_loss, r_gain])

        # Standalone year 2 gain (no carry)
        at_no_carry = apply_uk_cgt(r_gain, UK_HIGHER_RATE, portfolio_start=7_000)

        # With carry-forward
        at_with_carry = apply_uk_cgt(r, UK_HIGHER_RATE, portfolio_start=10_000)

        # The equity at end of both runs should be higher with carry-forward (less tax in yr 2)
        end_carry = float((1 + at_with_carry).prod())
        end_no_carry = float((1 + at_no_carry).prod())
        # Can't directly compare (different starting portfolios), but check structure:
        # at_with_carry year 2 portion should show less CGT drag
        yr2_at = at_with_carry[at_with_carry.index >= "2021-01-04"]
        yr2_pre = r_gain
        assert float((1 + yr2_at).prod()) >= float((1 + yr2_pre).prod()) - 1e-6

    def test_empty_series(self):
        r = pd.Series([], dtype=float)
        at = apply_uk_cgt(r)
        assert len(at) == 0

    def test_multi_year_compounding(self):
        """Three profitable years should each take a CGT hit."""
        daily = (1.30 ** (1 / 252)) - 1  # 30%/year, exempt is £3k, portfolio starts £10k
        # Year 1 gain: £3k → £0 taxable (exactly at exempt). Year 2 starts with £13k...
        # 30% of £13k = £3.9k > £3k → taxable in year 2. Year 3 similarly.
        r = _flat_returns(daily, 3 * 252, start="2020-01-02")
        at = apply_uk_cgt(r, UK_HIGHER_RATE, portfolio_start=10_000)
        pre = float((1 + r).prod())
        post = float((1 + at).prod())
        assert post < pre
        # After-tax CAGR should be roughly 24-28% (30% - CGT drag)
        years = len(at) / 252
        at_cagr = post ** (1 / years) - 1
        assert 0.20 < at_cagr < 0.32


class TestPlatformClassification:
    def test_uk_stock(self):
        from datadesk.universe.platform import classify
        c = classify("LGEN.L")
        assert c["is_uk"]
        assert c["t212_isa"]
        assert not c["alpaca"]

    def test_us_etf(self):
        from datadesk.universe.platform import classify
        c = classify("SPY")
        assert c["is_us_etf"]
        assert c["alpaca"]
        assert not c["t212_isa"]
        assert c["ucits_equivalent"] == "CSPX.L"

    def test_us_stock(self):
        from datadesk.universe.platform import classify
        c = classify("NVDA")
        assert c["is_us_stock"]
        assert c["alpaca"]
        assert c["t212_isa"]

    def test_index_ticker(self):
        from datadesk.universe.platform import classify, available_on_alpaca, available_on_t212_isa
        assert not available_on_alpaca("^VIX")
        assert not available_on_t212_isa("^VIX")

    def test_split_by_platform(self):
        from datadesk.universe.platform import split_by_platform
        tickers = ["AAPL", "NVDA", "SPY", "QQQ", "LGEN.L", "^VIX"]
        b = split_by_platform(tickers)
        assert set(b["both"]) == {"AAPL", "NVDA"}
        assert set(b["alpaca_only"]) == {"SPY", "QQQ"}
        assert set(b["isa_only"]) == {"LGEN.L"}
        assert set(b["unavailable"]) == {"^VIX"}
