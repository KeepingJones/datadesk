import pandas as pd
from unittest.mock import patch, MagicMock
from datadesk.ingest.tiingo import fetch_tiingo_prices, TiingoRateLimitExceeded

@patch("datadesk.ingest.tiingo.TIINGO_API_KEY", "dummy_key")
@patch("datadesk.ingest.tiingo.httpx.Client")
def test_fetch_tiingo_prices_success(mock_client_class):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {
            "date": "2021-01-04T00:00:00.000Z",
            "close": 120.0,
            "high": 125.0,
            "low": 115.0,
            "open": 118.0,
            "volume": 1000000,
            "adjClose": 120.0,
            "adjHigh": 125.0,
            "adjLow": 115.0,
            "adjOpen": 118.0,
            "adjVolume": 1000000
        }
    ]
    mock_client.get.return_value = mock_response
    mock_client_class.return_value.__enter__.return_value = mock_client
    
    df = fetch_tiingo_prices("AAPL", "2021-01-01")
    
    assert df is not None
    assert not df.empty
    assert df.iloc[0]["ticker"] == "AAPL"
    assert df.iloc[0]["date"] == "2021-01-04"
    assert df.iloc[0]["close"] == 120.0
    assert df.iloc[0]["volume"] == 1000000

@patch("datadesk.ingest.tiingo.TIINGO_API_KEY", "dummy_key")
@patch("datadesk.ingest.tiingo.httpx.Client")
def test_fetch_tiingo_rate_limit(mock_client_class):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_client.get.return_value = mock_response
    mock_client_class.return_value.__enter__.return_value = mock_client
    
    try:
        fetch_tiingo_prices("AAPL", "2021-01-01")
        assert False, "Should have raised TiingoRateLimitExceeded"
    except TiingoRateLimitExceeded:
        pass
