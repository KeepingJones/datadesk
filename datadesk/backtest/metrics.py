"""Performance metrics. All take a Series of daily net returns."""

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def equity_curve(returns: pd.Series) -> pd.Series:
    return (1 + returns.fillna(0)).cumprod()


def cagr(returns: pd.Series) -> float:
    if len(returns) == 0:
        return 0.0
    total = float(equity_curve(returns).iloc[-1])
    years = len(returns) / TRADING_DAYS
    if years <= 0 or total <= 0:
        return 0.0
    return total ** (1 / years) - 1


def sharpe(returns: pd.Series, rf_annual: float = 0.0) -> float:
    if len(returns) < 2:
        return 0.0
    excess = returns - rf_annual / TRADING_DAYS
    sd = float(excess.std())
    if sd < 1e-12:  # constant series: float noise, not real vol
        return 0.0
    return float(excess.mean() / sd * np.sqrt(TRADING_DAYS))


def sortino(returns: pd.Series, rf_annual: float = 0.0) -> float:
    if len(returns) < 2:
        return 0.0
    excess = returns - rf_annual / TRADING_DAYS
    downside = excess[excess < 0]
    dd = float(downside.std())
    if len(downside) < 2 or dd < 1e-12:
        return 0.0
    return float(excess.mean() / dd * np.sqrt(TRADING_DAYS))


def max_drawdown(returns: pd.Series) -> float:
    """Maximum peak-to-trough drawdown, returned as a NEGATIVE fraction."""
    curve = equity_curve(returns)
    peak = curve.cummax()
    return float(((curve - peak) / peak).min())


def calmar(returns: pd.Series) -> float:
    mdd = abs(max_drawdown(returns))
    if mdd == 0:
        return 0.0
    return cagr(returns) / mdd


def summarize(
    returns: pd.Series, 
    turnover: pd.Series | None = None,
    benchmark_returns: pd.DataFrame | None = None,
) -> dict:
    out = {
        "cagr": round(cagr(returns), 4),
        "sharpe": round(sharpe(returns), 3),
        "sortino": round(sortino(returns), 3),
        "max_drawdown": round(max_drawdown(returns), 4),
        "calmar": round(calmar(returns), 3),
        "days": len(returns),
    }
    if turnover is not None and len(turnover):
        out["avg_annual_turnover"] = round(float(turnover.mean()) * TRADING_DAYS, 2)
        
    if benchmark_returns is not None and not benchmark_returns.empty:
        for bm in benchmark_returns.columns:
            bm_ret = benchmark_returns[bm].dropna()
            aligned = pd.concat([returns, bm_ret], axis=1, join='inner').dropna()
            if not aligned.empty and len(aligned) > 50:
                strat_cagr = cagr(aligned.iloc[:, 0])
                bm_cagr = cagr(aligned.iloc[:, 1])
                out[f"outperform_{bm}"] = round(strat_cagr - bm_cagr, 4)
                out[f"corr_{bm}"] = round(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]), 3)

    return out
