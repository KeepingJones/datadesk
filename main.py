"""
DataDesk entry point.

  python main.py backtest          run the core momentum+trend backtest, save to platform store
  python main.py serve             start the ops console on http://localhost:8000
  python main.py collect-trump     refresh the Trump communications corpus
  python main.py backfill T1 T2..  backfill daily history for tickers (yfinance)
  python main.py coverage          print history-store coverage
"""

import argparse
import logging
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("error_log.txt", encoding="utf-8")
    ]
)
logger = logging.getLogger("datadesk")


def cmd_backtest() -> None:
    from datadesk.backtest.costs import CostModel
    from datadesk.backtest.engine import run_backtest
    from datadesk.db import save_backtest_run
    from datadesk.history.store import coverage, load_closes
    from datadesk.strategies.momentum import momentum
    from datadesk.strategies.trend import apply_trend_filter

    cov = coverage()
    tickers = cov[cov["rows"] > 800]["ticker"].tolist()
    if not tickers:
        print("History store is empty — run: python -m datadesk.history.migrate "
              "or python main.py backfill <tickers>")
        return

    prices = load_closes(tickers=tickers)
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.9)).ffill(limit=5)

    params = {"lookback": 126, "top_n": 10, "skip": 21, "trend_window": 200, "trend_band": 0.02}
    weights = momentum(params["lookback"], params["top_n"], params["skip"])(prices)
    if "SPY" in prices.columns:
        weights = apply_trend_filter(
            weights, prices["SPY"], params["trend_window"], params["trend_band"]
        )

    warmup = prices.index[min(params["lookback"] + params["skip"] + 5, len(prices) - 1)]
    result = run_backtest(weights, prices, CostModel(default_tier="L1"), start=str(warmup.date()))

    save_backtest_run("momentum+trend (core)", params, result.metrics, result.equity)
    print(f"Universe: {prices.shape[1]} tickers, {prices.shape[0]} days")
    print(f"Metrics:  {result.metrics}")
    print("Saved to platform store — view at http://localhost:8000 (python main.py serve)")


def cmd_serve(port: int) -> None:
    import subprocess
    import time

    import uvicorn
    
    # Force kill anything listening on this port (Windows)
    try:
        out = subprocess.check_output(f"netstat -aon | findstr :{port} | findstr LISTENING", shell=True).decode()
        for line in out.strip().split('\n'):
            if line:
                pid = line.strip().split()[-1]
                logger.info(f"Killing old server process (PID: {pid}) on port {port}...")
                subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1)
    except subprocess.CalledProcessError:
        pass # No process found
        
    from datadesk.api.app import app

    logger.info(f"DataDesk ops console: http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


def cmd_collect_trump() -> None:
    from datadesk.ingest.trump import collect

    print(f"New posts stored: {collect()}")


def cmd_backfill(tickers: list[str], source: str) -> None:
    if source == "massive":
        from datadesk.ingest.massive import backfill_massive
        written = backfill_massive(tickers)
    else:
        from datadesk.ingest.backfill import backfill_history
        written = backfill_history(tickers)

    for t, n in written.items():
        print(f"  {t:>10}  {n} bars")


def cmd_coverage() -> None:
    from datadesk.history.store import coverage

    print(coverage().to_string(index=False))


def cmd_holdout() -> None:
    from datadesk.backtest.engine import run_backtest
    from datadesk.db import save_backtest_run
    from datadesk.history.store import coverage, load_closes
    from datadesk.strategies.blend import inverse_volatility_blend
    from datadesk.strategies.insider import insider_congress_follow
    from datadesk.strategies.meanrev import mean_reversion
    from datadesk.strategies.momentum import momentum
    from datadesk.strategies.regime import compose_scales, vix_scale
    from datadesk.strategies.trend import trend_signal
    
    cov = coverage()
    tickers = cov[cov["rows"] > 800]["ticker"].tolist()
    if not tickers:
        print("History store is empty")
        return

    prices = load_closes(tickers=tickers)
    prices = prices.dropna(axis=0, thresh=int(len(prices.columns) * 0.5))
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.9)).ffill(limit=5)
    
    print("Generating strategy weights...")
    w_mom = momentum(126, 10, 21)(prices)
    w_mr = mean_reversion()(prices)
    w_insider = insider_congress_follow()(prices)
    
    print("Blending portfolios...")
    w_blend = inverse_volatility_blend([w_mom, w_mr, w_insider], prices)
    
    if "^VIX" in prices.columns and "SPY" in prices.columns:
        print("Applying Global Risk Overlays (Trend & VIX)...")
        t_scale = trend_signal(prices["SPY"], 200, 0.02)
        v_scale = vix_scale(prices["^VIX"])
        global_scale = compose_scales(t_scale, v_scale)
        w_blend = w_blend.mul(global_scale, axis=0)
    elif "SPY" in prices.columns:
        t_scale = trend_signal(prices["SPY"], 200, 0.02)
        w_blend = w_blend.mul(t_scale, axis=0)
    
    from datadesk.backtest.costs import ALPACA_COSTS, T212_ISA_COSTS
    
    print("Running backtests...")
    warmup = prices.index[min(150, len(prices)-1)]
    
    # Alpaca Run
    res_alpaca = run_backtest(w_blend, prices, ALPACA_COSTS, start=str(warmup.date()))
    
    # T212 Run
    res_t212 = run_backtest(w_blend, prices, T212_ISA_COSTS, start=str(warmup.date()))
    
    print("=== HOLDOUT REPORT (ALPACA - 0bps FX) ===")
    print(f"Full period CAGR: {res_alpaca.metrics.get('cagr', 'N/A')}")
    print(f"Full period Sharpe: {res_alpaca.metrics.get('sharpe', 'N/A')}")
    print(f"Full period MaxDD: {res_alpaca.metrics.get('max_drawdown', 'N/A')}")
    
    print("\n=== HOLDOUT REPORT (T212 - 15bps FX) ===")
    print(f"Full period CAGR: {res_t212.metrics.get('cagr', 'N/A')}")
    print(f"Full period Sharpe: {res_t212.metrics.get('sharpe', 'N/A')}")
    print(f"Full period MaxDD: {res_t212.metrics.get('max_drawdown', 'N/A')}")
    
    save_backtest_run("Blended Holdout (Alpaca Paper)", {}, res_alpaca.metrics, res_alpaca.equity)
    print("Saved to platform store.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DataDesk — market data platform (paper only)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backtest")
    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("collect-trump")
    p_bf = sub.add_parser("backfill")
    p_bf.add_argument("--source", choices=["yahoo", "massive"], default="yahoo", help="Data source to use")
    p_bf.add_argument("tickers", nargs="+")
    sub.add_parser("coverage")
    sub.add_parser("holdout")
    args = parser.parse_args()

    if args.command == "backtest":
        cmd_backtest()
    elif args.command == "serve":
        cmd_serve(args.port)
    elif args.command == "collect-trump":
        cmd_collect_trump()
    elif args.command == "backfill":
        cmd_backfill(args.tickers, args.source)
    elif args.command == "coverage":
        cmd_coverage()
    elif args.command == "holdout":
        cmd_holdout()
