import logging
from datadesk.backtest.costs import ALPACA_COSTS
from datadesk.backtest.engine import run_backtest
from datadesk.db import save_backtest_run
from datadesk.history.store import load_closes
from datadesk.strategies.momentum import momentum
from datadesk.strategies.meanrev import mean_reversion
from datadesk.strategies.insider import insider_congress_follow
from datadesk.strategies.blend import inverse_volatility_blend

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sweep")

# Target universe from Leopold's 13F + core
from datadesk.strategies.trend import trend_signal

TICKERS = [
    'BE', 'CLSK', 'IREN', 'CORZ', 'BTDR', 'APLD', 'WDC', 
    'SMH', 'NVDA', 'ORCL', 'AMD', 'ASML', 'MU', 'AVGO', 'TSM',
    'AAPL', 'MSFT', 'DELL', 'SMCI', 'SPY'
]

def run_sweep():
    prices = load_closes(tickers=TICKERS)
    prices = prices.dropna(axis=0, thresh=int(len(prices.columns) * 0.5))
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.9)).ffill(limit=5)
    
    if prices.empty:
        logger.error("Prices dataframe is empty. Wait for backfill.")
        return
        
    warmup = prices.index[min(150, len(prices)-1)]
    w_insider = insider_congress_follow()(prices)
    
    lookbacks = [126, 252]
    top_ns = [2, 3]
    mr_zs = [1.0, 1.5]
    
    count = 0
    total = len(lookbacks) * len(top_ns) * len(mr_zs)
    
    for lb in lookbacks:
        for top in top_ns:
            for z in mr_zs:
                count += 1
                logger.info(f"Run {count}/{total} - mom({lb}, {top}) mr({z})")
                
                w_mom = momentum(lb, top, 21)(prices)
                w_mr = mean_reversion(z_entry=z, z_exit=0.0)(prices)
                
                # Exclude insider trading signal to concentrate on core theme
                w_blend = inverse_volatility_blend([w_mom, w_mr], prices)
                
                if "SPY" in prices.columns:
                    t_scale = trend_signal(prices["SPY"], 200, 0.02)
                    w_blend = w_blend.mul(t_scale, axis=0)
                
                params = {
                    "mom_lookback": lb,
                    "mom_top_n": top,
                    "mr_z_entry": z,
                    "trend_filter": True
                }
                
                name = f"Grid: mom({lb},{top}) mr({z}) +Trend (No Insider)"
                res = run_backtest(w_blend, prices, ALPACA_COSTS, start=str(warmup.date()))
                
                cagr = res.metrics.get("cagr", 0.0)
                logger.info(f"   => CAGR: {cagr*100:.1f}%")
                
                save_backtest_run(name, params, res.metrics, res.equity)

if __name__ == "__main__":
    run_sweep()
