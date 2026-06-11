"""DataDesk ops console — FastAPI + Jinja2, no build chain."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from datadesk.config import PAPER_TRADE_MODE
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
        import sqlite3
        import pandas as pd
        conn = sqlite3.connect(r"C:\Users\ewanj\trading-bot\alt_data.db")
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


import random
import time
from datetime import datetime, timedelta

@app.get("/api/ai_feed")
def api_ai_feed() -> list[dict]:
    """Simulates the background Ollama Phi-3.5 agent digesting data and generating fundamental valuations."""
    now = datetime.now()
    feed = [
        {"timestamp": (now - timedelta(seconds=12)).strftime("%H:%M:%S"), "ticker": "TSM", "message": "Situational Awareness framework activated. Computing fabrication bottlenecks. Raising Fair Value target to 1.15x."},
        {"timestamp": (now - timedelta(seconds=45)).strftime("%H:%M:%S"), "ticker": "NVDA", "message": "Analyzing SEC 10-Q filing. Margins stable. Fundamental Fair Value target maintained."},
        {"timestamp": (now - timedelta(seconds=82)).strftime("%H:%M:%S"), "ticker": "VST", "message": "Energy constraint override triggered. DCF re-rating applied. Fair Value target raised to 1.40x."},
        {"timestamp": (now - timedelta(minutes=2)).strftime("%H:%M:%S"), "ticker": "AAPL", "message": "Processing Trump Truth Social post sentiment. Sentiment: NEGATIVE. Fast-Path signaled."},
        {"timestamp": (now - timedelta(minutes=5)).strftime("%H:%M:%S"), "ticker": "CEG", "message": "Nuclear energy cap-ex identified. Updating fundamental target."},
    ]
    # Randomly shuffle or pick to simulate live feed updates
    return feed

@app.get("/api/live_trades")
def api_live_trades() -> list[dict]:
    """Simulates live trades executing on the OMS Fast-Path."""
    now = datetime.now()
    trades = [
        {"timestamp": (now - timedelta(seconds=2)).strftime("%H:%M:%S"), "broker": "Alpaca", "side": "BUY", "ticker": "SMCI", "alloc": "10.0%", "reason": "Jensen Huang Keynote (Partner Shoutout)"},
        {"timestamp": (now - timedelta(seconds=14)).strftime("%H:%M:%S"), "broker": "T212", "side": "BUY", "ticker": "TSM", "alloc": "5.0%", "reason": "Supply-Chain Anomaly (NVDA lead)"},
        {"timestamp": (now - timedelta(seconds=85)).strftime("%H:%M:%S"), "broker": "Alpaca", "side": "SELL", "ticker": "AAPL", "alloc": "8.0%", "reason": "Trailing Stop-Loss Triggered"},
        {"timestamp": (now - timedelta(minutes=2)).strftime("%H:%M:%S"), "broker": "Alpaca", "side": "BUY", "ticker": "AAPL", "alloc": "8.0%", "reason": "Trump Sentiment (Negative - Short)"},
    ]
    return trades

# --- DAEMON COMMAND & CONTROL (REAL THREADING) ---
import threading
from datadesk.live.monitors.agent_worker import AgentWorker
from datadesk.live.monitors.trump_monitor import TrumpMonitor
from datadesk.live.monitors.supply_chain import SupplyChainMonitor
from datadesk.live.monitors.jensen_monitor import JensenMonitor
from datadesk.live.monitors.news_monitor import NewsMonitor
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

# Auto-start all daemons on boot
for d_name in daemon_mgr.daemons.keys():
    daemon_mgr.start(d_name)

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
            result.append({
                "symbol": p.symbol,
                "qty": p.qty,
                "market_value": p.market_value,
                "unrealized_pl": p.unrealized_pl,
                "unrealized_plpc": p.unrealized_plpc,
            })
        return {"status": "ok", "positions": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}



from pydantic import BaseModel
from datadesk.live.universe import get_active_universe, add_ticker
from datadesk.ingest.validation import validate_universe

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
    import subprocess
    import os
    # Launch sweep.py in the background
    subprocess.Popen(
        [".venv\\Scripts\\python", "sweep.py"], 
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    return {"status": "started"}

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
