"""DataDesk ops console — FastAPI + Jinja2, no build chain."""

from pathlib import Path
import threading as _threading
import datetime as _dt
import time as _time

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import logging

from contextlib import asynccontextmanager

from datadesk.config import setup_logging
logger = setup_logging("", "live.log")

from datadesk.config import PAPER_TRADE_MODE
from datadesk.live.oms import CLOSED_POSITIONS, HISTORIC_TRADES
from datadesk.live.universe import get_active_universe
from datadesk.monte_carlo import simulation as mc_sim

# Global Monte Carlo config and status
MONTE_CARLO_CONFIG = {"default_runs": 1000, "models": ["bootstrap", "gbm"]}
MONTE_CARLO_STATUS = {
    "running": False,
    "progress": 0,
    "total": 0,
    "result_path": None,
    "message": "Idle",
}
from datadesk.db import load_backtest_runs
from datadesk.history.store import coverage

app = FastAPI(title="DataDesk")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "dashboard"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent.parent / "dashboard")), name="static")

_last_weekly_run: _dt.date | None = None
_sweep_lock = _threading.Lock()
_sweep_running = False

# Runtime-mutable broker mode flags (survive server lifetime, reset on restart)
_ALPACA_PAPER: bool = True   # True = paper, False = live
_T212_MODE: str = "demo"     # "demo" or "live"


def _weekly_scheduler_loop() -> None:
    """Background thread: runs weekly-update every Sunday between 07:00–07:59 UTC."""
    global _last_weekly_run
    while True:
        _time.sleep(3600)  # check every hour
        now = _dt.datetime.utcnow()
        if now.weekday() == 6 and now.hour == 7 and _last_weekly_run != now.date():
            logger.info("scheduler: running weekly update")
            try:
                from datadesk.config import ALTDATA_DB
                from datadesk.history.store import coverage
                from datadesk.ingest.backfill import backfill_smart
                from datadesk.ingest.fundamentals import fetch_fundamentals
                import sqlite3

                cov = coverage()
                tradeable = [t for t in cov["ticker"].tolist() if not t.startswith("^")]
                backfill_smart(tradeable)
                stale_cut = (now - _dt.timedelta(days=7)).isoformat(timespec="seconds")
                con = sqlite3.connect(ALTDATA_DB)
                fresh = {r[0] for r in con.execute(
                    "SELECT ticker FROM equity_ratios WHERE fetched_at > ? GROUP BY ticker", (stale_cut,)
                )}
                con.close()
                stale = [t for t in tradeable if t not in fresh]
                fetch_fundamentals(stale, verbose=False)
                _last_weekly_run = now.date()
                logger.info("scheduler: weekly update complete")
            except Exception as e:
                logger.exception(f"scheduler: weekly update failed: {e}")


@app.on_event("startup")
def _start_scheduler() -> None:
    t = _threading.Thread(target=_weekly_scheduler_loop, daemon=True, name="weekly-scheduler")
    t.start()
    logger.info("weekly scheduler started (fires Sundays 07:00 UTC)")
    # Auto-start all daemons except Jensen (parked — no live transcript feed)
    for name in ("agent_worker", "trump_monitor", "supply_chain", "news_monitor"):
        daemon_mgr.start(name)
        logger.info(f"auto-started daemon: {name}")


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

        from datadesk.config import ALTDATA_DB
        conn = sqlite3.connect(f"file:{ALTDATA_DB}?mode=ro", uri=True)
        insiders = pd.read_sql("SELECT COUNT(*) as c, MAX(filing_date) as m FROM insiders", conn)
        congress = pd.read_sql(
            "SELECT COUNT(*) as c, MAX(disclosure_date) as m FROM congress_trading", conn
        )
        conn.close()

        return {
            "insider_rows": int(insiders.iloc[0]["c"]),
            "insider_latest": str(insiders.iloc[0]["m"])[:10],
            "congress_rows": int(congress.iloc[0]["c"]),
            "congress_latest": str(congress.iloc[0]["m"])[:10],
        }
    except Exception:
        return {
            "insider_rows": 0,
            "insider_latest": "—",
            "congress_rows": 0,
            "congress_latest": "—",
        }


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


@app.get("/api/reports")
def api_reports(analyst: str | None = None, limit: int = 20) -> list[dict]:
    from datadesk.db import load_reports
    return load_reports(analyst=analyst, limit=limit)


