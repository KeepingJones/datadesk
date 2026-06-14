"""
Core reconciliation engine.

Fetches prices from all configured sources, cross-checks them pairwise per
instrument, classifies every break, assigns liquidity-adjusted tolerances,
and returns the full break list ready for persistence + reporting.

Sources and the ADV lookup are injectable so the engine is testable
without network access.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from datadesk.config import INSTRUMENTS, TOLERANCES
from datadesk.models import PriceBreak, PriceQuote
from datadesk.quality.classifier import classify_break
from datadesk.quality.liquidity import get_adv_usd, get_liquidity_tier, get_tolerance_for_tier

logger = logging.getLogger(__name__)

SourceFn = Callable[[], list[PriceQuote]]

_SEVERITY_ORDER = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}


def _default_sources() -> list[SourceFn]:
    from datadesk.ingest.ecb import get_fx_rates
    from datadesk.ingest.fred import get_all_quotes
    from datadesk.ingest.yahoo import get_bulk_quotes

    return [lambda: get_bulk_quotes(INSTRUMENTS), get_all_quotes, get_fx_rates]


class ReconEngine:
    def __init__(
        self,
        sources: list[SourceFn] | None = None,
        adv_lookup: Callable[[str], float | None] = get_adv_usd,
        instruments: list[dict] | None = None,
    ):
        self._sources = sources if sources is not None else _default_sources()
        self._adv_lookup = adv_lookup
        self._instruments = instruments if instruments is not None else INSTRUMENTS
        self._quotes: dict[str, list[PriceQuote]] = {}  # ticker → [quotes from each source]

    # ── Data ingestion ─────────────────────────────────────────────────────

    def fetch_all(self) -> dict[str, list[PriceQuote]]:
        """Pull prices from all sources and index by ticker."""
        self._quotes = {}
        for source in self._sources:
            try:
                for q in source():
                    self._quotes.setdefault(q.ticker, []).append(q)
            except Exception as e:
                logger.exception(f"Source fetch failed: {e}")

        logger.info(
            f"Fetched prices for {len(self._quotes)} instruments "
            f"({sum(len(v) for v in self._quotes.values())} total quotes)"
        )
        return self._quotes

    # ── Reconciliation ─────────────────────────────────────────────────────

    def reconcile(self) -> list[PriceBreak]:
        """
        Cross-check every instrument that has 2+ source quotes.
        Returns one PriceBreak per source pair per instrument.
        """
        if not self._quotes:
            self.fetch_all()

        breaks: list[PriceBreak] = []
        inst_map = {i["ticker"]: i for i in self._instruments}

        for ticker, quotes in self._quotes.items():
            if len(quotes) < 2:
                continue  # only one source — nothing to reconcile

            inst = inst_map.get(ticker, {})
            asset_class = inst.get("asset_class", "default")
            base_tolerance = TOLERANCES.get(asset_class, TOLERANCES["default"])

            tier = get_liquidity_tier(ticker, asset_class, adv_usd=self._adv_lookup(ticker))
            tolerance = get_tolerance_for_tier(base_tolerance, tier)

            for i in range(len(quotes)):
                for j in range(i + 1, len(quotes)):
                    q_a, q_b = quotes[i], quotes[j]
                    if q_a.price <= 0 or q_b.price <= 0:
                        continue

                    mid = (q_a.price + q_b.price) / 2
                    diff_pct = abs(q_a.price - q_b.price) / mid * 100

                    cause, severity = classify_break(
                        ticker, asset_class, q_a, q_b, diff_pct, tolerance
                    )

                    # Skip INFO-level within-spread non-issues unless they're FX
                    if severity == "INFO" and asset_class != "fx":
                        continue

                    breaks.append(
                        PriceBreak(
                            ticker=ticker,
                            asset_class=asset_class,
                            source_a=q_a.source,
                            source_b=q_b.source,
                            price_a=round(q_a.price, 6),
                            price_b=round(q_b.price, 6),
                            diff_pct=round(diff_pct, 4),
                            tolerance_pct=round(tolerance, 4),
                            break_cause=cause,
                            severity=severity,
                            timestamp=datetime.now(UTC),
                        )
                    )

        breaks.sort(key=lambda b: (_SEVERITY_ORDER.get(b.severity, 3), b.ticker))
        logger.info(
            f"Reconciliation complete: {len(breaks)} breaks "
            f"({sum(1 for b in breaks if b.severity == 'CRITICAL')} critical)"
        )
        return breaks

    def run(self) -> tuple[dict[str, list[PriceQuote]], list[PriceBreak]]:
        """Fetch + reconcile in one call. Returns (quotes_by_ticker, breaks)."""
        quotes = self.fetch_all()
        breaks = self.reconcile()
        return quotes, breaks
