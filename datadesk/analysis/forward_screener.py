"""
Forward screener — finding the next breakout stock.

Combines four orthogonal signals to rank the current universe:

  1. MOMENTUM (weight 0.50)
     6-month minus 1-month return (standard cross-sectional momentum).
     Normalized to [0,1] rank within universe. Positive only.

  2. QUALITY (weight 0.30)
     Composite of revenue growth + net margin + ROE from fundamentals.
     Tickers without fundamentals score 0.50 (neutral — no penalty).

  3. CONGRESS SIGNAL (weight 0.20)
     Any member of Congress disclosed a buy in the last 45 days → +1.
     No signal → 0. Weighted by number of distinct legislators buying.

Composite = 0.50 × momentum_rank + 0.30 × quality_rank + 0.20 × congress_score

THEMATIC ACCELERATION (additive signal):
  Technology S-curves create multi-year momentum waves. When multiple tickers
  in the same theme are simultaneously showing strong momentum, the THEME ITSELF
  is likely in its acceleration phase — the best time to add exposure.

  Themes tracked:
    AI_INFRA      — chips and tools driving AI model training/inference
    QUANTUM       — quantum computing hardware and software
    DATACENTRE    — power and cooling for AI data centres
    SEMI_EQUIP    — semiconductor capital equipment (picks-and-shovels for chips)
    OPTICAL_NET   — optical networking enabling AI data transfer scale
    ENERGY_TRANS  — clean energy and hydrogen transition
    UK_FINTECH    — UK-listed financial technology

  theme_acceleration_score(): if ≥2 tickers in a theme are in momentum top-quartile,
  the theme is "hot". Hot theme → +0.05 bonus to composite for all theme members.
  This is NOT a separate portfolio — it's a tilt within the momentum ranking.

  How this finds the "next NVDA":
    - NVDA's 2014 momentum signal coincided with the GPU gaming super-cycle
    - The AI_INFRA theme going hot (NVDA + KLAC + ASML + TSM all in top momentum)
      would have been a signal that the theme was entering its acceleration phase
    - Today, watching QUANTUM theme: if IONQ + IBM + GOOGL start moving together,
      that's the early signal of the next S-curve

NEWS SENTIMENT NOTE:
  The `news_sentiment_score()` function below provides a hook for adding
  daily news sentiment as a fourth signal. Currently returns 0.5 (neutral)
  for all tickers — wire in a real NLP model or API to activate it.
  When populated, reduce momentum weight to 0.40 and add 0.10 for news.

  Good free options for news sentiment:
    - news_articles table in altdata.db (headlines already stored)
    - VADER / TextBlob on those headlines (pip install vaderSentiment)
    - FinBERT (pip install transformers) for financial-specific sentiment
    - Google news RSS + VADER for tickers not in our news corpus

Output: ranked DataFrame with columns:
  ticker, name, sector, momentum_score, quality_score, congress_score,
  composite, momentum_rank, first_signal_date, recent_congress_buys,
  index_membership
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from datadesk.config import ALTDATA_DB
from datadesk.history.store import load_closes
from datadesk.strategies.momentum import month_end_dates

# ── Thematic S-curve map ─────────────────────────────────────────────────────
# Maps tickers to their primary technology theme.
# Multiple memberships allowed — a ticker can be in more than one theme.
# Add new tickers here as the universe expands.
#
# HOW TO FIND NEW STOCKS: ETF constituent pages are the best free source.
#   SMH (semiconductors): vaneck.com/us/en/investments/semiconductor-etf-smh
#   QTUM (quantum):       defiance.com/etfs/qtum
#   DRIV (autonomous):    globalxetfs.com/funds/driv
#   CLOU (cloud):         globalxetfs.com/funds/clou
#   AIQ (AI broad):       globalxetfs.com/funds/aiq
#   BOTZ (robotics/AI):   globalxetfs.com/funds/botz

THEMES: dict[str, list[str]] = {
    # AI training & inference chips — the core S-curve
    "AI_INFRA": [
        "NVDA", "AMD", "AVGO", "MRVL", "ASML", "TSM", "KLAC", "LRCX", "AMAT",
        "MU", "MCHP", "ON", "NXPI", "GFS", "COHU", "FORM", "SMH", "6857.T",
        "005930.KS", "STM", "SNDK",
    ],
    # Quantum computing — earliest stage S-curve, 3-10 year horizon
    "QUANTUM": [
        "IONQ", "GOOGL", "IBM", "MSFT",
    ],
    # Data centre power & cooling — AI is electricity-hungry
    "DATACENTRE_POWER": [
        "NEE", "VST", "CEG", "BE", "VRT", "DLR", "EQIX", "IRM", "NXT.AX",
        "OKLO", "DTE",
    ],
    # Optical networking — fibre/photonics for AI data transfer at scale
    "OPTICAL_NET": [
        "VIAV", "CIEN", "LITE", "COHR", "GLW", "STLTECH.NS", "5801.T", "5802.T",
        "601869.SS", "NEX.PA", "PRY.MI", "TPRO.MI", "POET",
    ],
    # Semiconductor capital equipment — picks-and-shovels for every chip wave
    "SEMI_EQUIP": [
        "ASML", "KLAC", "LRCX", "AMAT", "ONTO", "COHU", "FORM", "6857.T", "8035.T",
        "STM", "IFX.DE",
    ],
    # Cloud/SaaS platforms — AI software layer
    "CLOUD_AI_SW": [
        "MSFT", "GOOG", "GOOGL", "META", "AMZN", "SNOW", "DDOG", "ZS", "NET",
        "PLTR", "CRM", "NOW", "OKTA",
    ],
    # Energy transition — clean power, hydrogen, batteries
    "ENERGY_TRANS": [
        "NEE", "ITM.L", "DRX.L", "BE", "SSE.L", "NG.L", "VG", "OKLO",
    ],
    # UK technology — often overlooked, sterling-denominated
    "UK_TECH": [
        "OXIG.L", "KNOS.L", "CCC.L", "SGE.L", "REL.L", "AUTO.L", "WISE.L",
        "BYIT.L", "RSW.L", "PCT.L", "SMT.L", "ALFA.L",
    ],
}

# Reverse lookup: ticker → list[theme]
_TICKER_THEMES: dict[str, list[str]] = {}
for _theme, _tickers in THEMES.items():
    for _t in _tickers:
        _TICKER_THEMES.setdefault(_t, []).append(_theme)


def theme_acceleration(
    prices: pd.DataFrame,
    lookback: int = 63,   # shorter window (3m) catches theme inflection faster
    top_quartile_frac: float = 0.25,
) -> dict[str, float]:
    """
    Return {theme: acceleration_score} — how many theme members are in the
    top quartile of momentum across the full universe.

    Score = fraction of theme members with positive and top-quartile momentum.
    Score > 0.5 means more than half the theme is in the top quartile → HOT.
    Score > 0.3 → WARMING. Below 0.1 → COLD.
    """
    # 3-month return, no skip (faster signal for theme detection)
    raw_mom = (prices.iloc[-1] / prices.shift(lookback).iloc[-1] - 1).dropna()
    if raw_mom.empty:
        return {}
    cutoff = raw_mom.quantile(1 - top_quartile_frac)
    top_set = set(raw_mom[raw_mom >= cutoff].index)

    scores: dict[str, float] = {}
    for theme, members in THEMES.items():
        in_universe = [t for t in members if t in raw_mom.index]
        if not in_universe:
            continue
        n_hot = sum(1 for t in in_universe if t in top_set and raw_mom.get(t, 0) > 0)
        scores[theme] = round(n_hot / len(in_universe), 3)
    return scores


def _load_fundamentals(db_path: Path) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    try:
        df = pd.read_sql(
            """
            SELECT r.ticker, i.name, i.sector,
                   r.revenue_growth, r.net_margin, r.roe,
                   r.trailing_pe, r.forward_pe, r.beta,
                   r.week52_change, r.dividend_yield
            FROM equity_ratios r
            LEFT JOIN equity_info i ON i.ticker = r.ticker
            WHERE r.fetched_at = (
                SELECT MAX(fetched_at) FROM equity_ratios WHERE ticker = r.ticker
            )
            """,
            con,
        )
    except Exception:
        df = pd.DataFrame()
    con.close()
    return df.set_index("ticker") if not df.empty else df


def _load_recent_congress_buys(db_path: Path, days: int = 45) -> pd.Series:
    """Return Series of ticker → count of distinct legislators buying in last `days` calendar days."""
    import re
    from datetime import datetime

    def _parse(s: str):
        if not s or not isinstance(s, str):
            return None
        s = s.strip()
        s = re.sub(r"([A-Za-z])(\d{4})$", r"\1 \2", s)
        for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                return pd.Timestamp(datetime.strptime(s, fmt))
            except ValueError:
                continue
        return None

    con = sqlite3.connect(db_path)
    df = pd.read_sql(
        "SELECT ticker, disclosure_date, filer_name FROM congress_trading WHERE transaction_type='buy'",
        con,
    )
    con.close()
    df["disc_dt"] = df["disclosure_date"].apply(_parse)
    df = df.dropna(subset=["disc_dt"])
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
    recent = df[df["disc_dt"] >= cutoff]
    if recent.empty:
        return pd.Series(dtype=float)
    return recent.groupby("ticker")["filer_name"].nunique()


def _load_index_memberships(db_path: Path, tickers: list[str]) -> dict[str, list[str]]:
    try:
        con = sqlite3.connect(db_path)
        df = pd.read_sql(
            f"SELECT ticker, index_ticker FROM index_memberships WHERE ticker IN ({','.join('?'*len(tickers))})",
            con, params=tickers,
        )
        con.close()
        result: dict[str, list[str]] = {}
        for _, row in df.iterrows():
            result.setdefault(row["ticker"], []).append(row["index_ticker"])
        return result
    except Exception:
        return {}


def news_sentiment_score(ticker: str, db_path: Path | None = None) -> float:
    """
    Hook for news sentiment. Returns 0.5 (neutral) until wired to a model.

    To activate:
      1. pip install vaderSentiment
      2. Load recent headlines from altdata.db news_articles WHERE ticker=ticker
      3. Run VADER on each headline, average compound scores
      4. Scale from [-1,1] to [0,1]

    Example (drop-in replacement body):
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        sia = SentimentIntensityAnalyzer()
        con = sqlite3.connect(db_path or ALTDATA_DB)
        rows = con.execute(
            "SELECT title FROM news_articles WHERE ticker=? "
            "AND published_at >= date('now','-14 days')",
            (ticker,)
        ).fetchall()
        con.close()
        if not rows:
            return 0.5
        scores = [sia.polarity_scores(r[0])['compound'] for r in rows]
        return (sum(scores) / len(scores) + 1) / 2   # scale to [0,1]
    """
    return 0.5


def rank_universe(
    db_path: Path | None = None,
    lookback: int = 126,
    skip: int = 21,
    congress_window: int = 45,
    news_weight: float = 0.0,   # 0 until news_sentiment_score() is wired
) -> pd.DataFrame:
    """
    Rank the full universe by composite multi-factor score.

    Returns a DataFrame sorted by composite descending, with all signal
    components visible for inspection.
    """
    db = db_path or ALTDATA_DB
    prices = load_closes()
    prices = prices.ffill()

    fund = _load_fundamentals(db)
    congress_buys = _load_recent_congress_buys(db, days=congress_window)

    # 1. Momentum score at last rebalance date
    signal = prices.shift(skip) / prices.shift(skip + lookback) - 1
    rebal_dates = month_end_dates(prices.index)
    last_rebal = rebal_dates[-1] if len(rebal_dates) > 0 else prices.index[-1]
    if last_rebal not in signal.index:
        last_rebal = prices.index[-1]
    mom_raw = signal.loc[last_rebal].dropna()
    mom_raw = mom_raw[mom_raw > 0]   # long-only: ignore downtrends

    tickers = list(mom_raw.index)
    index_map = _load_index_memberships(db, tickers)

    # Theme acceleration — which S-curves are hot right now?
    theme_scores = theme_acceleration(prices)

    rows = []
    for ticker in tickers:
        mom = float(mom_raw.get(ticker, 0.0))

        # Quality score
        if not fund.empty and ticker in fund.index:
            f = fund.loc[ticker]
            rev = f.get("revenue_growth") or 0.0
            nm  = f.get("net_margin") or 0.0
            roe = f.get("roe") or 0.0
            quality_raw = (
                min(max(rev, -0.5), 1.0) / 1.0 * 0.4 +    # revenue growth, capped ±50%
                min(max(nm, -0.5), 0.5) / 0.5 * 0.4 +     # net margin, capped ±50%
                min(max(roe, -0.5), 1.0) / 1.0 * 0.2      # ROE
            )
            quality_raw = max(0.0, min(quality_raw, 1.0))
            name   = f.get("name") or ticker
            sector = f.get("sector") or "—"
        else:
            quality_raw = 0.5   # neutral — no fundamental data
            name   = ticker
            sector = "—"

        # Congress signal (0 = none, 1+ = distinct legislators buying)
        n_leg = int(congress_buys.get(ticker, 0))
        congress_raw = min(n_leg / 3.0, 1.0)   # saturates at 3 legislators

        # News sentiment (neutral hook)
        news_raw = news_sentiment_score(ticker, db)

        # Theme membership and hottest theme score
        ticker_themes = _TICKER_THEMES.get(ticker, [])
        hot_theme_score = max((theme_scores.get(t, 0.0) for t in ticker_themes), default=0.0)
        themes_str = ", ".join(ticker_themes) if ticker_themes else "—"

        rows.append({
            "ticker": ticker,
            "name": name,
            "sector": sector,
            "momentum_score": round(mom * 100, 1),   # in % for readability
            "quality_score":  round(quality_raw, 3),
            "congress_score": round(congress_raw, 3),
            "news_score":     round(news_raw, 3),
            "theme_score":    round(hot_theme_score, 3),
            "themes":         themes_str,
            "n_congress_buyers": n_leg,
            "index_membership": ", ".join(index_map.get(ticker, [])) or "—",
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Rank-normalise momentum to [0,1]
    df["momentum_rank"] = df["momentum_score"].rank(pct=True)

    # Composite — momentum dominant, quality second, congress third, news fourth
    # Theme acceleration adds a small tilt (+0.05 max) on top of composite
    mom_w  = max(0.0, 0.50 - news_weight / 2)
    qual_w = 0.30
    cong_w = max(0.0, 0.20 - news_weight / 2)
    df["composite"] = (
        mom_w  * df["momentum_rank"]   +
        qual_w * df["quality_score"]   +
        cong_w * df["congress_score"]  +
        news_weight * df["news_score"] +
        0.05   * df["theme_score"]     # theme acceleration tilt (max +0.05)
    ).round(4)

    df = df.sort_values("composite", ascending=False).reset_index(drop=True)
    df.index = df.index + 1   # rank starts at 1
    return df


def print_forward_screen(df: pd.DataFrame, top_n: int = 15) -> None:
    """Print the ranked forward screener output including theme acceleration."""
    # Theme acceleration header
    from datadesk.history.store import load_closes
    try:
        prices = load_closes().ffill()
        t_scores = theme_acceleration(prices)
        hot   = {t: s for t, s in t_scores.items() if s >= 0.5}
        warm  = {t: s for t, s in t_scores.items() if 0.25 <= s < 0.5}
        cold  = {t: s for t, s in t_scores.items() if s < 0.25}
        print(f"\n{'='*105}")
        print("THEMATIC S-CURVE RADAR (3-month momentum, % of theme members in top quartile)")
        print(f"{'='*105}")
        if hot:
            print(f"  HOT   🔥  {', '.join(f'{t}({s:.0%})' for t,s in sorted(hot.items(), key=lambda x:-x[1]))}")
        if warm:
            print(f"  WARM  ~   {', '.join(f'{t}({s:.0%})' for t,s in sorted(warm.items(), key=lambda x:-x[1]))}")
        if cold:
            print(f"  COLD  —   {', '.join(f'{t}({s:.0%})' for t,s in sorted(cold.items(), key=lambda x:-x[1]))}")
        print(f"\n  HOT = S-curve likely in acceleration. Add exposure to theme members.")
        print(f"  WARM = Theme starting to move. Watch for confirmation over 1-2 months.")
        print(f"  COLD = Theme not yet moving. Good for early research, not yet a signal.")
    except Exception:
        pass

    print(f"\n{'='*105}")
    print("FORWARD SCREENER — multi-factor ranking (momentum 50% | quality 30% | congress 20% | theme tilt)")
    print(f"{'='*105}")
    print(
        f"{'#':3s} {'Ticker':8s} {'Name':26s} {'Sector':20s} "
        f"{'Mom%':7s} {'Qual':6s} {'Cong':5s} {'Theme':7s} {'Comp':6s} {'Themes'}"
    )
    print("-" * 105)
    for rank, row in df.head(top_n).iterrows():
        name = str(row["name"])[:25]
        sector = str(row["sector"])[:19]
        themes = str(row.get("themes", "—"))[:30]
        print(
            f"{rank:3d} {row['ticker']:8s} {name:26s} {sector:20s} "
            f"{row['momentum_score']:+6.1f}% {row['quality_score']:.3f} "
            f"{row['congress_score']:.3f} {row['theme_score']:.3f}  "
            f"{row['composite']:.4f} {themes}"
        )
    print(f"\nCongress signal = buys disclosed in last 45 days.")
    print("Theme score = fraction of theme peers in top-quartile momentum (hot theme → bonus weight).")
    print("NEWS SENTIMENT: not wired — see datadesk/analysis/forward_screener.py:news_sentiment_score()")