@app.get("/api/pnl_summary")
def api_pnl_summary() -> dict:
    """Derive daily/weekly/monthly PnL from the most recent backtest equity curve."""
    runs = load_backtest_runs(limit=50)
    if not runs:
        return {"daily": [], "weekly": [], "monthly": []}

    # Prefer the blended/holdout run; fall back to highest-CAGR run
    run = next((r for r in runs if "blended" in r["name"].lower()), None) or \
          max(runs, key=lambda r: r["metrics"].get("cagr", 0))

    equity = run["equity"]  # [[date_str, value], ...]
    if len(equity) < 2:
        return {"daily": [], "weekly": [], "monthly": []}

    df = pd.DataFrame(equity, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    def _pct(new, old):
        if old == 0:
            return 0.0
        return round((new - old) / old * 100, 2)

    # Daily: last 20 trading days
    daily = []
    vals = df["value"].tolist()
    dates = df.index.tolist()
    for i in range(max(1, len(vals) - 20), len(vals)):
        daily.append({
            "period": dates[i].strftime("%b %d"),
            "pct": _pct(vals[i], vals[i - 1]),
            "value": round(vals[i], 4),
        })
    daily.reverse()

    # Weekly: resample to week-end, last 12 weeks
    weekly_df = df["value"].resample("W").last().dropna()
    weekly = []
    wvals = weekly_df.tolist()
    wdates = weekly_df.index.tolist()
    for i in range(max(1, len(wvals) - 12), len(wvals)):
        weekly.append({
            "period": f"W/E {wdates[i].strftime('%b %d')}",
            "pct": _pct(wvals[i], wvals[i - 1]),
            "value": round(wvals[i], 4),
        })
    weekly.reverse()

    # Monthly: resample to month-end, last 12 months
    monthly_df = df["value"].resample("ME").last().dropna()
    monthly = []
    mvals = monthly_df.tolist()
    mdates = monthly_df.index.tolist()
    for i in range(max(1, len(mvals) - 12), len(mvals)):
        monthly.append({
            "period": mdates[i].strftime("%b %Y"),
            "pct": _pct(mvals[i], mvals[i - 1]),
            "value": round(mvals[i], 4),
        })
    monthly.reverse()

    return {
        "run_name": run["name"],
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
    }


@app.get("/api/ai_feed")
def api_ai_feed() -> list[dict]:
    """Real signal feed: recent entries from the shadow store (every monitor signal
    lands there, armed or not). No fabricated entries (docs-honesty rule)."""
    try:
        from datadesk.live.shadow import load_signals

        df = load_signals(limit=25)
        if df.empty:
            return []
        return [
            {
                "timestamp": str(r["ts"])[11:19],
                "ticker": r["ticker"],
                "message": f"[{r['source']}] {r['side']} {r['weight']:.1%} — "
                f"{r['reason'] or 'no reason given'}"
                + (" (EXECUTED)" if r["executed"] else " (shadow)"),
            }
            for _, r in df.iterrows()
        ]
    except Exception:
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
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"] = df["timestamp"].dt.date
    daily = df.groupby("date")["pnl"].sum().reset_index()
    # Convert to simple dict mapping date string to pnl
    return {str(row["date"]): round(row["pnl"], 4) for _, row in daily.iterrows()}


@app.get("/api/historic_trades")
def api_historic_trades() -> list[dict]:
    """Return list of historic trades with PnL and timestamps."""
    return HISTORIC_TRADES


@app.get("/api/live_trades")
def api_live_trades() -> list[dict]:
    """Return list of live OMS fast-path trades."""
    from datadesk.live import shadow
    try:
        df = shadow.load_signals(limit=20)
        if df.empty:
            return []
        # Return trades in the format expected by the frontend
        return [
            {
                "timestamp": str(r["ts"])[11:19],
                "broker": "Trading212" if r["ticker"].endswith(".L") else "Alpaca",
                "side": r["side"],
                "ticker": r["ticker"],
                "reason": r["reason"] or "signal"
            }
            for _, r in df.iterrows()
            if r["executed"] # Only show executed trades in live fast-path
        ]
    except Exception:
        return []


# --- DAEMON COMMAND & CONTROL (REAL THREADING) ---
import threading

from datadesk.live.monitors.agent_worker import AgentWorker
from datadesk.live.monitors.jensen_monitor import JensenMonitor
from datadesk.live.monitors.news_monitor import NewsMonitor
from datadesk.live.monitors.rebalancer import DailyRebalancer
from datadesk.live.monitors.price_feed import PriceFeed
from datadesk.live.monitors.research_analyst import ResearchAnalyst
from datadesk.live.monitors.risk_analyst import RiskAnalyst
from datadesk.live.monitors.strategy_analyst import StrategyAnalyst
from datadesk.live.monitors.supply_chain import SupplyChainMonitor
from datadesk.live.monitors.trump_monitor import TrumpMonitor
from datadesk.live.oms import OMSFastPath


class DaemonManager:
    def __init__(self):
        self.oms = OMSFastPath()
        self.daemons = {
            # ── Intraday / event-driven ──────────────────────────────────────
            "agent_worker":      {"instance": AgentWorker(self.oms),        "thread": None},
            "trump_monitor":     {"instance": TrumpMonitor(self.oms),       "thread": None},
            "supply_chain":      {"instance": SupplyChainMonitor(self.oms), "thread": None},
            "jensen_monitor":    {"instance": JensenMonitor(self.oms),      "thread": None},
            "news_monitor":      {"instance": NewsMonitor(),                 "thread": None},
            "rebalancer":        {"instance": DailyRebalancer(self.oms),    "thread": None},
            "price_feed":        {"instance": PriceFeed(self.oms),          "thread": None},
            # ── Out-of-session analysts ──────────────────────────────────────
            "research_analyst":  {"instance": ResearchAnalyst(),            "thread": None},
            "strategy_analyst":  {"instance": StrategyAnalyst(),            "thread": None},
            "risk_analyst":      {"instance": RiskAnalyst(self.oms),        "thread": None},
        }

    @property
    def status(self):
        return {
            name: {
                "running": (info["thread"] is not None and info["thread"].is_alive()),
                "last_run": getattr(info["instance"], "last_run", "Never"),
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
    logger.info(f"Start daemon request: {daemon_name}")
    if daemon_mgr.start(daemon_name):
        logger.info(f"Daemon {daemon_name} started")
        return {"status": "started", "daemon": daemon_name}
    logger.warning(f"Attempted to start unknown daemon: {daemon_name}")
    return {"error": "unknown daemon"}


@app.post("/api/daemons/{daemon_name}/stop")
def stop_daemon(daemon_name: str):
    logger.info(f"Stop daemon request: {daemon_name}")
    if daemon_mgr.stop(daemon_name):
        logger.info(f"Daemon {daemon_name} stopped")
        return {"status": "stopped", "daemon": daemon_name}
    logger.warning(f"Attempted to stop unknown daemon: {daemon_name}")
    return {"error": "unknown daemon"}


@app.get("/api/t212/account")
def api_t212_account():
    """Cash summary from T212 (demo or live, based on T212_MODE)."""
    try:
        from datadesk.ingest.t212_client import T212Client
        client = T212Client()
        cash = client.get_cash()
        return {
            "status": "ok",
            "mode": client.mode,
            "free": round(cash.free, 2),
            "invested": round(cash.invested, 2),
            "ppl": round(cash.ppl, 2),
            "result": round(cash.result, 2),
            "total": round(cash.total, 2),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/t212/positions")
def api_t212_positions():
    """Open positions from T212."""
    try:
        from datadesk.ingest.t212_client import T212Client
        client = T212Client()
        positions = client.get_portfolio()
        return {
            "status": "ok",
            "positions": [
                {
                    "ticker": p.ticker,
                    "quantity": p.quantity,
                    "avg_price": p.avg_price,
                    "current_price": p.current_price,
                    "ppl": round(p.ppl, 2),
                    "fx_ppl": round(p.fx_ppl, 2) if p.fx_ppl is not None else None,
                    "market_value": round(p.quantity * p.current_price, 2),
                    "ppl_pct": round(p.ppl / (p.quantity * p.avg_price) * 100, 2)
                    if p.avg_price and p.quantity else 0.0,
                }
                for p in positions
            ],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


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

        alpaca = TradingClient(api_key, secret_key, paper=_ALPACA_PAPER)
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
            "pnl_pct": round(pnl_pct, 2),
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

        alpaca = TradingClient(api_key, secret_key, paper=_ALPACA_PAPER)
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

            result.append(
                {
                    "symbol": sym,
                    "qty": p.qty,
                    "market_value": p.market_value,
                    "unrealized_pl": p.unrealized_pl,
                    "unrealized_plpc": p.unrealized_plpc,
                    "side": side,
                    "ai_status": status_text,
                }
            )
        return {"status": "ok", "positions": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/alpaca/mode")
def api_alpaca_mode_get():
    return {"paper": _ALPACA_PAPER, "mode": "paper" if _ALPACA_PAPER else "live"}


@app.post("/api/alpaca/mode")
def api_alpaca_mode_set(mode: str):
    """Switch Alpaca between paper and live. mode='paper' or mode='live'."""
    global _ALPACA_PAPER
    if mode not in ("paper", "live"):
        return {"status": "error", "message": "mode must be 'paper' or 'live'"}
    _ALPACA_PAPER = mode == "paper"
    logger.warning(f"Alpaca mode switched to: {mode.upper()}")
    return {"status": "ok", "paper": _ALPACA_PAPER, "mode": mode}


@app.get("/api/t212/mode")
def api_t212_mode_get():
    import os
    active = os.getenv("T212_MODE", _T212_MODE)
    return {"mode": active}


@app.post("/api/t212/mode")
def api_t212_mode_set(mode: str):
    """Switch T212 between demo and live at runtime. mode='demo' or mode='live'."""
    global _T212_MODE
    if mode not in ("demo", "live"):
        return {"status": "error", "message": "mode must be 'demo' or 'live'"}
    _T212_MODE = mode
    import os
    os.environ["T212_MODE"] = mode  # picked up by t212_client on next instantiation
    logger.warning(f"T212 mode switched to: {mode.upper()}")
    return {"status": "ok", "mode": mode}


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
    logger.info(f"Add ticker request: {req.ticker}")
    result = add_ticker(req.ticker)
    logger.info(f"Add ticker result: {result}")
    return result


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

    global _sweep_running
    with _sweep_lock:
        if _sweep_running:
            return {"status": "already_running"}
        _sweep_running = True

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _run_and_clear():
        global _sweep_running
        try:
            subprocess.run(
                [".venv\\Scripts\\python", "sweep.py"],
                cwd=root,
            )
        finally:
            _sweep_running = False

    _threading.Thread(target=_run_and_clear, daemon=True).start()
    return {"status": "started"}


@app.post("/api/monte_carlo/run")
def api_monte_carlo_run(
    runs: int = 1000, model: str = "bootstrap", background_tasks: BackgroundTasks = None
):
    """Bootstrap strategy returns from the latest backtest run. Returns percentile fan bands."""
    if model not in MONTE_CARLO_CONFIG["models"]:
        return {"status": "error", "message": f"Model {model} not supported"}
    MONTE_CARLO_STATUS.update(
        {"running": True, "progress": 0, "total": runs, "result_path": None, "message": "Running", "result": None}
    )

    def run_sim():
        try:
            result_path = mc_sim.run_simulation(
                runs, model, status_callback=lambda p: MONTE_CARLO_STATUS.update({"progress": p})
            )
            import json
            with open(result_path) as f:
                result_data = json.load(f)
            MONTE_CARLO_STATUS.update({
                "running": False,
                "progress": runs,
                "result_path": result_path,
                "message": f"Done — {runs} paths bootstrapped from {result_data['n_days']} days of real returns",
                "result": result_data,
            })
        except Exception as e:
            logger.exception("Monte Carlo error")
            MONTE_CARLO_STATUS.update({"running": False, "message": f"Error: {e}", "result": None})

    if background_tasks:
        background_tasks.add_task(run_sim)
    else:
        run_sim()
    return {"status": "started", "runs": runs, "model": model}


@app.get("/api/monte_carlo/status")
def api_monte_carlo_status():
    """Return Monte Carlo status including result data when complete."""
    return MONTE_CARLO_STATUS


# ── Maintenance triggers ──────────────────────────────────────────────────────

_job_status: dict[str, dict] = {}
_job_lock = _threading.Lock()


def _run_job(job_id: str, fn, *args, **kwargs):
    with _job_lock:
        _job_status[job_id] = {"status": "running", "started_at": __import__("datetime").datetime.utcnow().isoformat()}
    try:
        result = fn(*args, **kwargs)
        with _job_lock:
            _job_status[job_id].update({"status": "done", "result": str(result)[:500]})
    except Exception as e:
        with _job_lock:
            _job_status[job_id].update({"status": "error", "error": str(e)[:200]})


@app.get("/api/jobs/status")
def api_jobs_status():
    with _job_lock:
        return dict(_job_status)


@app.post("/api/trigger/weekly-update")
def api_trigger_weekly_update(background_tasks: BackgroundTasks):
    """Trigger weekly maintenance: price gap-fill + fundamentals refresh."""
    from datadesk.config import ALTDATA_DB
    from datadesk.history.store import coverage
    from datadesk.ingest.backfill import backfill_smart
    from datadesk.ingest.fundamentals import fetch_fundamentals
    import sqlite3
    from datetime import datetime, timedelta

    def _weekly():
        cov = coverage()
        tradeable = [t for t in cov["ticker"].tolist() if not t.startswith("^")]
        written = backfill_smart(tradeable)
        stale_cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat(timespec="seconds")
        try:
            con = sqlite3.connect(ALTDATA_DB)
            fresh = {r[0] for r in con.execute(
                "SELECT ticker FROM equity_ratios WHERE fetched_at > ? GROUP BY ticker", (stale_cutoff,)
            )}
            con.close()
        except Exception:
            fresh = set()
        stale = [t for t in tradeable if t not in fresh]
        fetch_fundamentals(stale, verbose=False)
        return {"prices": sum(written.values()), "fundamentals_refreshed": len(stale)}

    job_id = f"weekly-{__import__('time').time():.0f}"
    background_tasks.add_task(_run_job, job_id, _weekly)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/trigger/enrich")
def api_trigger_enrich(background_tasks: BackgroundTasks, tickers: list[str] | None = None):
    """Fetch/refresh fundamentals for given tickers (or all in universe)."""
    from datadesk.history.store import coverage
    from datadesk.ingest.fundamentals import fetch_fundamentals

    def _enrich():
        t_list = tickers or [t for t in coverage()["ticker"].tolist() if not t.startswith("^")]
        return fetch_fundamentals(t_list, verbose=False)

    job_id = f"enrich-{__import__('time').time():.0f}"
    background_tasks.add_task(_run_job, job_id, _enrich)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/trigger/backfill")
def api_trigger_backfill(tickers: list[str], background_tasks: BackgroundTasks):
    """Backfill price history + fundamentals for given tickers."""
    from datadesk.ingest.backfill import backfill_history
    from datadesk.ingest.fundamentals import fetch_fundamentals

    def _backfill():
        written = backfill_history(tickers)
        fetch_fundamentals(tickers, verbose=False)
        return written

    job_id = f"backfill-{__import__('time').time():.0f}"
    background_tasks.add_task(_run_job, job_id, _backfill)
    return {"job_id": job_id, "status": "queued"}


def _clean_row(row: dict) -> dict:
    """Replace non-finite floats (NaN/Inf) with None so JSON serialization never fails."""
    import math
    return {
        k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v)
        for k, v in row.items()
    }


@app.get("/api/fundamentals")
def api_fundamentals(ticker: str | None = None):
    """Return stored fundamentals. Pass ?ticker=AAPL for one ticker, else returns all."""
    import sqlite3
    from datadesk.config import ALTDATA_DB
    try:
        con = sqlite3.connect(ALTDATA_DB)
        con.row_factory = sqlite3.Row
        if ticker:
            info_row = con.execute("SELECT * FROM equity_info WHERE ticker=?", (ticker,)).fetchone()
            ratios_row = con.execute(
                "SELECT * FROM equity_ratios WHERE ticker=? ORDER BY fetched_at DESC LIMIT 1", (ticker,)
            ).fetchone()
            fins = con.execute(
                "SELECT * FROM equity_financials WHERE ticker=? ORDER BY fiscal_year DESC LIMIT 5", (ticker,)
            ).fetchall()
            bal = con.execute(
                "SELECT * FROM equity_balance WHERE ticker=? ORDER BY fiscal_year DESC LIMIT 5", (ticker,)
            ).fetchall()
            return {
                "info": _clean_row(dict(info_row)) if info_row else None,
                "ratios": _clean_row(dict(ratios_row)) if ratios_row else None,
                "financials": [_clean_row(dict(r)) for r in fins],
                "balance": [_clean_row(dict(r)) for r in bal],
            }
        else:
            rows = con.execute("""
                SELECT r.ticker, i.name, i.sector, i.industry, i.country, i.exchange,
                       r.market_cap, r.trailing_pe, r.forward_pe, r.price_to_book,
                       r.price_to_sales, r.ev_to_ebitda,
                       r.dividend_yield, r.payout_ratio,
                       r.revenue, r.revenue_growth,
                       r.gross_margin, r.operating_margin, r.net_margin,
                       r.roe, r.roa, r.debt_to_equity, r.current_ratio,
                       r.free_cashflow, r.beta,
                       r.week52_high, r.week52_low, r.week52_change,
                       r.short_pct_float, r.fetched_at
                FROM equity_ratios r
                LEFT JOIN equity_info i ON r.ticker=i.ticker
                WHERE r.id IN (SELECT MAX(id) FROM equity_ratios GROUP BY ticker)
                ORDER BY r.market_cap DESC NULLS LAST
            """).fetchall()
            return [_clean_row(dict(r)) for r in rows]
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            con.close()
        except Exception:
            pass


@app.get("/api/thesis")
def api_thesis(ticker: str | None = None):
    """
    Return investment thesis for one ticker (?ticker=NVDA) or all tickers.
    Template-based from stored fundamentals — no LLM required.
    """
    from datadesk.analysis.thesis import generate_thesis, generate_all_theses
    import dataclasses

    def _serialise(t):
        return dataclasses.asdict(t)

    try:
        if ticker:
            return _serialise(generate_thesis(ticker))
        else:
            results = generate_all_theses()
            return [_serialise(v) for v in results.values()]
    except Exception as e:
        return {"error": str(e)}


_congress_study_cache: dict = {}
_trump_study_cache: dict = {}


@app.get("/api/analysis/congress")
def api_congress_event_study(refresh: bool = False):
    """
    Congress trading event study — measures forward returns after disclosure dates.
    Cached after first computation (set ?refresh=true to recompute).
    """
    global _congress_study_cache
    if not refresh and _congress_study_cache:
        return _congress_study_cache
    try:
        from datadesk.analysis.congress_events import run_congress_event_study
        import dataclasses
        study = run_congress_event_study()
        result = {
            "n_events": study.n_events,
            "n_tickers": study.n_tickers,
            "windows": study.windows,
            "avg_returns": study.avg_returns,
            "avg_abnormal": study.avg_abnormal,
            "top_tickers": study.top_tickers,
            "top_legislators": study.top_legislators,
        }
        _congress_study_cache = result
        return result
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/analysis/trump")
def api_trump_event_study(refresh: bool = False):
    """
    Trump post event study — classifies posts by category, measures SPY abnormal return.
    Cached after first computation (set ?refresh=true to recompute).
    """
    global _trump_study_cache
    if not refresh and _trump_study_cache:
        return _trump_study_cache
    try:
        from datadesk.analysis.trump_events import run_trump_event_study
        study = run_trump_event_study()
        result = {
            "n_posts": study.n_posts,
            "n_actionable": study.n_actionable,
            "windows": study.windows,
            "category_abnormal": study.category_abnormal,
            "category_counts": study.category_counts,
            "top_events": [
                {k: (str(v) if hasattr(v, "isoformat") else v) for k, v in e.items()}
                for e in study.top_events
            ],
        }
        _trump_study_cache = result
        return result
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/phase")
def api_phase(nav: float = 0.0, monthly: float = 500.0, cagr: float = 0.20, years: int = 15):
    """
    Return current portfolio phase + NAV projection.

    ?nav=5000       current portfolio NAV in GBP
    ?monthly=500    monthly contribution
    ?cagr=0.20      assumed annual CAGR for projection
    ?years=15       projection horizon
    """
    from datadesk.strategies.phase import (
        PHASES, _THRESHOLDS, portfolio_phase, simulate_nav_series
    )
    phase = portfolio_phase(nav)
    phase_idx = PHASES.index(phase)
    next_threshold = _THRESHOLDS[phase_idx] if phase_idx < len(_THRESHOLDS) else None

    projection = simulate_nav_series(monthly, max(nav, monthly), cagr, years)
    months_to_next = None
    if next_threshold:
        for month, pnav, _ in projection:
            if pnav >= next_threshold:
                months_to_next = month
                break

    return {
        "nav_gbp": nav,
        "phase": {
            "label": phase.label,
            "top_n": phase.top_n,
            "rebal_freq": phase.rebal_freq,
            "min_position_gbp": phase.min_position_gbp,
            "description": phase.description,
        },
        "next_threshold_gbp": next_threshold,
        "months_to_next_phase": months_to_next,
        "projection": [
            {"month": m, "nav_gbp": n, "phase": p} for m, n, p in projection
        ],
        "thresholds": _THRESHOLDS,
    }


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
