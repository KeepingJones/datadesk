"""
Signal genesis audit — look-ahead bias analysis.

Answers the question: "Was this holding selected by real-time momentum, or do
we only own it because we know it became great?"

For each ticker in the price universe, finds:
  - first_signal_date: first month-end where 6-1 momentum was POSITIVE
  - price_at_signal: closing price on that date
  - current_price: most recent close
  - pct_gain_from_signal: return from signal date to today (strategy-captured)
  - pct_total_gain: total return over the full price history
  - frac_captured: fraction of possible gain earned by following the signal

A ticker where first_signal coincides with the backtest start (i.e., it was
already in strong uptrend day one) is likely to be survivorship-biased —
the strategy would have had to be tracking it from the beginning.

A ticker where first_signal is AFTER backtest start means the momentum engine
genuinely discovered it during the period — this is real-time alpha.

Universe construction bias: any ticker where first_price is AFTER backtest
start was added to the universe with foreknowledge of its existence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from datadesk.strategies.momentum import month_end_dates


@dataclass
class TickerSignalRecord:
    ticker: str
    first_price_date: str
    first_signal_date: str | None
    price_at_signal: float | None
    current_price: float
    pct_gain_from_signal: float | None
    pct_total_gain: float
    frac_captured: float | None
    backtest_start: str
    look_ahead_added: bool   # True if first_price_date > backtest_start


def run_signal_audit(
    prices: pd.DataFrame,
    backtest_start: str,
    lookback: int = 126,
    skip: int = 21,
) -> list[TickerSignalRecord]:
    """
    Audit every ticker for when momentum first turned positive and how much
    of the total available return the signal captured.
    """
    start_dt = pd.Timestamp(backtest_start)
    signal = prices.shift(skip) / prices.shift(skip + lookback) - 1

    rebal_dates = month_end_dates(prices.index)

    records: list[TickerSignalRecord] = []
    for ticker in prices.columns:
        col = prices[ticker].dropna()
        if col.empty:
            continue

        first_price_date = str(col.index[0].date())
        current_price = float(col.iloc[-1])
        first_bar_ts = col.index[0]

        # Was this ticker added AFTER the backtest started? (selection look-ahead)
        look_ahead_added = first_bar_ts > start_dt

        # When did the 6-1 momentum score first turn positive?
        sig_col = signal[ticker].dropna()
        positive_signals = sig_col[sig_col > 0]
        # Only consider rebalance dates after warmup
        warmup_end = prices.index[min(lookback + skip + 5, len(prices) - 1)]
        positive_on_rebal = [d for d in rebal_dates if d in positive_signals.index
                             and positive_signals.loc[d] > 0
                             and d >= warmup_end]

        if positive_on_rebal:
            first_sig = positive_on_rebal[0]
            price_at_sig = float(prices.loc[first_sig, ticker])
            gain_from_signal = (current_price / price_at_sig - 1) * 100
        else:
            first_sig = None
            price_at_sig = None
            gain_from_signal = None

        pct_total_gain = (current_price / float(col.iloc[0]) - 1) * 100
        frac_captured = (
            (gain_from_signal / pct_total_gain)
            if gain_from_signal is not None and pct_total_gain != 0
            else None
        )

        records.append(TickerSignalRecord(
            ticker=ticker,
            first_price_date=first_price_date,
            first_signal_date=str(first_sig.date()) if first_sig else None,
            price_at_signal=round(price_at_sig, 4) if price_at_sig else None,
            current_price=round(current_price, 4),
            pct_gain_from_signal=round(gain_from_signal, 1) if gain_from_signal is not None else None,
            pct_total_gain=round(pct_total_gain, 1),
            frac_captured=round(frac_captured, 3) if frac_captured is not None else None,
            backtest_start=backtest_start,
            look_ahead_added=look_ahead_added,
        ))

    return sorted(records, key=lambda r: r.pct_total_gain, reverse=True)


def print_signal_audit(records: list[TickerSignalRecord], top_n: int = 20) -> None:
    """Print a formatted signal genesis report."""
    print(f"\n{'='*90}")
    print("SIGNAL GENESIS AUDIT — when did real-time momentum first identify each winner?")
    print(f"{'='*90}")
    print(
        f"{'Ticker':8s} {'1st Price':12s} {'1st Signal':12s} {'Price@Sig':10s} "
        f"{'Total%':8s} {'From Sig%':9s} {'Captured':9s} {'Look-ahead?':12s}"
    )
    print("-" * 90)
    for r in records[:top_n]:
        la = "YES*" if r.look_ahead_added else "no"
        sig_date = r.first_signal_date or "no signal"
        price_sig = f"${r.price_at_signal:.2f}" if r.price_at_signal else "—"
        total = f"{r.pct_total_gain:+.0f}%"
        from_sig = f"{r.pct_gain_from_signal:+.0f}%" if r.pct_gain_from_signal is not None else "—"
        captured = f"{r.frac_captured*100:.0f}%" if r.frac_captured is not None else "—"
        print(
            f"{r.ticker:8s} {r.first_price_date:12s} {sig_date:12s} {price_sig:10s} "
            f"{total:8s} {from_sig:9s} {captured:9s} {la:12s}"
        )
    la_count = sum(1 for r in records if r.look_ahead_added)
    if la_count:
        print(f"\n* {la_count} tickers have price history starting AFTER backtest_start")
        print("  → selection look-ahead: we added them knowing they existed and/or succeeded")
    print()
