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

# --- DAEMON COMMAND & CONTROL ---
class DaemonManager:
    def __init__(self):
        self.status = {
            "agent_worker": True,
            "trump_monitor": True,
            "supply_chain": True,
            "jensen_monitor": True
        }

daemon_mgr = DaemonManager()

@app.get("/api/daemons/status")
def get_daemons_status():
    return daemon_mgr.status

@app.post("/api/daemons/{daemon_name}/start")
def start_daemon(daemon_name: str):
    if daemon_name in daemon_mgr.status:
        daemon_mgr.status[daemon_name] = True
        return {"status": "started", "daemon": daemon_name}
    return {"error": "unknown daemon"}

@app.post("/api/daemons/{daemon_name}/stop")
def stop_daemon(daemon_name: str):
    if daemon_name in daemon_mgr.status:
        daemon_mgr.status[daemon_name] = False
        return {"status": "stopped", "daemon": daemon_name}
    return {"error": "unknown daemon"}



@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "paper": PAPER_TRADE_MODE,
            "coverage": _coverage_summary(),
            "runs": load_backtest_runs(limit=5),
            "trump": _trump_stats(),
            "insider": _insider_stats(),
        },
    )
