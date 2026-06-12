"""DataDesk ops console — FastAPI + Jinja2, no build chain."""

from pathlib import Path

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from datadesk.config import PAPER_TRADE_MODE
from datadesk.live.oms import CLOSED_POSITIONS, HISTORIC_TRADES
from datadesk.live.universe import get_active_universe
from datadesk.monte_carlo import simulation as mc_sim

# Global Monte Carlo config and status
MONTE_CARLO_CONFIG = {
    "default_runs": 1000,
    "models": ["bootstrap", "gbm"]
}
MONTE_CARLO_STATUS = {
    "running": False,
    "progress": 0,
    "total": 0,
    "result_path": None,
    "message": "Idle"
}
from datadesk.db import load_backtest_runs
from datadesk.history.store import coverage

app = FastAPI(title="DataDesk")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "dashboard"))


def _trump_stats() -> dict:
    try:
        from datadesk.ingest.trump import load_posts

        df = load_posts()
        if df.empty:
            return {"posts": 0, "latest": "—"}
        return {"posts": len(df), "latest": str(df["created_at"].max())[:16]}
    except Exception:
        return {"posts": 0, "latest": "—"}

def _insider_stats() -> dict:
    try:
        import os
        import sqlite3

        import pandas as pd
        alt_db = os.getenv("ALT_DATA_DB", r"C:\Users\ewanj\trading-bot\alt_data.db")
        conn = sqlite3.connect(f"file:{alt_db}?mode=ro", uri=True)
        insiders = pd.read_sql("SELECT COUNT(*) as c, MAX(filing_date) as m FROM insiders", conn)
        congress = pd.read_sql("SELECT COUNT(*) as c, MAX(disclosure_date) as m FROM congress_trading", conn)
        conn.close()
        
        return {
            "insider_rows": int(insiders.iloc[0]["c"]),
            "insider_latest": str(insiders.iloc[0]["m"])[:10],
            "congress_rows": int(congress.iloc[0]["c"]),
            "congress_latest": str(congress.iloc[0]["m"])[:10],
        }
    except Exception:
        return {"insider_rows": 0, "insider_latest": "—", "congress_rows": 0, "congress_latest": "—"}


def _coverage_summary() -> dict:
    cov = coverage()
    if cov.empty:
        return {"tickers": 0, "bars": 0, "first": "—", "last": "—", "top": []}
    return {
        "tickers": len(cov),
        "bars": int(cov["rows"].sum()),
        "first": cov["first"].min(),
        "last": cov["last"].max(),
        "top": cov.sort_values("rows", ascending=False).head(12).to_dict("records"),
    }


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "paper_trade_mode": PAPER_TRADE_MODE,
        "endpoints": ["/", "/api/health", "/api/coverage", "/api/runs"],
    }


@app.get("/api/coverage")
def api_coverage() -> dict:
    return _coverage_summary()


@app.get("/api/runs")
def api_runs() -> list[dict]:
    return load_backtest_runs()


@app.get("/api/ai_feed")
def api_ai_feed() -> list[dict]:
    """AI worker activity feed. Returns [] until the real LLM worker emits events —
    no fabricated entries (docs-honesty rule)."""
    return []

@app.get("/api/top_grid")
def api_top_grid(limit: int = 10) -> list[dict]:
    """Return top backtest runs sorted by a chosen metric (e.g., CAGR)."""
    runs = load_backtest_runs(limit=limit)
    # Ensure runs are sorted by CAGR descending (already sorted in index)
    runs.sort(key=lambda r: r["metrics"].get("cagr", 0), reverse=True)
    return runs

@app.post("/api/sweep/reload")
def api_sweep_reload() -> dict:
    """Trigger a fresh sweep run (same as /api/sweep/run) and return status."""
    try:
        return api_sweep_run()
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/daily_pnl")
def api_daily_pnl() -> dict:
    """Aggregate historic trade PnL by day.
    Returns a dict with dates as keys and total pnl for that day.
    """
    if not HISTORIC_TRADES:
        return {}
    df = pd.DataFrame(HISTORIC_TRADES)
    # Ensure timestamp is datetime
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['date'] = df['timestamp'].dt.date
    daily = df.groupby('date')['pnl'].sum().reset_index()
    # Convert to simple dict mapping date string to pnl
    return {str(row['date']): round(row['pnl'], 4) for _, row in daily.iterrows()}

