import pandas as pd

from datadesk.history.store import coverage, load_closes, save_bars


def test_save_and_load_roundtrip(tmp_path):
    db = tmp_path / "hist.db"
    df = pd.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "MSFT"],
            "date": ["2024-01-02", "2024-01-03", "2024-01-02"],
            "close": [185.0, 186.5, 370.0],
            "volume": [1e6, 1.1e6, 2e6],
        }
    )
    assert save_bars(df, source="test", db_path=db) == 3

    closes = load_closes(db_path=db)
    assert closes.loc["2024-01-03", "AAPL"] == 186.5
    assert pd.isna(closes.loc["2024-01-03", "MSFT"])  # no bar that day


def test_upsert_overwrites_same_key(tmp_path):
    db = tmp_path / "hist.db"
    row = {"ticker": ["AAPL"], "date": ["2024-01-02"], "close": [185.0]}
    save_bars(pd.DataFrame(row), source="a", db_path=db)
    row["close"] = [186.0]
    save_bars(pd.DataFrame(row), source="b", db_path=db)
    assert load_closes(db_path=db).iloc[0, 0] == 186.0


def test_nan_close_rows_skipped(tmp_path):
    db = tmp_path / "hist.db"
    df = pd.DataFrame(
        {"ticker": ["A", "B"], "date": ["2024-01-02", "2024-01-02"], "close": [100.0, None]}
    )
    assert save_bars(df, source="test", db_path=db) == 1


def test_date_range_filter(tmp_path):
    db = tmp_path / "hist.db"
    df = pd.DataFrame(
        {
            "ticker": ["A"] * 3,
            "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "close": [1.0, 2.0, 3.0],
        }
    )
    save_bars(df, source="test", db_path=db)
    closes = load_closes(start="2024-01-03", end="2024-01-03", db_path=db)
    assert len(closes) == 1


def test_coverage_report(tmp_path):
    db = tmp_path / "hist.db"
    df = pd.DataFrame(
        {
            "ticker": ["A", "A", "B"],
            "date": ["2024-01-02", "2024-01-03", "2024-01-02"],
            "close": [1.0, 2.0, 3.0],
        }
    )
    save_bars(df, source="test", db_path=db)
    cov = coverage(db_path=db)
    assert cov.set_index("ticker").loc["A", "rows"] == 2
