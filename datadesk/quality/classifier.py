"""
Break root-cause classification.

Logic mirrors what a market data analyst does when investigating a
Bloomberg-vs-internal price discrepancy — work through the most common
causes before escalating as a genuine break.
"""

from datadesk.models import PriceQuote


def classify_break(
    ticker: str,
    asset_class: str,
    q_a: PriceQuote,
    q_b: PriceQuote,
    diff_pct: float,
    tolerance_pct: float,
) -> tuple[str, str]:
    """Return (break_cause, severity)."""

    # 1. Data quality — zero or clearly bad price
    if q_a.price <= 0 or q_b.price <= 0:
        return "DATA_QUALITY", "CRITICAL"

    # 2. Stale price — one source has is_stale flagged
    if q_a.is_stale or q_b.is_stale:
        return "STALE_PRICE", "WARNING"

    # 3. Within spread — diff is tiny, almost certainly bid/ask
    if diff_pct < 0.05:
        return "SPREAD_WITHIN_NORMAL", "INFO"

    # 4. FX rate break — affects the entire non-base-currency book
    if asset_class == "fx":
        return "GENUINE_DISCREPANCY", "CRITICAL"

    # 5. Corporate action heuristic — price diff > 40% on equity
    #    (split, special dividend, or rights issue mis-adjustment)
    if asset_class == "equity" and diff_pct > 40.0:
        return "CORPORATE_ACTION", "WARNING"

    # 6. Within tolerance — technically a break but acceptable
    if diff_pct <= tolerance_pct:
        return "SPREAD_WITHIN_NORMAL", "INFO"

    # 7. Bond prices can legitimately differ when one source uses dirty price
    if asset_class in ("govt_bond", "corp_bond") and diff_pct < 2.0:
        return "FX_CONVERSION", "WARNING"

    # 8. Genuine discrepancy — needs investigation
    severity = "CRITICAL" if diff_pct > tolerance_pct * 3 else "WARNING"
    return "GENUINE_DISCREPANCY", severity
