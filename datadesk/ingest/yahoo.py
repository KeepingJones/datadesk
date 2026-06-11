import logging
import time
from datetime import UTC, datetime

import yfinance as yf

from datadesk.models import PriceQuote

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[PriceQuote, float]] = {}
_TTL = 300  # seconds


def get_quote(ticker: str, asset_class: str, currency: str) -> PriceQuote | None:
    now = time.time()
    if ticker in _cache:
        quote, ts = _cache[ticker]
        if now - ts < _TTL:
            return quote

    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d", interval="1d")
        if hist.empty:
            logger.warning(f"Yahoo: no data for {ticker}")
            return None

        price = float(hist["Close"].iloc[-1])
        volume = int(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else None

        bid = ask = None
        try:
            fi = t.fast_info
            bid = float(fi.bid) if getattr(fi, "bid", None) else None
            ask = float(fi.ask) if getattr(fi, "ask", None) else None
        except Exception:  # noqa: BLE001 — fast_info is best-effort only
            pass

        quote = PriceQuote(
            ticker=ticker,
            source="yahoo",
            asset_class=asset_class,
            currency=currency,
            price=round(price, 6),
            bid=round(bid, 6) if bid else None,
            ask=round(ask, 6) if ask else None,
            volume=volume,
            timestamp=datetime.now(UTC),
        )
        _cache[ticker] = (quote, now)
        return quote

    except Exception as e:
        logger.error(f"Yahoo fetch failed for {ticker}: {e}")
        return None


def get_bulk_quotes(instruments: list[dict]) -> list[PriceQuote]:
    quotes = []
    tickers = [i["ticker"] for i in instruments]

    try:
        raw = yf.download(tickers, period="2d", auto_adjust=True, progress=False, group_by="ticker")
    except Exception as e:
        logger.error(f"Yahoo bulk download failed: {e}")
        return []

    for inst in instruments:
        ticker = inst["ticker"]
        try:
            if len(tickers) == 1:
                closes = raw["Close"].dropna()
            elif ticker in raw.columns.get_level_values(0):
                closes = raw[ticker]["Close"].dropna()
            else:
                closes = None

            if closes is None or closes.empty:
                logger.warning(f"Yahoo bulk: no close for {ticker}")
                continue

            quotes.append(
                PriceQuote(
                    ticker=ticker,
                    source="yahoo",
                    asset_class=inst["asset_class"],
                    currency=inst["currency"],
                    price=round(float(closes.iloc[-1]), 6),
                    timestamp=datetime.now(UTC),
                )
            )
        except Exception as e:
            logger.warning(f"Yahoo bulk parse failed for {ticker}: {e}")
            q = get_quote(ticker, inst["asset_class"], inst["currency"])
            if q:
                quotes.append(q)

    return quotes
