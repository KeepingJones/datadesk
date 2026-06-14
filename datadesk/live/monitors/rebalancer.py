"""
Daily Rebalancer — closes the loop between backtesting and live trading.

Every trading day at 15:45 ET (15 minutes before close), this daemon:
  1. Loads the best HOLDOUT strategy from platform.db (by Sharpe)
  2. Rebuilds its weight function from stored params
  3. Fetches today's price history and computes today's target weights
  4. Diffs target vs current OMS positions
  5. Submits BUY/SELL signals to bring the book to target
  6. Records everything to the shadow store (no broker execution unless armed)

DRIFT_THRESHOLD: only rebalance a position if its current weight differs from
target by more than this — avoids churn on trivial drift.
"""

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datadesk.live.oms import OMSFastPath

logger = logging.getLogger(__name__)

DRIFT_THRESHOLD = 0.02    # 2% drift before rebalancing
POLL_INTERVAL = 60        # seconds between checks
REBAL_HOUR_ET = 15        # 3pm ET target — fire once when hour reaches this
REBAL_MINUTE_ET = 45      # 3:45pm ET


def _et_now() -> datetime:
    """Current time in US/Eastern (UTC-5 winter, UTC-4 summer — good enough approximation)."""
    import time as _time
    utc = datetime.now(timezone.utc)
    # Approximate ET offset: UTC-5 Nov-Mar, UTC-4 Mar-Nov
    doy = utc.timetuple().tm_yday
    offset_hours = -4 if (90 < doy < 307) else -5  # rough DST boundary
    from datetime import timedelta
    return utc + timedelta(hours=offset_hours)


def _is_weekday() -> bool:
    return _et_now().weekday() < 5


def _build_strategy(params: dict, prices):
    """Reconstruct the weight DataFrame from stored params."""
    from datadesk.strategies.momentum import momentum
    from datadesk.strategies.meanrev import mean_reversion
    from datadesk.strategies.blend import inverse_volatility_blend
    from datadesk.strategies.trend import trend_signal
    import pandas as pd

    variant = params.get("variant", "mom+mr")
    lb = params.get("mom_lookback", 126)
    top = params.get("mom_top_n", 2)
    z = params.get("mr_z_entry", 1.0)
    trend = params.get("trend_filter", True)
    blend_type = params.get("blend", "inv_vol")

    if variant == "mom_only":
        w = momentum(lb, top, 21)(prices)
    elif variant == "mr_only":
        w = mean_reversion(z_entry=z, z_exit=0.0)(prices)
    elif variant == "trend_only_EW":
        n = prices.shape[1] - (1 if "SPY" in prices.columns else 0)
        cols = [c for c in prices.columns if c != "SPY"]
        w = pd.DataFrame(1.0 / n, index=prices.index, columns=cols)
    else:  # mom+mr (default)
        w_mom = momentum(lb, top, 21)(prices)
        w_mr = mean_reversion(z_entry=z, z_exit=0.0)(prices)
        if blend_type == "inv_vol":
            w = inverse_volatility_blend([w_mom, w_mr], prices)
        else:
            w = (w_mom + w_mr) / 2.0

    if trend and "SPY" in prices.columns:
        scale = trend_signal(prices["SPY"], 200, 0.02)
        w = w.mul(scale, axis=0)

    return w


def _get_best_run() -> dict | None:
    """Load the highest-Sharpe HOLDOUT run from platform.db."""
    import json
    from datadesk.db import load_backtest_runs
    runs = load_backtest_runs(limit=200)
    holdout_runs = [r for r in runs if "HOLDOUT" in r["name"]]
    if not holdout_runs:
        holdout_runs = runs  # fall back to any run
    if not holdout_runs:
        return None
    return max(holdout_runs, key=lambda r: r["metrics"].get("sharpe", 0))


def _universe_tickers(params: dict) -> list[str]:
    """Return universe tickers from stored params."""
    from sweep import UNIVERSES
    name = params.get("universe", "AI_SEMI")
    return UNIVERSES.get(name, [])


