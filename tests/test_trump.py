"""Trump collector with mocked HTTP — no network."""

import pandas as pd
import pytest

from datadesk.ingest import trump

SAMPLE = [
    {
        "id": "116732777898985789",
        "created_at": "2026-06-11T18:00:34.633Z",
        "content": "I am pleased to announce the Nomination of Jay Clayton...",
        "url": "https://truthsocial.com/@realDonaldTrump/116732777898985789",
        "media": [],
        "replies_count": 843,
        "reblogs_count": 2813,
        "favourites_count": 12404,
    },
    {
        "id": "107797156496908384",
        "created_at": "2022-02-14T15:54:32.528Z",
        "content": "Get Ready!",
        "url": "https://truthsocial.com/@realDonaldTrump/107797156496908384",
        "media": [],
        "replies_count": 33994,
        "reblogs_count": 49001,
        "favourites_count": 264075,
    },
]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_collect_saves_posts(tmp_path, monkeypatch):
    db = tmp_path / "alt.db"
    monkeypatch.setattr(trump.requests, "get", lambda url, timeout: FakeResponse(SAMPLE))
    assert trump.collect(db_path=db) == 2

    df = trump.load_posts(db_path=db)
    assert len(df) == 2
    assert df.iloc[0]["created_at"].year == 2022  # sorted ascending
    assert "Jay Clayton" in df.iloc[1]["content"]
    assert df["observed_at"].notna().all()


def test_recollect_is_idempotent_and_preserves_observed_at(tmp_path, monkeypatch):
    db = tmp_path / "alt.db"
    monkeypatch.setattr(trump.requests, "get", lambda url, timeout: FakeResponse(SAMPLE))
    trump.collect(db_path=db)
    first_observed = trump.load_posts(db_path=db)["observed_at"].tolist()

    assert trump.collect(db_path=db) == 0  # nothing new
    assert trump.load_posts(db_path=db)["observed_at"].tolist() == first_observed


def test_new_posts_appended(tmp_path, monkeypatch):
    db = tmp_path / "alt.db"
    monkeypatch.setattr(trump.requests, "get", lambda url, timeout: FakeResponse(SAMPLE[:1]))
    trump.collect(db_path=db)
    monkeypatch.setattr(trump.requests, "get", lambda url, timeout: FakeResponse(SAMPLE))
    assert trump.collect(db_path=db) == 1
    assert len(trump.load_posts(db_path=db)) == 2


def test_date_filter(tmp_path, monkeypatch):
    db = tmp_path / "alt.db"
    monkeypatch.setattr(trump.requests, "get", lambda url, timeout: FakeResponse(SAMPLE))
    trump.collect(db_path=db)
    df = trump.load_posts(start="2026-01-01", db_path=db)
    assert len(df) == 1


def test_malformed_rows_skipped(tmp_path):
    db = tmp_path / "alt.db"
    bad = [{"content": "no id"}, {"id": "x1", "created_at": "2026-01-01T00:00:00Z"}]
    assert trump.save_posts(bad, db_path=db) == 1


def test_non_list_archive_raises(monkeypatch):
    monkeypatch.setattr(trump.requests, "get", lambda url, timeout: FakeResponse({"not": "a list"}))
    with pytest.raises(ValueError):
        trump.fetch_archive()


def test_load_empty_returns_empty_frame(tmp_path):
    df = trump.load_posts(db_path=tmp_path / "alt.db")
    assert isinstance(df, pd.DataFrame)
    assert df.empty
