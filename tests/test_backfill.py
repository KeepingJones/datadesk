"""Backfill with mocked yfinance — no network."""

import numpy as np
import pandas as pd

from datadesk.history.store import load_closes, save_bars
from datadesk.ingest import backfill


def fake_download_factory(tickers_served: list[str], n_days: int = 30):
    """Returns a fake yf.download serving a MultiIndex frame for given tickers."""

    def fake_download(batch, **kwargs):
        served = [t for t in (batch if isinstance(batch, list) else [batch]) if t in tickers_served]
        if not served:
            return pd.DataFrame()
        idx = pd.bdate_range("2024-01-01", periods=n_days)
        frames = {}
        for t in served:
            base = 100 + hash(t) % 50
            frames[t] = pd.DataFrame(
                {
                    "Open": base + np.arange(n_days) * 0.1,
                    "High": base + 1 + np.arange(n_days) * 0.1,
                    "Low": base - 1 + np.arange(n_days) * 0.1,
                    "Close": base + 0.5 + np.arange(n_days) * 0.1,
                    "Volume": 1e6,
                },
                index=idx,
            )
        return pd.concat(frames, axis=1)  # MultiIndex columns (ticker, field)

    return fake_download


def test_backfill_writes_bars(tmp_path, monkeypatch):
    db = tmp_path / "hist.db"
    monkeypatch.setattr(backfill.yf, "download", fake_download_factory(["AAPL", "MSFT"]))

    written = backfill.backfill_history(["AAPL", "MSFT"], db_path=db)
    assert written == {"AAPL": 30, "MSFT": 30}

    closes = load_closes(db_path=db)
    assert set(closes.columns) == {"AAPL", "MSFT"}
    assert len(closes) == 30


def test_backfill_unknown_ticker_returns_zero(tmp_path, monkeypatch):
    db = tmp_path / "hist.db"
    monkeypatch.setattr(backfill.yf, "download", fake_download_factory(["AAPL"]))

    written = backfill.backfill_history(["AAPL", "NOPE.XX"], db_path=db)
    assert written["AAPL"] == 30
    assert written["NOPE.XX"] == 0


def test_backfill_missing_skips_covered_tickers(tmp_path, monkeypatch):
    db = tmp_path / "hist.db"
    # AAPL already has plenty of history
    existing = pd.DataFrame(
        {
            "ticker": "AAPL",
            "date": pd.bdate_range("2018-01-01", periods=1500).strftime("%Y-%m-%d"),
            "close": 150.0,
        }
    )
    save_bars(existing, source="seed", db_path=db)

    calls = []
    real_fake = fake_download_factory(["AAPL", "NEW"])

    def spying_download(batch, **kwargs):
        calls.extend(batch if isinstance(batch, list) else [batch])
        return real_fake(batch, **kwargs)

    monkeypatch.setattr(backfill.yf, "download", spying_download)
    written = backfill.backfill_missing(["AAPL", "NEW"], min_rows=1000, db_path=db)

    assert "AAPL" not in calls  # covered → not re-downloaded
    assert written == {"NEW": 30}


def test_backfill_missing_all_covered_is_noop(tmp_path, monkeypatch):
    db = tmp_path / "hist.db"
    existing = pd.DataFrame(
        {
            "ticker": "AAPL",
            "date": pd.bdate_range("2018-01-01", periods=1500).strftime("%Y-%m-%d"),
            "close": 150.0,
        }
    )
    save_bars(existing, source="seed", db_path=db)

    def explode(*a, **k):
        raise AssertionError("should not download")

    monkeypatch.setattr(backfill.yf, "download", explode)
    assert backfill.backfill_missing(["AAPL"], min_rows=1000, db_path=db) == {}


def test_download_failure_is_contained(tmp_path, monkeypatch):
    db = tmp_path / "hist.db"

    def broken(*a, **k):
        raise ConnectionError("yahoo down")

    monkeypatch.setattr(backfill.yf, "download", broken)
    written = backfill.backfill_history(["AAPL"], db_path=db)
    assert written == {"AAPL": 0}
