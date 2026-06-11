import logging
from datetime import UTC, datetime

import requests

from datadesk.models import PriceQuote

logger = logging.getLogger(__name__)

ECB_BASE = "https://data-api.ecb.europa.eu/service/data"

# ECB statistical data key for EUR FX reference rates: EXR/D.{currency}.EUR.SP00.A
ECB_FX_PAIRS = {
    "USD": "EXR/D.USD.EUR.SP00.A",
    "GBP": "EXR/D.GBP.EUR.SP00.A",
    "JPY": "EXR/D.JPY.EUR.SP00.A",
    "CHF": "EXR/D.CHF.EUR.SP00.A",
}


def _fetch_ecb_rate(series_key: str) -> float | None:
    url = f"{ECB_BASE}/{series_key}"
    params = {"format": "jsondata", "lastNObservations": "5"}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        series = data["dataSets"][0]["series"]
        key = next(iter(series.keys()))
        obs = series[key]["observations"]
        latest_idx = max(obs.keys(), key=int)
        return float(obs[latest_idx][0])
    except Exception as e:
        logger.error(f"ECB fetch failed for {series_key}: {e}")
        return None


def get_fx_rates() -> list[PriceQuote]:
    """Fetch official ECB EUR reference rates as PriceQuotes."""
    quotes = []
    for currency, series_key in ECB_FX_PAIRS.items():
        rate = _fetch_ecb_rate(series_key)
        if rate is None:
            continue

        # ECB publishes rates as X per EUR — stored as-is, recon engine handles direction
        quotes.append(
            PriceQuote(
                ticker=f"EUR{currency}=X",
                source="ecb",
                asset_class="fx",
                currency=currency,
                price=round(rate, 6),
                timestamp=datetime.now(UTC),
            )
        )
    return quotes
