"""Reconciliation engine with injected fake sources — no network."""

from datadesk.models import PriceQuote
from datadesk.quality.engine import ReconEngine


def _quote(ticker, source, asset_class, price, currency="USD", is_stale=False):
    return PriceQuote(
        ticker=ticker,
        source=source,
        asset_class=asset_class,
        currency=currency,
        price=price,
        is_stale=is_stale,
    )


INSTRUMENTS = [
    {"ticker": "AAPL", "asset_class": "equity", "currency": "USD", "name": "Apple"},
    {"ticker": "GBPUSD=X", "asset_class": "fx", "currency": "USD", "name": "GBP/USD"},
    {"ticker": "TLT", "asset_class": "govt_bond", "currency": "USD", "name": "US 20Y ETF"},
]


def make_engine(quotes_by_source: list[list[PriceQuote]]) -> ReconEngine:
    return ReconEngine(
        sources=[lambda qs=qs: qs for qs in quotes_by_source],
        adv_lookup=lambda ticker: 500_000_000,  # everything L1 — deterministic tolerance
        instruments=INSTRUMENTS,
    )


def test_single_source_instrument_produces_no_breaks():
    engine = make_engine([[_quote("AAPL", "yahoo", "equity", 185.5)]])
    _, breaks = engine.run()
    assert breaks == []


def test_agreeing_sources_produce_no_equity_breaks():
    engine = make_engine(
        [
            [_quote("AAPL", "yahoo", "equity", 185.50)],
            [_quote("AAPL", "vendor_b", "equity", 185.52)],  # within spread
        ]
    )
    _, breaks = engine.run()
    assert breaks == []


def test_diverging_sources_produce_break_with_correct_fields():
    engine = make_engine(
        [
            [_quote("AAPL", "yahoo", "equity", 185.50)],
            [_quote("AAPL", "vendor_b", "equity", 190.00)],
        ]
    )
    _, breaks = engine.run()
    assert len(breaks) == 1
    b = breaks[0]
    assert b.ticker == "AAPL"
    assert {b.source_a, b.source_b} == {"yahoo", "vendor_b"}
    assert b.break_cause == "GENUINE_DISCREPANCY"
    assert b.severity == "CRITICAL"  # ~2.4% diff > 3x 0.5% tolerance
    assert b.diff_pct > 2.0
    assert b.tolerance_pct == 0.5  # L1 equity


def test_fx_within_spread_is_kept_not_filtered():
    engine = make_engine(
        [
            [_quote("GBPUSD=X", "yahoo", "fx", 1.2700)],
            [_quote("GBPUSD=X", "ecb", "fx", 1.27003)],  # 0.002% — INFO but FX is kept
        ]
    )
    _, breaks = engine.run()
    assert len(breaks) == 1
    assert breaks[0].severity == "INFO"


def test_zero_price_quote_is_skipped():
    engine = make_engine(
        [
            [_quote("AAPL", "yahoo", "equity", 0.0)],
            [_quote("AAPL", "vendor_b", "equity", 185.0)],
        ]
    )
    _, breaks = engine.run()
    assert breaks == []


def test_three_sources_produce_pairwise_breaks():
    engine = make_engine(
        [
            [_quote("AAPL", "yahoo", "equity", 100.0)],
            [_quote("AAPL", "vendor_b", "equity", 105.0)],
            [_quote("AAPL", "vendor_c", "equity", 110.0)],
        ]
    )
    _, breaks = engine.run()
    assert len(breaks) == 3  # 3 choose 2


def test_breaks_sorted_critical_first():
    engine = make_engine(
        [
            [
                _quote("TLT", "yahoo", "govt_bond", 100.0),
                _quote("AAPL", "yahoo", "equity", 100.0),
            ],
            [
                _quote("TLT", "fred", "govt_bond", 101.0),  # 1% bond diff → WARNING
                _quote("AAPL", "vendor_b", "equity", 110.0),  # ~9.5% → CRITICAL
            ],
        ]
    )
    _, breaks = engine.run()
    assert [b.severity for b in breaks] == ["CRITICAL", "WARNING"]


def test_failing_source_does_not_kill_run():
    def broken_source():
        raise ConnectionError("vendor feed down")

    engine = ReconEngine(
        sources=[broken_source, lambda: [_quote("AAPL", "yahoo", "equity", 185.0)]],
        adv_lookup=lambda t: 500_000_000,
        instruments=INSTRUMENTS,
    )
    quotes, breaks = engine.run()
    assert "AAPL" in quotes
    assert breaks == []


def test_diff_pct_uses_mid_price_denominator():
    engine = make_engine(
        [
            [_quote("AAPL", "yahoo", "equity", 90.0)],
            [_quote("AAPL", "vendor_b", "equity", 110.0)],
        ]
    )
    _, breaks = engine.run()
    # |90-110| / mid(100) = 20%, not 22.2% (vs 90) or 18.2% (vs 110)
    assert breaks[0].diff_pct == 20.0
