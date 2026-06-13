"""
Exchange and market-cap based liquidity tier assignment.

Returns a dict {ticker → "L1" | "L2" | "L3"} for use with CostModel.

Tier definitions (from costs.py):
  L1 — 5bps half-spread:  large-cap US/UK, ETFs, FTSE 100
  L2 — 15bps half-spread: mid-cap US/UK, European, Japanese, HK names
  L3 — 40bps half-spread: UK AIM / US micro-cap / OTC / Pink Sheets

Exchange categories used:
  US L1 exchanges:    NMS, NYQ (NYSE Arca ETFs handled as ETF → L1)
  US L2 exchanges:    NMS/NYQ mid-cap, NMS with market cap < $1B → L2/L3
  ETF exchanges:      PCX, BTS, NGM, PCX → always L1 (index ETFs)
  UK large (LSE):     market_cap > 5B USD → L1, else L2
  UK AIM / small:     market_cap < 500M USD → L3
  European (GER/PAR/AMS/EBS/MIL) → L2
  Japan (JPX) → L2
  Hong Kong (HKG) / Singapore (SES) / Australia (ASX) → L2
  OTC (PNK, OID, OQX, NSI, CXI) → L3
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from datadesk.config import ALTDATA_DB

# Exchanges that are always ETFs — always L1 regardless of "market cap"
_ETF_EXCHANGES = {"PCX", "BTS"}

# Exchanges where we treat every listing as L2 (European mid-tier liquidity)
_L2_EXCHANGES = {"GER", "PAR", "AMS", "EBS", "MIL", "JPX", "HKG", "SES", "ASX", "KSC"}

# Exchanges that are OTC / pink-sheet → L3
_OTC_EXCHANGES = {"PNK", "OID", "OQX", "NSI", "CXI", "SHH"}

# Market cap thresholds (USD)
_L1_CAP_USD    = 5_000_000_000   # > $5B → L1 within US/UK
_L2_CAP_USD    =   500_000_000   # $500M–$5B → L2
# below $500M on US/UK exchanges → L3


def build_cost_tiers(db_path: Path | None = None) -> dict[str, str]:
    """
    Return {ticker: tier_string} from equity_info.

    For tickers not in equity_info (VIX, ETFs not in info table, etc.),
    caller falls back to CostModel.default_tier.
    """
    db = db_path or ALTDATA_DB
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT ticker, sector, exchange, market_cap FROM equity_info"
    ).fetchall()
    con.close()

    tiers: dict[str, str] = {}
    for ticker, sector, exchange, mkt_cap in rows:
        exchange = (exchange or "").upper()
        mkt_cap = mkt_cap or 0.0

        # ETFs — always L1
        if exchange in _ETF_EXCHANGES or sector is None:
            tiers[ticker] = "L1"
            continue

        # OTC / pink sheets → L3
        if exchange in _OTC_EXCHANGES:
            tiers[ticker] = "L3"
            continue

        # European / Asian exchanges → L2 flat
        if exchange in _L2_EXCHANGES:
            tiers[ticker] = "L2"
            continue

        # US & UK (NMS, NYQ, LSE, NGM, NCM, NMS, NYQ …) — tier by market cap
        if mkt_cap >= _L1_CAP_USD:
            tiers[ticker] = "L1"
        elif mkt_cap >= _L2_CAP_USD:
            tiers[ticker] = "L2"
        else:
            tiers[ticker] = "L3"

    return tiers