@app.get("/api/historic_trades")
def api_historic_trades() -> list[dict]:
    """Return list of historic trades with PnL and timestamps."""
    return HISTORIC_TRADES

# --- DAEMON COMMAND & CONTROL (REAL THREADING) ---
import threading

from datadesk.live.monitors.agent_worker import AgentWorker
from datadesk.live.monitors.jensen_monitor import JensenMonitor
from datadesk.live.monitors.news_monitor import NewsMonitor
from datadesk.live.monitors.supply_chain import SupplyChainMonitor
from datadesk.live.monitors.trump_monitor import TrumpMonitor
from datadesk.live.oms import OMSFastPath


class DaemonManager:
    def __init__(self):
        self.oms = OMSFastPath()
        self.daemons = {
            "agent_worker": {"instance": AgentWorker(self.oms), "thread": None},
            "trump_monitor": {"instance": TrumpMonitor(self.oms), "thread": None},
            "supply_chain": {"instance": SupplyChainMonitor(self.oms), "thread": None},
            "jensen_monitor": {"instance": JensenMonitor(self.oms), "thread": None},
            "news_monitor": {"instance": NewsMonitor(), "thread": None}
        }

    @property
    def status(self):
        return {
            name: {
                "running": (info["thread"] is not None and info["thread"].is_alive()),
                "last_run": getattr(info["instance"], "last_run", "Never")
            }
            for name, info in self.daemons.items()
        }

    def start(self, name: str):
        if name in self.daemons:
            info = self.daemons[name]
            if info["thread"] is None or not info["thread"].is_alive():
                info["thread"] = threading.Thread(target=info["instance"].start, daemon=True)
                info["thread"].start()
            return True
        return False

    def stop(self, name: str):
        if name in self.daemons:
            self.daemons[name]["instance"].stop()
            self.daemons[name]["thread"] = None
            return True
        return False

daemon_mgr = DaemonManager()

# Daemons NEVER auto-start (DESIGN §6.2: shadow-first, explicit arming only).
# Start them deliberately via POST /api/daemons/{name}/start or the dashboard.

@app.get("/api/daemons/status")
def get_daemons_status():
    return daemon_mgr.status

@app.post("/api/daemons/{daemon_name}/start")
def start_daemon(daemon_name: str):
    if daemon_mgr.start(daemon_name):
        return {"status": "started", "daemon": daemon_name}
    return {"error": "unknown daemon"}

@app.post("/api/daemons/{daemon_name}/stop")
def stop_daemon(daemon_name: str):
    if daemon_mgr.stop(daemon_name):
        return {"status": "stopped", "daemon": daemon_name}
    return {"error": "unknown daemon"}

