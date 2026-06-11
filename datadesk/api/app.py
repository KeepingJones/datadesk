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
