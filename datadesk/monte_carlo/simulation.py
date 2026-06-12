# Monte Carlo simulation utilities
import datetime
import os

import numpy as np
import pandas as pd


def bootstrap_returns(series: pd.Series, n: int) -> pd.Series:
    """Generate a bootstrap resampled return series of length n from given series."""
    returns = series.pct_change().dropna()
    sampled = np.random.choice(returns.values, size=n, replace=True)
    return pd.Series(sampled)


def gbm_path(series: pd.Series, mu: float, sigma: float, n: int, dt: float = 1 / 252) -> pd.Series:
    """Generate a Geometric Brownian Motion price path.
    series: original price series (used for initial price)
    mu: expected return
    sigma: volatility
    n: number of steps
    dt: time step size (default daily)
    """
    price0 = series.iloc[-1]
    shocks = np.random.normal(loc=(mu - 0.5 * sigma**2) * dt, scale=sigma * np.sqrt(dt), size=n)
    price_path = price0 * np.exp(np.cumsum(shocks))
    return pd.Series(price_path)


def run_simulation(runs: int, model: str, status_callback=None) -> str:
    """Run Monte Carlo simulations and write results to CSV.
    Returns the absolute path to the generated CSV file.
    """
    # Generate a dummy base series (e.g., synthetic price series)
    base_series = pd.Series(np.cumprod(1 + np.random.normal(0.0005, 0.02, 1000)))
    results = []
    for i in range(runs):
        if model == "bootstrap":
            price_path = bootstrap_returns(base_series, len(base_series))
        else:  # gbm
            price_path = gbm_path(base_series, mu=0.0005, sigma=0.02, n=len(base_series))
        final_price = price_path.iloc[-1]
        results.append({"run": i + 1, "model": model, "final_price": final_price})
        if status_callback:
            status_callback(i + 1)
    results_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(results_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"monte_carlo_{model}_{runs}_{timestamp}.csv"
    path = os.path.join(results_dir, filename)
    pd.DataFrame(results).to_csv(path, index=False)
    return path


# Legacy placeholder for strategy based simulation (kept for compatibility)
def run_strategy_simulation(strategy_func, price_path: pd.Series) -> dict:
    """Run the given strategy function on a price path and return performance metrics.
    strategy_func: callable that takes price_path and returns dict of metrics.
    """
    metrics = strategy_func(price_path)
    return metrics
