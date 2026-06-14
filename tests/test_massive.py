import pandas as pd
from unittest.mock import patch, MagicMock
from datadesk.ingest.massive import fetch_massive_prices, MassiveRateLimitExceeded

@patch("datadesk.ingest.massive.os.environ.get")
@patch("datadesk.ingest.massive.requests.get")
@patch("datadesk.ingest.massive.time.sleep")
def test_fetch_massive_prices_success(mock_sleep, mock_get, mock_env_get):
    mock_env_get.return_value = "dummy_key"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": "OK",
        "results": [
            {
                "t": 1609718400000, # 2021-01-04
                "v": 1000000,
                "o": 118.0,
                "c": 120.0,
                "h": 125.0,
                "l": 115.0
            }
        ]
    }
    mock_get.return_value = mock_response
    
    df = fetch_massive_prices("AAPL", "2021-01-01")
    
    assert df is not None
    assert not df.empty
    assert df.iloc[0]["ticker"] == "AAPL"
    assert df.iloc[0]["date"] == "2021-01-04"
    assert df.iloc[0]["close"] == 120.0
    assert df.iloc[0]["volume"] == 1000000
    
    # Verify sleep was called to respect the 12s rate limit
    mock_sleep.assert_called_with(12.0)

@patch("datadesk.ingest.massive.os.environ.get")
@patch("datadesk.ingest.massive.requests.get")
@patch("datadesk.ingest.massive.time.sleep")
def test_fetch_massive_rate_limit(mock_sleep, mock_get, mock_env_get):
    mock_env_get.return_value = "dummy_key"
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_get.return_value = mock_response
    
    try:
        fetch_massive_prices("AAPL", "2021-01-01")
        assert False, "Should have raised MassiveRateLimitExceeded"
    except MassiveRateLimitExceeded:
        pass
        
    # Verify 60s cooldown sleep was called
    mock_sleep.assert_called_with(60)
