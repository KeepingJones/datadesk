import logging
from datetime import UTC, datetime, timedelta

import fredapi
import pandas as pd

from datadesk.config import FRED_API_KEY
from datadesk.models import PriceQuote

logger = logging.getLogger(__name__)

# FRED series → asset class mapping for the paper fund
FRED_SERIES_MAP = {
    # US Treasury yields (proxy for govt bond prices)
    "DGS2": {"name": "US 2Y Treasury Yield", "asset_class": "govt_bond", "currency": "USD"},
    "DGS10": {"name": "US 10Y Treasury Yield", "asset_class": "govt_bond", "currency": "USD"},
    "DGS30": {"name": "US 30Y Treasury Yield", "asset_class": "govt_bond", "currency": "USD"},
    # Credit spreads (ICE BofA)
    "BAMLC0A0CM": {"name": "US IG OAS Spread", "asset_class": "corp_bond", "currency": "USD"},
    "BAMLH0A0HYM2": {"name": "US HY OAS Spread", "asset_class": "corp_bond", "currency": "USD"},
    # Risk-free rates
    "DFF": {"name": "Fed Funds Rate (SOFR proxy)", "asset_class": "rate", "currency": "USD"},
    "IUDSOIA": {"name": "SONIA", "asset_class": "rate", "currency": "GBP"},
    # Macro
    "CPIAUCSL": {"name": "US CPI", "asset_class": "macro", "currency": "USD"},
    "UNRATE": {"name": "US Unemployment Rate", "asset_class": "macro", "currency": "USD"},
}


def _get_fred() -> fredapi.Fred | None:
    if not FRED_API_KEY:
        logger.warning("FRED_API_KEY not set — skipping FRED source")
        return None
    return fredapi.Fred(api_key=FRED_API_KEY)


def get_series_latest(series_id: str) -> PriceQuote | None:
    fred = _get_fred()
    if not fred:
        return None

    meta = FRED_SERIES_MAP.get(
        series_id, {"name": series_id, "asset_class": "unknown", "currency": "USD"}
    )

    try:
        end = datetime.today()
        start = end - timedelta(days=10)
        data: pd.Series = fred.get_series(series_id, observation_start=start.strftime("%Y-%m-%d"))
        data = data.dropna()
        if data.empty:
            return None

        return PriceQuote(
            ticker=series_id,
            source="fred",
            asset_class=meta["asset_class"],
            currency=meta["currency"],
            price=round(float(data.iloc[-1]), 6),
            timestamp=datetime.now(UTC),
        )
    except Exception as e:
        logger.error(f"FRED fetch failed for {series_id}: {e}")
        return None


def get_rate(series_id: str) -> float | None:
    """Latest rate value (e.g. SOFR, SONIA) for FX forward calculations."""
    q = get_series_latest(series_id)
    return q.price if q else None


def get_all_quotes() -> list[PriceQuote]:
    return [q for sid in FRED_SERIES_MAP if (q := get_series_latest(sid))]
