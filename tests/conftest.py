import pytest

from datadesk.models import PriceQuote


@pytest.fixture
def make_quote():
    def _make(
        ticker: str = "AAPL",
        source: str = "yahoo",
        asset_class: str = "equity",
        currency: str = "USD",
        price: float = 100.0,
        is_stale: bool = False,
    ) -> PriceQuote:
        return PriceQuote(
            ticker=ticker,
            source=source,
            asset_class=asset_class,
            currency=currency,
            price=price,
            is_stale=is_stale,
        )

    return _make
