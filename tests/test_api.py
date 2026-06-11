"""Ops console API — TestClient, no server, no network."""

import pandas as pd
from fastapi.testclient import TestClient

from datadesk.api.app import app
from datadesk.db import load_backtest_runs, save_backtest_run

client = TestClient(app)


def test_health_reports_paper_mode():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["paper_trade_mode"] is True


def test_coverage_endpoint_shape():
    r = client.get("/api/coverage")
    assert r.status_code == 200
    assert {"tickers", "bars", "first", "last", "top"} <= set(r.json())


def test_runs_endpoint_returns_list():
    r = client.get("/api/runs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_index_renders():
    r = client.get("/")
    assert r.status_code == 200
    assert "DATADESK" in r.text
    assert "PAPER MODE ON" in r.text


def test_backtest_run_roundtrip(tmp_path):
    db = tmp_path / "platform.db"
    equity = pd.Series(
        [1.0, 1.01, 1.02],
        index=pd.bdate_range("2024-01-01", periods=3),
    )
    save_backtest_run("test", {"p": 1}, {"cagr": 0.1, "sharpe": 1.2}, equity, db_path=db)
    runs = load_backtest_runs(db_path=db)
    assert len(runs) == 1
    assert runs[0]["name"] == "test"
    assert runs[0]["equity"][0] == ["2024-01-01", 1.0]
    assert runs[0]["metrics"]["sharpe"] == 1.2
