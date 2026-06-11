import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path(os.getenv("DATADESK_DB_PATH", "datadesk.db"))
FUND_BASE_CURRENCY = os.getenv("FUND_BASE_CURRENCY", "GBP")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")

PAPER_TRADE_MODE = True  # Never change this — no live capital

# ── Reconciliation tolerances (%) ────────────────────────────────────────────
TOLERANCES = {
    "equity": 0.50,  # 50bp — intraday spread acceptable
    "fx": 0.10,  # 10bp — FX is tighter market
    "govt_bond": 0.05,  # 5bp — liquid benchmark bonds
    "corp_bond": 1.00,  # 100bp — illiquid, wider bid-ask
    "commodity": 1.00,
    "option": 2.00,  # Greeks sensitivity makes this noisy
    "volatility": 2.00,
    "default": 0.50,
}

# ── Instrument universe ──────────────────────────────────────────────────────
INSTRUMENTS = [
    # Equities — large cap across regions
    {"ticker": "AAPL", "asset_class": "equity", "currency": "USD", "name": "Apple Inc"},
    {"ticker": "MSFT", "asset_class": "equity", "currency": "USD", "name": "Microsoft"},
    {"ticker": "BP.L", "asset_class": "equity", "currency": "GBP", "name": "BP plc"},
    {"ticker": "SHEL.L", "asset_class": "equity", "currency": "GBP", "name": "Shell plc"},
    {"ticker": "9984.T", "asset_class": "equity", "currency": "JPY", "name": "SoftBank"},
    {"ticker": "SAP.DE", "asset_class": "equity", "currency": "EUR", "name": "SAP SE"},
    # FX rates
    {"ticker": "GBPUSD=X", "asset_class": "fx", "currency": "USD", "name": "GBP/USD"},
    {"ticker": "EURUSD=X", "asset_class": "fx", "currency": "USD", "name": "EUR/USD"},
    {"ticker": "USDJPY=X", "asset_class": "fx", "currency": "JPY", "name": "USD/JPY"},
    {"ticker": "USDCHF=X", "asset_class": "fx", "currency": "CHF", "name": "USD/CHF"},
    # Government bonds (ETF proxies)
    {"ticker": "TLT", "asset_class": "govt_bond", "currency": "USD", "name": "US 20Y Treasury ETF"},
    {"ticker": "IGLT.L", "asset_class": "govt_bond", "currency": "GBP", "name": "UK Gilt ETF"},
    {
        "ticker": "IBTS.L",
        "asset_class": "govt_bond",
        "currency": "EUR",
        "name": "EUR Short-Term Govts ETF",
    },
    # Corporate bonds (IG ETF proxies)
    {"ticker": "LQD", "asset_class": "corp_bond", "currency": "USD", "name": "US IG Corp Bond ETF"},
    {
        "ticker": "SLXX.L",
        "asset_class": "corp_bond",
        "currency": "GBP",
        "name": "UK Sterling Corp Bond ETF",
    },
    # Commodity futures
    {"ticker": "GC=F", "asset_class": "commodity", "currency": "USD", "name": "Gold Futures"},
    {"ticker": "CL=F", "asset_class": "commodity", "currency": "USD", "name": "WTI Crude Futures"},
    {
        "ticker": "NG=F",
        "asset_class": "commodity",
        "currency": "USD",
        "name": "Natural Gas Futures",
    },
    # Index ETF
    {"ticker": "SPY", "asset_class": "equity", "currency": "USD", "name": "S&P 500 ETF"},
    # Volatility indices
    {"ticker": "^VIX", "asset_class": "volatility", "currency": "USD", "name": "CBOE VIX"},
    {"ticker": "^VVIX", "asset_class": "volatility", "currency": "USD", "name": "VIX of VIX"},
]

# ── Liquidity tiering (L1/L2/L3) by 30-day ADV in USD ────────────────────────
LIQUIDITY_TIERS = {
    "L1": {"min_adv_usd": 100_000_000, "max_position_adv_pct": 0.10, "days_to_liquidate_max": 1},
    "L2": {"min_adv_usd": 10_000_000, "max_position_adv_pct": 0.05, "days_to_liquidate_max": 5},
    "L3": {"min_adv_usd": 0, "max_position_adv_pct": 0.02, "days_to_liquidate_max": 20},
}

# ── Break root-cause classification ──────────────────────────────────────────
BREAK_CAUSES = [
    "STALE_PRICE",  # source hasn't refreshed — TTL exceeded
    "SOURCE_OUTAGE",  # one feed is down entirely
    "CORPORATE_ACTION",  # dividend, split, spin-off
    "FX_CONVERSION",  # break only exists in base currency terms
    "SPREAD_WITHIN_NORMAL",  # within bid-ask, no action needed
    "DATA_QUALITY",  # NaN, zero, clearly wrong price
    "GENUINE_DISCREPANCY",  # real price break — escalate
]