class DailyRebalancer:
    def __init__(self, oms: "OMSFastPath"):
        self.oms = oms
        self.is_running = False
        self.last_run = "Never"
        self._last_rebal_date: str | None = None

    def start(self):
        self.is_running = True
        logger.info("[REBALANCER] started — will rebalance at ~15:45 ET each trading day")
        while self.is_running:
            try:
                self._tick()
            except Exception as e:
                logger.error(f"[REBALANCER] tick error: {e}")
            time.sleep(POLL_INTERVAL)

    def stop(self):
        self.is_running = False

    def _tick(self):
        if not _is_weekday():
            return
        now = _et_now()
        today_str = now.strftime("%Y-%m-%d")
        if (now.hour > REBAL_HOUR_ET or (now.hour == REBAL_HOUR_ET and now.minute >= REBAL_MINUTE_ET)):
            if self._last_rebal_date != today_str:
                logger.info(f"[REBALANCER] triggering daily rebalance for {today_str}")
                self.rebalance()
                self._last_rebal_date = today_str
                self.last_run = now.strftime("%H:%M:%S")

    def rebalance(self) -> dict:
        """
        Compute target weights from best strategy and submit drift signals.
        Returns summary dict for logging/dashboard.
        """
        from datadesk.history.store import load_closes

        best = _get_best_run()
        if best is None:
            logger.warning("[REBALANCER] no backtest runs found — run the sweep first")
            return {"status": "no_runs"}

        params = best["params"]
        strategy_name = best["name"]
        logger.info(f"[REBALANCER] using strategy: {strategy_name} (Sharpe {best['metrics'].get('sharpe', 0):.2f})")

        tickers = _universe_tickers(params)
        if not tickers:
            logger.warning("[REBALANCER] could not resolve universe tickers")
            return {"status": "no_tickers"}

        prices = load_closes(tickers=tickers)
        if prices is None or prices.empty or len(prices) < 50:
            logger.warning("[REBALANCER] insufficient price history for strategy reconstruction")
            return {"status": "no_prices"}

        prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.80)).ffill(limit=5)

        try:
            weights = _build_strategy(params, prices)
        except Exception as e:
            logger.error(f"[REBALANCER] strategy build failed: {e}")
            return {"status": "strategy_error", "error": str(e)}

        if weights.empty:
            return {"status": "empty_weights"}

        # Today's target = last row of the weight matrix
        target_row = weights.iloc[-1]
        targets: dict[str, float] = {
            t: float(w) for t, w in target_row.items() if float(w) > 1e-6
        }

        logger.info(f"[REBALANCER] target weights: {targets}")

        buys, sells, holds, skipped = [], [], [], []

        # Get current prices for the last row of price history
        current_prices = prices.iloc[-1].to_dict()

        # 1. Open / increase positions
        for ticker, target_w in targets.items():
            current_pos = self.oms.active_positions.get(ticker)
            current_w = current_pos["alloc"] if current_pos else 0.0
            drift = abs(target_w - current_w)

            if drift < DRIFT_THRESHOLD:
                holds.append(ticker)
                continue

            ref_price = current_prices.get(ticker)
            self.oms.submit_signal(
                ticker,
                "BUY",
                weight_pct=target_w,
                price=ref_price,
                reason=f"daily rebalance → {target_w:.1%} (drift {drift:.1%})",
                source="rebalancer",
            )
            buys.append(ticker)

        # 2. Close positions no longer in target
        with self.oms._lock:
            held_tickers = list(self.oms.active_positions.keys())

        for ticker in held_tickers:
            if ticker not in targets:
                ref_price = current_prices.get(ticker)
                self.oms.submit_signal(
                    ticker,
                    "SELL",
                    weight_pct=0.0,
                    price=ref_price,
                    reason="daily rebalance → not in target",
                    source="rebalancer",
                )
                sells.append(ticker)

        summary = {
            "status": "ok",
            "strategy": strategy_name,
            "targets": targets,
            "buys": buys,
            "sells": sells,
            "holds": holds,
        }
        logger.info(
            f"[REBALANCER] done — {len(buys)} buys, {len(sells)} sells, {len(holds)} holds"
        )
        return summary
