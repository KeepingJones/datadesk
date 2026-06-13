"""
Economic regime classifier and exposure scaler.

Extends the basic bear_only_scale (SPY < 200dMA AND VIX > 30) with a
richer three-state regime model:

  EXPANSION  — full weight (scale = 1.0)
    SPY above 150dMA AND VIX < 25 AND yield curve not deeply inverted

  CAUTION    — reduced weight (scale = 0.65)
    Any of: yield curve inverted (10Y-3M < -0.5%), VIX 25-32,
    SPY within 5% of 200dMA (approaching bear), SPY below 150dMA

  STRESS     — minimum weight (scale = 0.35)
    SPY below 200dMA AND (VIX > 32 OR yield curve deeply inverted < -1%)
    i.e. full recession/crisis mode

Yield curve proxy: ^TNX (10Y) minus ^IRX (3M Treasury Bill).
10Y-3M inversion is the most reliable recession predictor in academic
literature (Campbell Harvey, 1988). We use a free yfinance source.

Usage:
    scale = economic_regime_scale(spy, vix, yield_curve)
    weights = weights.mul(scale, axis=0)

Also exports:
    regime_series(spy, vix, yield_curve) → pd.Series of "EXPANSION" | "CAUTION" | "STRESS"
    fetch_yield_curve(start) → pd.Series of 10Y-3M spread (yfinance)
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# ── Thresholds ──────────────────────────────────────────────────────────────
_VIX_CAUTION  = 25.0    # VIX above this → at least CAUTION
_VIX_STRESS   = 32.0    # VIX above this + SPY below 200dMA → STRESS
_YC_CAUTION   = -0.50   # yield curve (10Y-3M) below this → CAUTION (in %)
_YC_STRESS    = -1.00   # yield curve below this → STRESS bias
_MA_LONG      = 200     # long-term MA for SPY
_MA_SHORT     = 150     # short-term MA — early warning

_SCALE_EXPANSION = 1.00
_SCALE_CAUTION   = 0.65
_SCALE_STRESS    = 0.35


def fetch_yield_curve(start: str = "2012-01-01") -> pd.Series:
    """
    Fetch 10Y-3M yield spread (^TNX - ^IRX) from yfinance.

    Returns a daily Series of the spread in percentage points.
    Positive = normal (long yields > short) = expansion bias.
    Negative = inverted = recession signal.
    """
    try:
        import yfinance as yf
        raw = yf.download(["^TNX", "^IRX"], start=start, progress=False, auto_adjust=True)
        close = raw["Close"] if "Close" in raw.columns else raw
        if "^TNX" not in close.columns or "^IRX" not in close.columns:
            logger.warning("yield curve: ^TNX or ^IRX missing from yfinance — returning empty")
            return pd.Series(dtype=float)
        spread = (close["^TNX"] - close["^IRX"]).dropna()
        spread.index = pd.to_datetime(spread.index).tz_localize(None)
        return spread
    except Exception as e:
        logger.warning(f"yield curve fetch failed: {e}")
        return pd.Series(dtype=float)


def regime_series(
    spy: pd.Series,
    vix: pd.Series,
    yield_curve: pd.Series | None = None,
) -> pd.Series:
    """
    Return daily regime label ('EXPANSION' | 'CAUTION' | 'STRESS').

    spy:         SPY daily close prices
    vix:         VIX daily close (^VIX)
    yield_curve: 10Y-3M spread in % points (optional — falls back to SPY+VIX only)
    """
    idx = spy.index
    ma_long  = spy.rolling(_MA_LONG,  min_periods=int(_MA_LONG  * 0.8)).mean()
    ma_short = spy.rolling(_MA_SHORT, min_periods=int(_MA_SHORT * 0.8)).mean()
    vix_r    = vix.reindex(idx).ffill()

    if yield_curve is not None and not yield_curve.empty:
        yc = yield_curve.reindex(idx).ffill()
    else:
        yc = pd.Series(0.0, index=idx)  # treat as flat when unavailable

    regimes = []
    for date in idx:
        spy_val  = spy.loc[date]
        ma_l     = ma_long.loc[date]
        ma_s     = ma_short.loc[date]
        vix_val  = vix_r.loc[date] if date in vix_r.index else 18.0
        yc_val   = yc.loc[date] if date in yc.index else 0.0

        # STRESS conditions
        below_200 = (not pd.isna(ma_l)) and (spy_val < ma_l)
        high_vix  = vix_val > _VIX_STRESS
        deep_inv  = yc_val < _YC_STRESS

        if below_200 and (high_vix or deep_inv):
            regimes.append("STRESS")
            continue

        # CAUTION conditions (any one triggers)
        below_150  = (not pd.isna(ma_s)) and (spy_val < ma_s)
        mid_vix    = vix_val > _VIX_CAUTION
        inv_curve  = yc_val < _YC_CAUTION

        if below_150 or mid_vix or inv_curve:
            regimes.append("CAUTION")
        else:
            regimes.append("EXPANSION")

    return pd.Series(regimes, index=idx, name="regime")


def economic_regime_scale(
    spy: pd.Series,
    vix: pd.Series,
    yield_curve: pd.Series | None = None,
) -> pd.Series:
    """
    Return a daily scale factor [0.35, 0.65, 1.0] for multiplying portfolio weights.

    Drop-in replacement / extension for bear_only_scale:
      bear_only_scale returned 0.4 in STRESS, else 1.0 (two states).
      This returns three states with a gentler de-risking in CAUTION.
    """
    reg = regime_series(spy, vix, yield_curve)
    scale_map = {
        "EXPANSION": _SCALE_EXPANSION,
        "CAUTION":   _SCALE_CAUTION,
        "STRESS":    _SCALE_STRESS,
    }
    return reg.map(scale_map).rename("scale")


def regime_stats(reg: pd.Series) -> dict[str, float]:
    """Summary stats for a regime series — pct of days in each state."""
    counts = reg.value_counts()
    total = len(reg)
    return {state: round(counts.get(state, 0) / total * 100, 1)
            for state in ("EXPANSION", "CAUTION", "STRESS")}
