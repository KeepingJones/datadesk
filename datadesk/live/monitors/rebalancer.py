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
from typing import TYPE_CHECKING

from datadesk.live.market_calendar import (
    exchange_is_open,
    is_moc_window,
    is_trading_day,
    ticker_exchange,
)

if TYPE_CHECKING:
    from datadesk.live.oms import OMSFastPath

logger = logging.getLogger(__name__)

DRIFT_THRESHOLD = 0.02    # 2% drift before rebalancing
POLL_INTERVAL = 60        # seconds between checks


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
    """
    Load the best eligible HOLDOUT run from platform.db.

    Eligibility: Sharpe >= 1.0, MaxDD >= -30%, top_n >= 2.
    Prefer 3y holdout over 1y; fall back to any holdout, then any run.
    """
    from datadesk.db import load_backtest_runs
    runs = load_backtest_runs(limit=500)

    def _eligible(r: dict) -> bool:
        m = r["metrics"]
        p = r["params"]
        return (
            m.get("sharpe", 0) >= 1.0
            and m.get("max_drawdown", -1) >= -0.30
            and p.get("mom_top_n", 1) >= 2
        )

    holdout_3y = [r for r in runs if "HOLDOUT 3y" in r["name"] and _eligible(r)]
    if holdout_3y:
        return max(holdout_3y, key=lambda r: r["metrics"].get("sharpe", 0))

    holdout_1y = [r for r in runs if "HOLDOUT 1y" in r["name"] and _eligible(r)]
    if holdout_1y:
        return max(holdout_1y, key=lambda r: r["metrics"].get("sharpe", 0))

    # Fallback: any holdout passing filters, then any run passing filters
    holdouts = [r for r in runs if "HOLDOUT" in r["name"] and _eligible(r)]
    if holdouts:
        return max(holdouts, key=lambda r: r["metrics"].get("sharpe", 0))

    eligible_any = [r for r in runs if _eligible(r)]
    if eligible_any:
        return max(eligible_any, key=lambda r: r["metrics"].get("sharpe", 0))

    logger.warning("[REBALANCER] no eligible run found (all fail top_n/Sharpe/MaxDD filters)")
    return None


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
        """Fire rebalance when NYSE enters its MOC window on a trading day."""
        import datetime as _dt
        today = _dt.date.today()
        today_str = str(today)

        if not is_trading_day("NYSE", today):
            return

        if is_moc_window("NYSE") and self._last_rebal_date != today_str:
            logger.info(f"[REBALANCER] NYSE MOC window open — triggering rebalance for {today_str}")
            self.rebalance()
            self._last_rebal_date = today_str
            self.last_run = _dt.datetime.now().strftime("%H:%M:%S")

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

        buys, sells, holds, queued = [], [], [], []

        # Get current prices for the last row of price history
        current_prices = prices.iloc[-1].to_dict()

        # 1. Open / increase positions — only for exchanges currently tradeable
        for ticker, target_w in targets.items():
            exchange = ticker_exchange(ticker)
            if not exchange_is_open(exchange):
                queued.append(ticker)
                logger.info(
                    f"[REBALANCER] {ticker} queued — {exchange} closed, "
                    f"will execute at next open"
                )
                continue

            current_pos = self.oms.active_positions.get(ticker)
            current_w = current_pos["alloc"] if current_pos else 0.0
            drift = abs(target_w - current_w)

            if drift < DRIFT_THRESHOLD:
                holds.append(ticker)
                continue

            in_moc = is_moc_window(exchange)
            timing = "MOC" if in_moc else "market"
            ref_price = current_prices.get(ticker)
            self.oms.submit_signal(
                ticker,
                "BUY",
                weight_pct=target_w,
                price=ref_price,
                reason=f"rebalance {timing} → {target_w:.1%} (drift {drift:.1%})",
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
            "queued_closed_exchange": queued,
        }
        logger.info(
            f"[REBALANCER] done — {len(buys)} buys, {len(sells)} sells, "
            f"{len(holds)} holds, {len(queued)} queued (exchange closed)"
        )
        return summary
