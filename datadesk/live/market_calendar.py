"""
Market calendar: exchange hours, trading days, and session timing.

Supports the exchanges used by the two brokers in this system:
  Alpaca  → NYSE/NASDAQ  (US equities, ETFs)
  T212    → LSE          (UK equities, GBP)
  T212    → XETRA        (German/European equities)

Uses exchange_calendars for accurate holiday data.
Falls back to weekday-only logic if the package isn't available.

Execution timing philosophy (see DESIGN.md §7):
  - Daily momentum strategy signals are computed at close-of-day T
  - Target execution = same close T via MOC order (matches backtest assumption)
  - T212 fallback = market order at open T+1 (T212 has no MOC API)
  - Mid-session signals from event monitors (trump_monitor, news_monitor)
    execute immediately as market orders at signal time — these are designed
    for fast-moving, short-hold event trades where the edge is speed not price
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import NamedTuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exchange definitions
# ---------------------------------------------------------------------------

class ExchangeSpec(NamedTuple):
    calendar_name: str      # exchange_calendars key
    tz: str                 # pytz / zoneinfo timezone string
    moc_submit_time: time   # when to submit MOC orders (local exchange time)
    open_order_time: time   # when to submit next-day open orders (local exchange time)
    broker: str             # which broker handles this exchange


EXCHANGES: dict[str, ExchangeSpec] = {
    "NYSE": ExchangeSpec(
        calendar_name="XNYS",
        tz="America/New_York",
        moc_submit_time=time(15, 50),   # 10 min before 4pm close
        open_order_time=time(9, 25),    # 5 min before 9:30am open
        broker="Alpaca",
    ),
    "LSE": ExchangeSpec(
        calendar_name="XLON",
        tz="Europe/London",
        moc_submit_time=time(16, 20),   # 10 min before 4:30pm close
        open_order_time=time(7, 55),    # 5 min before 8am open
        broker="Trading212",
    ),
    "XETRA": ExchangeSpec(
        calendar_name="XETR",
        tz="Europe/Berlin",
        moc_submit_time=time(17, 20),   # 10 min before 5:30pm close
        open_order_time=time(8, 55),    # 5 min before 9am open
        broker="Trading212",
    ),
    "TSE": ExchangeSpec(
        calendar_name="XTKS",
        tz="Asia/Tokyo",
        moc_submit_time=time(15, 20),   # 10 min before 3:30pm close
        open_order_time=time(8, 55),    # 5 min before 9am open
        broker="Trading212",
    ),
    "HKEX": ExchangeSpec(
        calendar_name="XHKG",
        tz="Asia/Hong_Kong",
        moc_submit_time=time(15, 50),   # 10 min before 4pm close
        open_order_time=time(9, 25),    # 5 min before 9:30am open
        broker="Trading212",
    ),
}

# Ticker suffix → exchange routing
SUFFIX_TO_EXCHANGE = {
    ".L":  "LSE",
    ".DE": "XETRA",
    ".PA": "XETRA",   # Euronext Paris, same hours as Xetra for our purposes
    ".AS": "XETRA",   # Amsterdam
    ".MI": "XETRA",   # Milan
    ".T":  "TSE",
    ".HK": "HKEX",
    ".KS": "NYSE",    # Korean ADRs trade on NYSE when listed there
    ".SS": "NYSE",    # Shanghai-listed ADRs
}

# Default for tickers with no suffix (US stocks)
DEFAULT_EXCHANGE = "NYSE"


def ticker_exchange(ticker: str) -> str:
    for suffix, exchange in SUFFIX_TO_EXCHANGE.items():
        if ticker.upper().endswith(suffix.upper()):
            return exchange
    return DEFAULT_EXCHANGE


# ---------------------------------------------------------------------------
# Calendar queries
# ---------------------------------------------------------------------------

def _try_exchange_calendars(calendar_name: str, d: date) -> bool | None:
    """Return True/False if exchange_calendars is available, None otherwise."""
    try:
        import exchange_calendars as ec
        cal = ec.get_calendar(calendar_name)
        return cal.is_session(str(d))
    except Exception:
        return None


def is_trading_day(exchange: str = "NYSE", d: date | None = None) -> bool:
    """Return True if `d` (default: today) is a trading day on `exchange`."""
    if d is None:
        d = date.today()
    spec = EXCHANGES.get(exchange, EXCHANGES["NYSE"])

    result = _try_exchange_calendars(spec.calendar_name, d)
    if result is not None:
        return result

    # Fallback: weekdays only (misses holidays but better than nothing)
    logger.debug(f"exchange_calendars unavailable — using weekday fallback for {exchange}")
    return d.weekday() < 5


def next_trading_day(exchange: str = "NYSE", after: date | None = None) -> date:
    """Return the next trading day after `after` (default: today)."""
    d = (after or date.today()) + timedelta(days=1)
    for _ in range(14):  # never more than 2 weeks gap
        if is_trading_day(exchange, d):
            return d
        d += timedelta(days=1)
    raise RuntimeError(f"Could not find next trading day for {exchange} after {after}")


def _local_now(tz_str: str) -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(tz_str))


# ---------------------------------------------------------------------------
# Execution window checks
# ---------------------------------------------------------------------------

class ExecutionWindow(NamedTuple):
    exchange: str
    mode: str           # "moc" | "open" | "intraday" | "closed"
    fires_at_utc: datetime | None


def current_execution_windows() -> list[ExecutionWindow]:
    """
    Return which exchanges are currently in an execution window.
    Used by the rebalancer to decide what to submit right now.
    """
    windows = []
    for name, spec in EXCHANGES.items():
        local = _local_now(spec.tz)
        today = local.date()

        if not is_trading_day(name, today):
            windows.append(ExecutionWindow(name, "closed", None))
            continue

        lt = local.time()

        # MOC window: submit_time ≤ lt < close (close is submit_time + 10 min)
        moc_end = (
            datetime.combine(today, spec.moc_submit_time) + timedelta(minutes=12)
        ).time()
        if spec.moc_submit_time <= lt < moc_end:
            windows.append(ExecutionWindow(name, "moc", None))
            continue

        # Open window: open_order_time ≤ lt < open_order_time + 10 min
        open_end = (
            datetime.combine(today, spec.open_order_time) + timedelta(minutes=10)
        ).time()
        if spec.open_order_time <= lt < open_end:
            windows.append(ExecutionWindow(name, "open", None))
            continue

        windows.append(ExecutionWindow(name, "intraday", None))

    return windows


def is_moc_window(exchange: str = "NYSE") -> bool:
    for w in current_execution_windows():
        if w.exchange == exchange:
            return w.mode == "moc"
    return False


def exchange_is_open(exchange: str = "NYSE") -> bool:
    """True if the exchange is currently in a trading session."""
    spec = EXCHANGES.get(exchange, EXCHANGES["NYSE"])
    local = _local_now(spec.tz)
    if not is_trading_day(exchange, local.date()):
        return False
    # Rough open/close times (good enough for guard checks)
    ROUGH_OPENS = {
        "NYSE":  time(9, 30),
        "LSE":   time(8, 0),
        "XETRA": time(9, 0),
        "TSE":   time(9, 0),
        "HKEX":  time(9, 30),
    }
    ROUGH_CLOSES = {
        "NYSE":  time(16, 0),
        "LSE":   time(16, 30),
        "XETRA": time(17, 30),
        "TSE":   time(15, 30),
        "HKEX":  time(16, 0),
    }
    lt = local.time()
    return ROUGH_OPENS.get(exchange, time(9, 0)) <= lt <= ROUGH_CLOSES.get(exchange, time(16, 0))


# ---------------------------------------------------------------------------
# Mid-session trade guidance
# ---------------------------------------------------------------------------

def should_execute_intraday(ticker: str) -> bool:
    """
    True if the ticker's exchange is currently in session.
    Used by event-driven monitors (trump_monitor, news_monitor) to gate
    whether an intraday signal should submit immediately or be queued.
    """
    return exchange_is_open(ticker_exchange(ticker))


def route_signal(ticker: str) -> dict:
    """
    Return execution routing for a ticker:
      exchange, broker, is_open, recommended_timing
    """
    exchange = ticker_exchange(ticker)
    spec = EXCHANGES[exchange]
    is_open = exchange_is_open(exchange)
    in_moc = is_moc_window(exchange)

    if in_moc:
        timing = "moc"
    elif is_open:
        timing = "intraday_market"
    else:
        timing = "queue_for_open"

    return {
        "ticker": ticker,
        "exchange": exchange,
        "broker": spec.broker,
        "is_open": is_open,
        "in_moc_window": in_moc,
        "recommended_timing": timing,
    }
