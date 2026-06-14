import os
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv

load_dotenv()

# Directories
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def setup_logging(name: str, log_filename: str) -> logging.Logger:
    """Configures and returns a logger that writes to both console and a rotating log file."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers if setup_logging is called multiple times
    if not logger.handlers:
        formatter = logging.Formatter('%(asctime)s:%(name)s:%(levelname)s: %(message)s')
        
        # Console Handler
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        logger.addHandler(ch)
        
        # File Handler (10MB max size, keep 3 backups)
        log_path = LOG_DIR / log_filename
        fh = RotatingFileHandler(log_path, maxBytes=10*1024*1024, backupCount=3, encoding='utf-8')
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        
    return logger

DB_PATH = DATA_DIR / os.getenv("DATADESK_DB_PATH", "datadesk.db")
ALTDATA_DB = DATA_DIR / os.getenv("ALTDATA_DB_PATH", "altdata.db")
FUND_BASE_CURRENCY = os.getenv("FUND_BASE_CURRENCY", "GBP")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
TIINGO_API_KEY = os.getenv("TIINGO_API_KEY", "")

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
