"""
Strategy 4: Congress/Insider follow.

Trades on clustered insider Form 4 buys (k>=3 unique buyers in 14 days)
and Congressional STOCK Act purchases.
Uses `observed_at` (disclosure/filing date) to ensure point-in-time honesty.
"""

import sqlite3
from collections.abc import Callable

import pandas as pd


def insider_congress_follow(
    insider_cluster_days: int = 14,
    insider_min_buyers: int = 3,
    insider_hold_days: int = 15,
    congress_hold_days: int = 45,
    max_positions: int = 10,
) -> Callable[[pd.DataFrame], pd.DataFrame]:

    def target_weights(prices: pd.DataFrame) -> pd.DataFrame:
        weights = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)

        try:
            from datadesk.config import ALTDATA_DB
            conn = sqlite3.connect(f"file:{ALTDATA_DB}?mode=ro", uri=True)

            # Insiders (P = open market purchase)
            insiders = pd.read_sql(
                "SELECT ticker, filing_date as observed_at, filer_name FROM insiders WHERE transaction_type = 'P'",
                conn,
            )
            # Congress buys (actual DB values: 'buy', not 'Purchase')
            congress = pd.read_sql(
                "SELECT ticker, disclosure_date as observed_at, filer_name FROM congress_trading WHERE transaction_type = 'buy'",
                conn,
            )
            conn.close()
        except Exception:
            # If db is missing, return empty weights
            return weights

        # Convert to datetime — congress dates use format "24 Mar2026" (no space before year)
        import re as _re
        def _parse_date(s):
            if pd.isna(s):
                return pd.NaT
            s = str(s).strip()
            s = _re.sub(r"([A-Za-z])(\d{4})$", r"\1 \2", s)
            try:
                return pd.to_datetime(s, dayfirst=True, format="mixed")
            except Exception:
                return pd.NaT

        insiders["observed_at"] = pd.to_datetime(insiders["observed_at"], errors="coerce")
        congress["observed_at"] = congress["observed_at"].apply(_parse_date)

        insiders = insiders.dropna(subset=["observed_at"])
        congress = congress.dropna(subset=["observed_at"])

        tickers_in_universe = set(prices.columns)

        # Filter to universe
        insiders = insiders[insiders["ticker"].isin(tickers_in_universe)]
        congress = congress[congress["ticker"].isin(tickers_in_universe)]

        # --- Compute Insider Clusters ---
        # A cluster is defined as `insider_min_buyers` unique buyers within `insider_cluster_days`
        insiders = insiders.sort_values("observed_at")
        cluster_signals = []

        for ticker, group in insiders.groupby("ticker"):
            for i, row in group.iterrows():
                window_start = row["observed_at"] - pd.Timedelta(days=insider_cluster_days)
                window = group[
                    (group["observed_at"] <= row["observed_at"])
                    & (group["observed_at"] > window_start)
                ]
                if window["filer_name"].nunique() >= insider_min_buyers:
                    cluster_signals.append(
                        {"ticker": ticker, "signal_date": row["observed_at"], "type": "insider"}
                    )

        # --- Compute Congress Signals ---
        for i, row in congress.iterrows():
            cluster_signals.append(
                {"ticker": row["ticker"], "signal_date": row["observed_at"], "type": "congress"}
            )

        signals = pd.DataFrame(cluster_signals)
        if signals.empty:
            return weights

        # Group by signal_date and ticker to deduplicate multiple signals on same day
        signals["signal_date"] = signals["signal_date"].dt.floor("D")

        holding: dict[str, tuple[int, str]] = {}  # ticker -> (days_held, type)

        price_dates = pd.Series(prices.index)

        for i, original_date in enumerate(prices.index):
            current_date = pd.Timestamp(original_date)

            # Age existing positions
            expired = []
            for t, (days, stype) in holding.items():
                max_hold = insider_hold_days if stype == "insider" else congress_hold_days
                if days >= max_hold:
                    expired.append(t)
                else:
                    holding[t] = (days + 1, stype)

            for t in expired:
                del holding[t]

            # Add new positions based on signals observed *on or before* this date (up to 1 day lag)
            # Actually, to be strictly point-in-time, we can only act on a signal the day AFTER it is observed
            # (assuming observed_at is EOD, we trade next open/close).
            # We'll just look for signals on current_date and enter them (they will be held next day)
            day_signals = signals[signals["signal_date"] == current_date]

            for _, row in day_signals.iterrows():
                t = row["ticker"]
                stype = row["type"]
                if len(holding) < max_positions and t not in holding:
                    holding[t] = (0, stype)

            if holding:
                active_tickers = list(holding.keys())
                weights.loc[original_date, active_tickers] = 1.0 / max_positions

        return weights

    return target_weights
