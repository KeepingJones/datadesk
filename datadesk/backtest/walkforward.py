"""
Walk-forward validation.

Roll a (train, test) window across history. For each segment, pick the best
parameter set on the train slice only, then run it untouched on the test slice.
Stitched test returns are the only number that matters; param stability across
segments is the overfit flag.
"""

import itertools
import logging
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from datadesk.backtest.costs import CostModel
from datadesk.backtest.engine import run_backtest
from datadesk.backtest.metrics import sharpe, summarize

logger = logging.getLogger(__name__)

# A strategy factory takes params and returns a target-weights function
StrategyFactory = Callable[..., Callable[[pd.DataFrame], pd.DataFrame]]


@dataclass
class WalkForwardResult:
    returns: pd.Series  # stitched out-of-sample returns
    segments: list[dict]  # per-segment: train/test ranges, chosen params, test metrics
    metrics: dict
    param_stability: float  # share of segments choosing the modal param set (1.0 = stable)


def grid(**params: list) -> list[dict]:
    """grid(lookback=[63,126], top_n=[5,10]) → list of param dicts."""
    keys = list(params)
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*params.values())]


def walk_forward(
    prices: pd.DataFrame,
    strategy_factory: StrategyFactory,
    param_grid: list[dict],
    train_days: int = 504,
    test_days: int = 126,
    cost_model: CostModel | None = None,
    warmup_days: int = 280,
) -> WalkForwardResult:
    """
    warmup_days: extra history handed to the strategy before each slice so
    lookback windows are warm at the slice start (no NaN cold-start).
    """
    idx = prices.index
    segments = []
    oos_returns = []

    seg_start = warmup_days + train_days
    while seg_start + 1 < len(idx):
        train_lo = seg_start - train_days
        test_hi = min(seg_start + test_days, len(idx))

        train_prices = prices.iloc[max(0, train_lo - warmup_days) : seg_start]
        test_prices = prices.iloc[max(0, seg_start - warmup_days) : test_hi]
        train_eval_start = idx[train_lo]
        test_eval_start = idx[seg_start]

        # pick params on train only
        best_params, best_sharpe = None, -float("inf")
        for params in param_grid:
            weights = strategy_factory(**params)(train_prices)
            result = run_backtest(weights, train_prices, cost_model, start=str(train_eval_start))
            s = sharpe(result.returns)
            if s > best_sharpe:
                best_params, best_sharpe = params, s

        # run them untouched on test
        weights = strategy_factory(**best_params)(test_prices)
        test_result = run_backtest(weights, test_prices, cost_model, start=str(test_eval_start))
        oos_returns.append(test_result.returns)

        segments.append(
            {
                "train": (str(idx[train_lo].date()), str(idx[seg_start - 1].date())),
                "test": (str(idx[seg_start].date()), str(idx[test_hi - 1].date())),
                "params": best_params,
                "train_sharpe": round(best_sharpe, 3),
                "test_metrics": test_result.metrics,
            }
        )
        seg_start += test_days

    if not segments:
        raise ValueError("Not enough history for a single walk-forward segment")

    stitched = pd.concat(oos_returns)
    param_choices = [str(s["params"]) for s in segments]
    modal_share = max(param_choices.count(p) for p in set(param_choices)) / len(param_choices)

    return WalkForwardResult(
        returns=stitched,
        segments=segments,
        metrics=summarize(stitched),
        param_stability=round(modal_share, 2),
    )
