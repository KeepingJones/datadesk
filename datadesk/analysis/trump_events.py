"""
Trump social-media post event study.

Classifies each Truth Social post by market impact category, then measures
abnormal market returns over [+1, +2, +5, +10, +20] trading days.

Two classification approaches are combined:
  1. Keyword rules — fast, reliable for known patterns
  2. post_classifier.py — deterministic v3 taxonomy with Ollama fallback

Categories studied:
  TARIFF / TRADE WAR  → impact on SPY, XLI, XLK
  TAX / STIMULUS      → impact on SPY, XLF, small caps
  DEREGULATION        → impact on XLF, energy, healthcare
  SANCTIONS / IRAN    → impact on oil (XLE), defence
  COMPANY / SECTOR    → ticker-specific impact
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from datadesk.config import ALTDATA_DB
from datadesk.history.store import load_closes


_HOLD_WINDOWS = [1, 2, 5, 10, 20]

# Keyword rules — these are fast and more reliable than the AI classifier
# for historical analysis of well-known post types
_KEYWORD_RULES: list[tuple[str, str]] = [
    # (pattern, category)
    (r"\b(tariff|tariffs|import tax|trade war|trade deal|trade deficit)\b", "TARIFF_TRADE"),
    (r"\b(tax cut|tax reform|tax reduction|tax rate|lower taxes|tax plan)\b", "TAX_STIMULUS"),
    (r"\b(stimulus|infrastructure|spending bill|economic package)\b", "TAX_STIMULUS"),
    (r"\b(deregulat|rolling back regulation|regulation|EPA|FDA approval)\b", "DEREGULATION"),
    (r"\b(sanction|iran|russia|china|north korea|venezuela)\b", "GEOPOLITICAL"),
    (r"\b(rate|federal reserve|fed|interest rate|powell|inflation)\b", "MONETARY"),
    (r"\b(nasdaq|dow|s&p|stock market|stocks are|market is)\b", "MARKET_DIRECT"),
]

_IMPACT_TICKERS: dict[str, list[str]] = {
    "TARIFF_TRADE":   ["SPY", "XLI", "XLK", "XLF"],
    "TAX_STIMULUS":   ["SPY", "XLF", "XLK"],
    "DEREGULATION":   ["XLF", "XLE", "XLV"],
    "GEOPOLITICAL":   ["SPY", "GLD", "XLE"],
    "MONETARY":       ["SPY", "XLF", "TLT"],
    "MARKET_DIRECT":  ["SPY"],
    "NOISE":          ["SPY"],
}


def _keyword_classify(text: str) -> str:
    if not text:
        return "NOISE"
    lower = text.lower()
    for pattern, cat in _KEYWORD_RULES:
        if re.search(pattern, lower, re.IGNORECASE):
            return cat
    return "NOISE"


@dataclass
class TrumpEventStudy:
    n_posts: int
    n_actionable: int
    windows: list[int]
    # avg abnormal SPY return by category and window
    category_abnormal: dict[str, dict[int, float]]
    # category counts
    category_counts: dict[str, int]
    # best and worst single-day reactions
    top_events: list[dict]
    raw: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)


def run_trump_event_study(
    db_path: Path | None = None,
    min_content_len: int = 80,
) -> TrumpEventStudy:
    """
    Load trump_posts, classify, and compute forward returns after each post.
    """
    db = db_path or ALTDATA_DB
    con = sqlite3.connect(db)
    posts = pd.read_sql(
        f"SELECT id, created_at, content FROM trump_posts WHERE LENGTH(content) >= {min_content_len}",
        con,
    )
    con.close()

    posts["ts"] = pd.to_datetime(posts["created_at"], utc=True, errors="coerce")
    posts = posts.dropna(subset=["ts"])
    posts["date"] = posts["ts"].dt.tz_localize(None).dt.normalize()
    posts["category"] = posts["content"].apply(_keyword_classify)

    # Load SPY and key sector ETFs
    etfs_needed = list({t for tickers in _IMPACT_TICKERS.values() for t in tickers})
    prices = load_closes(tickers=etfs_needed)
    spy_rets = prices["SPY"].pct_change() if "SPY" in prices.columns else None

    # De-duplicate: one event per (date, category) — avoid double-counting multiple
    # posts on the same day in the same category
    posts_dedup = (
        posts.groupby(["date", "category"])
        .first()
        .reset_index()
    )
    posts_dedup = posts_dedup[
        (posts_dedup["date"] >= prices.index[0])
        & (posts_dedup["date"] <= prices.index[-2])
    ]

    results: list[dict] = []
    for _, row in posts_dedup.iterrows():
        cat = row["category"]
        post_date = row["date"]
        signal_idx = prices.index.searchsorted(post_date)
        if signal_idx >= len(prices.index):
            continue

        entry = {
            "date": post_date,
            "category": cat,
            "content_snippet": str(row["content"])[:120],
        }
        for w in _HOLD_WINDOWS:
            exit_idx = signal_idx + w
            if exit_idx >= len(prices) or spy_rets is None:
                entry[f"spy_{w}d"] = None
                continue
            spy_slice = spy_rets.iloc[signal_idx + 1: exit_idx + 1]
            entry[f"spy_{w}d"] = float((1 + spy_slice).prod() - 1) if not spy_slice.empty else None
        results.append(entry)

    raw = pd.DataFrame(results)
    if raw.empty:
        return TrumpEventStudy(
            n_posts=len(posts), n_actionable=0, windows=_HOLD_WINDOWS,
            category_abnormal={}, category_counts={}, top_events=[], raw=raw,
        )

    # Baseline: average SPY forward return on all trading days (unconditional)
    baseline: dict[int, float] = {}
    for w in _HOLD_WINDOWS:
        all_rets = []
        for i in range(len(prices) - w):
            if spy_rets is not None:
                s = spy_rets.iloc[i + 1: i + w + 1]
                all_rets.append(float((1 + s).prod() - 1))
        baseline[w] = sum(all_rets) / len(all_rets) if all_rets else 0.0

    category_abnormal: dict[str, dict[int, float]] = {}
    category_counts: dict[str, int] = {}
    for cat in raw["category"].unique():
        sub = raw[raw["category"] == cat]
        category_counts[cat] = len(sub)
        category_abnormal[cat] = {}
        for w in _HOLD_WINDOWS:
            col = f"spy_{w}d"
            vals = sub[col].dropna()
            if vals.empty:
                category_abnormal[cat][w] = 0.0
            else:
                # Abnormal = category avg minus unconditional baseline
                category_abnormal[cat][w] = float(vals.mean()) - baseline[w]

    n_actionable = int((raw["category"] != "NOISE").sum())

    # Top 10 largest 1-day moves after each post type
    top_events = []
    if not raw.empty and "spy_1d" in raw.columns:
        top_df = pd.concat([
            raw.nlargest(5, "spy_1d").assign(direction="up"),
            raw.nsmallest(5, "spy_1d").assign(direction="down"),
        ])
        top_events = top_df[["date", "category", "spy_1d", "content_snippet", "direction"]].to_dict("records")

    return TrumpEventStudy(
        n_posts=len(posts),
        n_actionable=n_actionable,
        windows=_HOLD_WINDOWS,
        category_abnormal=category_abnormal,
        category_counts=category_counts,
        top_events=top_events,
        raw=raw,
    )
