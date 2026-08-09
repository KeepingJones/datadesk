"""
Strategy v3 — Dynamic Multi-Factor & Volatility-Targeted Momentum.

Two building blocks, designed to compose the same way the v2 stack does
(base selector weights → regime scale → vol target), so the existing backtest
harness (run_backtest / walk_forward / vol_target_weights) drives it unchanged:

  1. residual_momentum(...)  → target_weights(prices) closure
       Cross-sectional 12-1 momentum computed on SPY-beta-RESIDUAL returns
       (each stock's return orthogonalised against the market), so the signal
       ranks "what rose beyond its market beta" rather than "what rose" — the
       component that survives growth→value/defensive factor rotations better
       than raw price momentum (plan-strategy-v3.md §3 Component C). Reuses
       momentum.py's monthly-rebalance, long-only, quality-filter and
       cash-when-nothing-qualifies conventions exactly.

  2. regime_tier_scale(spy, vix)  → per-day scale in {1.0, 0.7, 0.4, 0.1}
       The 4-tier graduated stress overlay (plan §3 Component A), replacing
       v2's binary bear_only_scale (1.0 / 0.4). Thresholds are monotonic
       ladders with most-cautious-wins precedence, which reproduces the plan's
       tier table for every labelled row AND resolves the one case the literal
       bands leave undefined — VIX ≥ 30 while SPY is still above its 200dMA
       maps to Bear (0.40), not back to full exposure.

Component B (15% vol targeting) is NOT re-implemented here — it already exists
as backtest.vol_target.vol_target_weights and is applied on top of these two,
exactly as the harness applies it to v2.
"""

from collections.abc import Callable, Collection

import pandas as pd

from datadesk.strategies.momentum import month_end_dates

# Tier exposure scales (plan-strategy-v3.md §3 Component A)
TIER_NORMAL = 1.00
TIER_CAUTION = 0.70
TIER_BEAR = 0.40
TIER_CRISIS = 0.10


def regime_tier_scale(
    spy: pd.Series,
    vix: pd.Series,
    ma_short: int = 50,
    ma_long: int = 200,
    vix_caution: float = 20.0,
    vix_bear: float = 25.0,
    vix_crisis: float = 30.0,
) -> pd.Series:
    """4-tier graduated stress overlay → per-day gross-exposure scale.

    | Tier    | Condition                    | Scale |
    |---------|------------------------------|-------|
    | Normal  | SPY > 50dMA  AND VIX < 20     | 1.00  |
    | Caution | SPY < 50dMA  OR  20 ≤ VIX<25  | 0.70  |
    | Bear    | SPY < 200dMA OR  25 ≤ VIX<30  | 0.40  |
    | Crisis  | SPY < 200dMA AND VIX ≥ 30     | 0.10  |

    Implemented as monotonic VIX thresholds with most-cautious-wins precedence:
    a more severe condition overwrites a milder one, so the labelled bands above
    fall out exactly, while VIX ≥ 30 with SPY > 200dMA resolves to Bear (0.40)
    rather than slipping back to full exposure — the gap the literal bands leave.
    During MA warm-up (`SPY < NaN` is False) the day stays at Normal.
    """
    vix = vix.reindex(spy.index).ffill()
    below_short = spy < spy.rolling(ma_short).mean()
    below_long = spy < spy.rolling(ma_long).mean()

    scale = pd.Series(TIER_NORMAL, index=spy.index)
    # assign least → most severe; later writes win (most cautious overlay)
    scale[below_short | (vix >= vix_caution)] = TIER_CAUTION
    scale[below_long | (vix >= vix_bear)] = TIER_BEAR
    scale[below_long & (vix >= vix_crisis)] = TIER_CRISIS
    return scale.fillna(TIER_NORMAL)


def residual_momentum(
    lookback: int = 126,
    top_n: int = 10,
    skip: int = 21,
    quality_universe: Collection[str] | None = None,
    benchmark: str = "SPY",
) -> Callable[[pd.DataFrame], pd.DataFrame]:
    """Cross-sectional residual (market-neutral) momentum, monthly rebalance.

    At each month end, over the 12-1 window (days `skip` .. `skip+lookback` back):
        beta_i  = cov(r_i, r_mkt) / var(r_mkt)          per stock, that window
        score_i = Σ r_i − beta_i · Σ r_mkt              cumulative residual return
    Rank by score, take the top_n with score > 0 (long-only), equal-weight
    1/top_n each (cash for empty slots). Same output contract as momentum():
    a daily weights frame, ffilled between rebalances.

    The `benchmark` column is used for orthogonalisation and is never itself
    selected. If it's absent the score degrades to raw cumulative momentum
    (beta = 0), so the closure still runs on benchmark-free frames.
    `quality_universe`, if given, restricts eligible names before ranking.
    """
    _qset = frozenset(quality_universe) if quality_universe else None

    def target_weights(prices: pd.DataFrame) -> pd.DataFrame:
        rets = prices.pct_change(fill_method=None)
        has_mkt = benchmark in prices.columns
        pos = {d: i for i, d in enumerate(prices.index)}

        rows = {}
        for date in month_end_dates(prices.index):
            loc = pos.get(date)
            lo = None if loc is None else loc - (skip + lookback)
            hi = None if loc is None else loc - skip
            if loc is None or lo is None or lo < 0:
                rows[date] = pd.Series(0.0, index=prices.columns)
                continue

            win = rets.iloc[lo:hi]
            scores = win.sum()
            if has_mkt:
                m = win[benchmark]
                m_dev = m - m.mean()
                var_m = float((m_dev * m_dev).mean())
                if var_m > 0:
                    cov = win.sub(win.mean()).mul(m_dev, axis=0).mean()
                    beta = cov / var_m
                    scores = win.sum() - beta * float(m.sum())
                scores = scores.drop(labels=[benchmark], errors="ignore")

            scores = scores.dropna()
            if _qset is not None:
                scores = scores[scores.index.isin(_qset)]
            scores = scores[scores > 0]  # long-only: no negative residual momentum

            w = pd.Series(0.0, index=prices.columns)
            if not scores.empty:
                top = scores.nlargest(top_n)
                w[top.index] = 1.0 / top_n  # cash remainder if fewer than N qualify
            rows[date] = w

        df = pd.DataFrame(rows).T.sort_index()
        return df.reindex(prices.index).ffill().fillna(0.0)

    return target_weights
