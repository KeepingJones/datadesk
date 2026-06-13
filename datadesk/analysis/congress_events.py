"""
Congress trading event study.

Measures forward stock returns after Congressional disclosure dates (not transaction
dates — disclosures can come up to 45 days after the trade, so we use the date the
signal became publicly knowable).

Returns a CongressEventStudy with:
  - per-ticker average abnormal returns vs SPY at [+1, +5, +20, +45] trading days
  - summary statistics by transaction type (buy/sell)
  - top alpha-generating legislators
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from datadesk.config import ALTDATA_DB
from datadesk.history.store import load_closes


_HOLD_WINDOWS = [1, 5, 20, 45]


def _parse_congress_date(s: str) -> pd.Timestamp | None:
    """Parse dates like '24 Mar2026' or '1 Apr2026'."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    # Insert space before 4-digit year if missing: "Mar2026" → "Mar 2026"
    import re
    s = re.sub(r"([A-Za-z])(\d{4})$", r"\1 \2", s)
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return pd.Timestamp(datetime.strptime(s, fmt))
        except ValueError:
            continue
    return None


@dataclass
class CongressEventStudy:
    n_events: int
    n_tickers: int
    windows: list[int]
    # avg forward return by window for buys vs sells (keys: "buy", "sell")
    avg_returns: dict[str, dict[int, float]]
    # avg abnormal return (vs SPY) by window
    avg_abnormal: dict[str, dict[int, float]]
    # top tickers by avg abnormal return (buy events, 20-day window)
    top_tickers: list[dict]
    # top legislators by number of buys with positive 20d outcome
    top_legislators: list[dict]
    raw: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)


def run_congress_event_study(
    db_path: Path | None = None,
    min_history_rows: int = 252,
) -> CongressEventStudy:
    """
    Load congress_trading, align with price history, compute forward returns.
    Only includes tickers that have price data in history.db.
    """
    db = db_path or ALTDATA_DB

    con = sqlite3.connect(db)
    df = pd.read_sql(
        "SELECT ticker, disclosure_date, transaction_date, filer_name, "
        "transaction_type, amount_range FROM congress_trading "
        "WHERE transaction_type IN ('buy', 'sell')",
        con,
    )
    con.close()

    df["disc_dt"] = df["disclosure_date"].apply(_parse_congress_date)
    df = df.dropna(subset=["disc_dt"])
    df["disc_dt"] = pd.to_datetime(df["disc_dt"]).dt.normalize()

    # Load prices for all tickers in congress data
    tickers_needed = df["ticker"].unique().tolist()
    prices = load_closes(tickers=tickers_needed + ["SPY"])
    available = set(prices.columns) - {"SPY"}

    df = df[df["ticker"].isin(available)].copy()
    df = df[(df["disc_dt"] >= prices.index[0]) & (df["disc_dt"] <= prices.index[-2])]

    spy_rets = prices["SPY"].pct_change() if "SPY" in prices.columns else None

    results: list[dict] = []
    for _, row in df.iterrows():
        t = row["ticker"]
        disc = row["disc_dt"]
        if t not in prices.columns:
            continue

        # Find position of disc date in price index
        idx_candidates = prices.index.searchsorted(disc)
        if idx_candidates >= len(prices.index):
            continue
        signal_idx = idx_candidates  # entry at next day's open = price at signal_idx

        entry_row = {
            "ticker": t,
            "disc_date": disc,
            "tx_type": row["transaction_type"],
            "filer": row["filer_name"],
            "amount": row["amount_range"],
        }
        for w in _HOLD_WINDOWS:
            exit_idx = signal_idx + w
            if exit_idx >= len(prices):
                entry_row[f"ret_{w}d"] = None
                entry_row[f"abn_{w}d"] = None
                continue
            entry_price = prices[t].iloc[signal_idx]
            exit_price  = prices[t].iloc[exit_idx]
            if pd.isna(entry_price) or pd.isna(exit_price) or entry_price == 0:
                entry_row[f"ret_{w}d"] = None
                entry_row[f"abn_{w}d"] = None
                continue
            ret = exit_price / entry_price - 1
            # SPY return over same window
            spy_ret = None
            if spy_rets is not None:
                spy_slice = spy_rets.iloc[signal_idx + 1: exit_idx + 1]
                spy_ret = float((1 + spy_slice).prod() - 1) if not spy_slice.empty else None
            entry_row[f"ret_{w}d"] = ret
            entry_row[f"abn_{w}d"] = (ret - spy_ret) if spy_ret is not None else ret
        results.append(entry_row)

    raw = pd.DataFrame(results)
    if raw.empty:
        return CongressEventStudy(
            n_events=0, n_tickers=0, windows=_HOLD_WINDOWS,
            avg_returns={}, avg_abnormal={}, top_tickers=[], top_legislators=[],
        )

    avg_returns: dict[str, dict[int, float]] = {}
    avg_abnormal: dict[str, dict[int, float]] = {}
    for tx in ("buy", "sell"):
        sub = raw[raw["tx_type"] == tx]
        avg_returns[tx]  = {w: float(sub[f"ret_{w}d"].dropna().mean()) for w in _HOLD_WINDOWS}
        avg_abnormal[tx] = {w: float(sub[f"abn_{w}d"].dropna().mean()) for w in _HOLD_WINDOWS}

    # Top tickers by avg abnormal return on buy events (20d window)
    buy_events = raw[raw["tx_type"] == "buy"].copy()
    if not buy_events.empty:
        ticker_stats = (
            buy_events.groupby("ticker")["abn_20d"]
            .agg(["mean", "count"])
            .rename(columns={"mean": "avg_abn_20d", "count": "n_events"})
            .query("n_events >= 2")
            .sort_values("avg_abn_20d", ascending=False)
            .head(20)
            .reset_index()
        )
        top_tickers = ticker_stats.to_dict("records")
    else:
        top_tickers = []

    # Top legislators by win rate on buys (positive 20d abnormal return)
    if not buy_events.empty:
        buy_events["win"] = buy_events["abn_20d"] > 0
        leg_stats = (
            buy_events.groupby("filer")
            .agg(n_buys=("ticker", "count"), win_rate=("win", "mean"), avg_abn=("abn_20d", "mean"))
            .query("n_buys >= 3")
            .sort_values("avg_abn", ascending=False)
            .head(15)
            .reset_index()
        )
        top_legislators = leg_stats.to_dict("records")
    else:
        top_legislators = []

    return CongressEventStudy(
        n_events=len(raw),
        n_tickers=raw["ticker"].nunique(),
        windows=_HOLD_WINDOWS,
        avg_returns=avg_returns,
        avg_abnormal=avg_abnormal,
        top_tickers=top_tickers,
        top_legislators=top_legislators,
        raw=raw,
    )