@app.get("/api/alpaca/account")
def api_alpaca_account():
    """Returns live paper trading balance and daily performance from Alpaca."""
    import os
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    
    if not api_key or not secret_key:
        return {"status": "error", "message": "ALPACA_API_KEY or ALPACA_SECRET_KEY not set in .env"}
        
    try:
        from alpaca.trading.client import TradingClient
        alpaca = TradingClient(api_key, secret_key, paper=True)
        account = alpaca.get_account()
        
        equity = float(account.equity)
        last_equity = float(account.last_equity)
        buying_power = float(account.buying_power)
        
        # Calculate daily PnL
        pnl_dollar = equity - last_equity
        pnl_pct = (pnl_dollar / last_equity) * 100 if last_equity > 0 else 0.0
        
        return {
            "status": "ok",
            "equity": round(equity, 2),
            "buying_power": round(buying_power, 2),
            "pnl_dollar": round(pnl_dollar, 2),
            "pnl_pct": round(pnl_pct, 2)
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/alpaca/positions")
def api_alpaca_positions():
    """Returns live active paper positions from Alpaca."""
    import os
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    
    if not api_key or not secret_key:
        return {"status": "error", "message": "Keys missing"}
        
    try:
        from alpaca.trading.client import TradingClient
        alpaca = TradingClient(api_key, secret_key, paper=True)
        positions = alpaca.get_all_positions()
        
        result = []
        for p in positions:
            sym = p.symbol
            side = str(p.side).split(".")[-1]
            status_text = "HOLD"
            pos_state = daemon_mgr.oms.active_positions.get(sym)
            if pos_state:
                cp = float(p.current_price) if p.current_price else 0.0
                fv = pos_state.get("fundamental_fair_value")
                sp = pos_state.get("stop_price")
                if fv is None:
                    status_text = "EVALUATING..."
                else:
                    if side == "BUY":
                        if cp <= sp:
                            status_text = "CLOSE (Stop Loss)"
                        elif cp >= fv:
                            status_text = "CLOSE (Take Profit)"
                        elif fv > cp * 1.15:
                            status_text = "BUY MORE"
                    elif side == "SELL":
                        if cp >= sp:
                            status_text = "CLOSE (Stop Loss)"
                        elif cp <= fv:
                            status_text = "CLOSE (Take Profit)"
            else:
                # Use recorded closed status if available, otherwise pending
                status_text = CLOSED_POSITIONS.get(sym, "PENDING")
            
            result.append({
                "symbol": sym, "qty": p.qty, "market_value": p.market_value,
                "unrealized_pl": p.unrealized_pl, "unrealized_plpc": p.unrealized_plpc,
                "side": side, "ai_status": status_text
            })
        return {"status": "ok", "positions": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


from pydantic import BaseModel

from datadesk.ingest.validation import validate_universe
from datadesk.live.universe import add_ticker


class TickerRequest(BaseModel):
    ticker: str

@app.get("/api/universe/list")
def api_universe_list():
    return {"universe": get_active_universe()}

@app.post("/api/universe/add")
def api_universe_add(req: TickerRequest):
    return add_ticker(req.ticker)

@app.get("/api/validation")
def api_validation():
    from datadesk.ingest.backfill import backfill_smart
    report = validate_universe()
    bad_tickers = [ticker for ticker, r in report.items() if r["status"] in ["FAIL", "WARN"]]
    if bad_tickers:
        backfill_smart(bad_tickers)
        report = validate_universe()
    return report

@app.post("/api/sweep/run")
def api_sweep_run():
    import os
    import subprocess
    # Launch sweep.py in the background
    subprocess.Popen(
        [".venv\\Scripts\\python", "sweep.py"], 
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    return {"status": "started"}


@app.post("/api/monte_carlo/run")
def api_monte_carlo_run(runs: int = 1000, model: str = "bootstrap", background_tasks: BackgroundTasks = None):
    """Start Monte Carlo simulation in background."""
    if model not in MONTE_CARLO_CONFIG["models"]:
        return {"status": "error", "message": f"Model {model} not supported"}
    MONTE_CARLO_STATUS.update({"running": True, "progress": 0, "total": runs,
                               "result_path": None, "message": "Running"})
    def run_sim():
        try:
            result_path = mc_sim.run_simulation(runs, model, status_callback=lambda p: MONTE_CARLO_STATUS.update({"progress": p}))
            MONTE_CARLO_STATUS.update({"running": False, "progress": runs, "result_path": result_path, "message": "Completed"})
        except Exception as e:
            MONTE_CARLO_STATUS.update({"running": False, "message": f"Error: {e}"})
    if background_tasks:
        background_tasks.add_task(run_sim)
    else:
        run_sim()
    return {"status": "started", "runs": runs, "model": model}


@app.get("/api/monte_carlo/status")
def api_monte_carlo_status():
    """Return Monte Carlo simulation status."""
    return MONTE_CARLO_STATUS

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    runs = load_backtest_runs(limit=50)
    # Sort runs by highest CAGR
    runs.sort(key=lambda r: r["metrics"].get("cagr", 0), reverse=True)
    top_runs = runs[:5] if runs else []
    
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "paper": PAPER_TRADE_MODE,
            "coverage": _coverage_summary(),
            "runs": top_runs,
            "trump": _trump_stats(),
            "insider": _insider_stats(),
        },
    )
