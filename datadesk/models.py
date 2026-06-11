from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PriceQuote(BaseModel):
    ticker: str
    source: str  # "yahoo", "fred", "ecb"
    asset_class: str
    currency: str
    price: float
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    timestamp: datetime = Field(default_factory=_utcnow)
    is_stale: bool = False


class PriceBreak(BaseModel):
    ticker: str
    asset_class: str
    source_a: str
    source_b: str
    price_a: float
    price_b: float
    diff_pct: float  # abs % difference vs mid
    tolerance_pct: float
    break_cause: str  # from config.BREAK_CAUSES
    severity: str  # "INFO" | "WARNING" | "CRITICAL"
    timestamp: datetime = Field(default_factory=_utcnow)
    resolved: bool = False


class Instrument(BaseModel):
    ticker: str
    asset_class: str
    currency: str
    name: str
    liquidity_tier: str | None = None  # L1 / L2 / L3
    adv_usd_30d: float | None = None


class FXRate(BaseModel):
    pair: str  # e.g. "GBPUSD"
    rate: float
    source: str
    timestamp: datetime = Field(default_factory=_utcnow)
