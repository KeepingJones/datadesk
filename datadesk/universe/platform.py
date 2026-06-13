"""
Platform universe: which tickers are tradeable on which broker accounts.

T212 ISA rules (FCA-regulated, UK ISA):
  - UK/LSE stocks (suffix .L): YES
  - EU stocks (suffix .DE, .PA, .AMS etc.): YES (limited selection)
  - US individual stocks (no suffix, e.g. AAPL, NVDA): YES — zero commission
  - US-listed ETFs (SPY, QQQ, IVV, VTI, etc.): NO — PRIIPs KID block
  - UCITS ETFs (.L suffix, e.g. CSPX.L, EQQQ.L): YES
  - Derivatives / options / shorts: NO

Alpaca (US broker, paper + live):
  - US stocks (no suffix): YES
  - US ETFs (SPY, QQQ, etc.): YES
  - UK/LSE stocks (.L): NO
  - UCITS ETFs (.L): NO
  - Options (US stocks/ETFs): YES (via options API)
  - Shorts: YES (margin account)

Dividend routing:
  - UK stocks in T212 ISA: dividends received gross, fully ISA-sheltered
  - US stocks in T212 ISA: 15% WHT withheld at source — not reclaimable
  - UK stocks in Alpaca: not available
  - US stocks in Alpaca: 15% WHT reduced to 15% with W-8BEN (down from 30%)
"""

from __future__ import annotations

# US ETFs that are blocked in T212 ISA (PRIIPs/KID)
_US_ETFS: frozenset[str] = frozenset(
    {
        # Broad market
        "SPY", "IVV", "VOO", "VTI", "ITOT", "SPTM",
        # Tech / sector
        "QQQ", "TQQQ", "SQQQ", "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI",
        "XLB", "XLRE", "XLU", "XLC",
        # Fixed income
        "TLT", "IEF", "BND", "AGG", "HYG", "LQD",
        # Commodities
        "GLD", "SLV", "USO", "UNG",
        # Factor
        "MTUM", "VLUE", "QUAL", "SIZE", "USMV",
        # International
        "EEM", "EFA", "VEA", "VWO", "IEMG",
        # Volatility
        "VXX", "UVXY",
        # Small/mid
        "IWM", "IJH", "MDY",
        # Dividend
        "VYM", "SCHD", "DVY",
    }
)

# UCITS equivalents available on T212 ISA (LSE-listed, KID-compliant)
UCITS_EQUIVALENTS: dict[str, str] = {
    "SPY": "CSPX.L",
    "IVV": "CSPX.L",
    "VOO": "VUSA.L",
    "VTI": "VWRL.L",
    "QQQ": "EQQQ.L",
    "GLD": "SGLN.L",
    "IWM": "ZPRR.L",
    "EEM": "VFEM.L",
    "EFA": "SWDA.L",
    "TLT": "IDTL.L",
    "HYG": "IHYU.L",
    "MTUM": "IUMO.L",
}


def is_uk_listed(ticker: str) -> bool:
    """Returns True for LSE-listed tickers (suffix .L)."""
    return ticker.upper().endswith(".L")


def is_us_etf(ticker: str) -> bool:
    """Returns True for known US-listed ETFs blocked in T212 ISA."""
    return ticker.upper() in _US_ETFS


def is_us_stock(ticker: str) -> bool:
    """US individual stock — no suffix, not a known ETF."""
    t = ticker.upper()
    return not t.endswith(".L") and not t.startswith("^") and t not in _US_ETFS


def available_on_alpaca(ticker: str) -> bool:
    """Alpaca trades US equities and ETFs. No UK/EU listings."""
    t = ticker.upper()
    if t.startswith("^"):  # index tickers like ^VIX — data only
        return False
    return not t.endswith(".L")


def available_on_t212_isa(ticker: str) -> bool:
    """T212 ISA: UK stocks, EU stocks, US individual stocks. Not US ETFs."""
    t = ticker.upper()
    if t.startswith("^"):
        return False
    if is_us_etf(t):
        return False
    return True


def classify(ticker: str) -> dict:
    """Return a dict describing the platform availability and routing preference."""
    t = ticker.upper()
    return {
        "ticker": t,
        "is_uk": is_uk_listed(t),
        "is_us_etf": is_us_etf(t),
        "is_us_stock": is_us_stock(t),
        "alpaca": available_on_alpaca(t),
        "t212_isa": available_on_t212_isa(t),
        "ucits_equivalent": UCITS_EQUIVALENTS.get(t),
    }


def split_by_platform(tickers: list[str]) -> dict[str, list[str]]:
    """
    Split a ticker list into three buckets:
      isa_only      — UK stocks, UCITS ETFs (.L) — only available on T212 ISA
      both          — US individual stocks — available on both, route by tax optimisation
      alpaca_only   — US ETFs — only tradeable on Alpaca
      unavailable   — index tickers etc. (data-only, not tradeable)
    """
    isa_only, both, alpaca_only, unavailable = [], [], [], []
    for t in tickers:
        if t.upper().startswith("^"):
            unavailable.append(t)
        elif is_uk_listed(t):
            isa_only.append(t)
        elif is_us_etf(t):
            alpaca_only.append(t)
        else:
            both.append(t)
    return {
        "isa_only": isa_only,
        "both": both,
        "alpaca_only": alpaca_only,
        "unavailable": unavailable,
    }
