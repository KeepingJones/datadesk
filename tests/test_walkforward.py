import numpy as np
import pandas as pd

from datadesk.backtest.walkforward import grid, walk_forward
from datadesk.strategies.momentum import momentum


def test_grid_expands_combinations():
    g = grid(lookback=[63, 126], top_n=[5, 10])
    assert len(g) == 4
    assert {"lookback": 63, "top_n": 10} in g


def test_walk_forward_runs_and_stitches():
    rng = np.random.default_rng(11)
    n = 1400
    idx = pd.bdate_range("2019-01-01", periods=n)
    prices = pd.DataFrame(
        {
            "UP": 100 * 1.0008 ** np.arange(n),
            "DOWN": 100 * 0.9995 ** np.arange(n),
            "NOISY": 100 * np.cumprod(1 + rng.normal(0.0002, 0.012, n)),
        },
        index=idx,
    )
    result = walk_forward(
        prices,
        strategy_factory=momentum,
        param_grid=grid(lookback=[63, 126], top_n=[1, 2]),
        train_days=400,
        test_days=120,
        warmup_days=160,
    )
    assert len(result.segments) >= 3
    assert len(result.returns) > 300
    assert 0 < result.param_stability <= 1.0
    # every segment chose params from the grid and reports test metrics
    for seg in result.segments:
        assert seg["params"] in grid(lookback=[63, 126], top_n=[1, 2])
        assert "sharpe" in seg["test_metrics"]
    # in a market with a persistent winner, OOS momentum should be profitable
    assert result.metrics["cagr"] > 0


def test_walk_forward_insufficient_history_raises():
    idx = pd.bdate_range("2024-01-01", periods=100)
    prices = pd.DataFrame({"A": np.linspace(100, 110, 100)}, index=idx)
    try:
        walk_forward(prices, momentum, grid(lookback=[63]), train_days=400, test_days=120)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
