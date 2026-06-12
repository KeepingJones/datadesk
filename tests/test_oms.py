"""OMS Fast-Path: shadow recording, risk limits, side-aware math. No broker, no network."""

import pytest

from datadesk.live import shadow
from datadesk.live.oms import HISTORIC_TRADES, OMSFastPath, TickerMapper


@pytest.fixture
def oms(tmp_path, monkeypatch):
    # route shadow records to a temp db and guarantee shadow mode
    monkeypatch.delenv("DATADESK_ARM_BROKER", raising=False)
    db = tmp_path / "platform.db"
    monkeypatch.setattr(shadow, "PLATFORM_DB", db)
    o = OMSFastPath()
    o._shadow_db = db
    return o


def test_shadow_mode_by_default_even_with_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPACA_API_KEY", "fake")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "fake")
    monkeypatch.delenv("DATADESK_ARM_BROKER", raising=False)
    monkeypatch.setattr(shadow, "PLATFORM_DB", tmp_path / "p.db")
    o = OMSFastPath()
    assert o.is_armed is False  # keys alone must never arm the broker


def test_signal_recorded_to_shadow_store(oms, tmp_path):
    assert oms.submit_signal("AAPL", "BUY", 0.05, price=185.0, source="test") is True
    df = shadow.load_signals(db_path=shadow.PLATFORM_DB)
    assert len(df) == 1
    row = df.iloc[0]
    assert (row["ticker"], row["side"], row["executed"]) == ("AAPL", "BUY", 0)
    assert row["ref_price"] == 185.0
    assert "AAPL" in oms.active_positions


def test_weight_truncated_to_max_position(oms):
    oms.submit_signal("AAPL", "BUY", 0.50, price=100.0)
    assert oms.active_positions["AAPL"]["alloc"] == oms.max_position_pct


def test_daily_loss_kill_switch_rejects_signals(oms):
    oms.current_nav = oms.daily_starting_nav * 0.94  # -6% > 5% limit
    assert oms.submit_signal("AAPL", "BUY", 0.05, price=100.0) is False
    assert "AAPL" not in oms.active_positions


def test_close_records_side_aware_pnl(oms):
    HISTORIC_TRADES.clear()
    oms.submit_signal("AAPL", "BUY", 0.10, price=100.0)
    oms.active_positions["AAPL"]["current_price"] = 110.0
    oms.submit_signal("AAPL", "SELL", 0.10, price=110.0)
    assert HISTORIC_TRADES[-1]["pnl"] == pytest.approx(10.0 * 0.10)  # long up = profit


def test_unknown_entry_price_gives_none_pnl_not_fake(oms):
    HISTORIC_TRADES.clear()
    oms.submit_signal("MSFT", "BUY", 0.10)  # no price known
    oms.submit_signal("MSFT", "SELL", 0.10)
    assert HISTORIC_TRADES[-1]["pnl"] is None  # never fabricated from a fake 100.0


def test_trailing_stop_liquidates_long(oms):
    oms.submit_signal("AAPL", "BUY", 0.10, price=100.0, stop_loss_pct=0.02)
    oms.update_prices("AAPL", 110.0)  # stop ratchets to 107.8
    oms.update_prices("AAPL", 107.0)  # below stop → liquidate
    assert "AAPL" not in oms.active_positions


def test_fundamental_stop_is_side_aware(oms):
    oms.submit_signal("AAPL", "BUY", 0.10, price=100.0)
    # FV above price: thesis intact, no liquidation
    oms.update_fundamental_target("AAPL", 120.0)
    assert "AAPL" in oms.active_positions
    # FV collapses below price*0.95: long thesis broken → liquidate
    oms.update_fundamental_target("AAPL", 90.0)
    assert "AAPL" not in oms.active_positions


def test_take_profit_at_fair_value(oms):
    oms.submit_signal("AAPL", "BUY", 0.10, price=100.0)
    oms.update_fundamental_target("AAPL", 105.0)
    oms.update_prices("AAPL", 105.5)
    assert "AAPL" not in oms.active_positions


def test_ticker_mapper_routing():
    assert TickerMapper.is_us_stock("AAPL") is True
    assert TickerMapper.is_us_stock("ULVR.L") is False
    assert TickerMapper.to_broker("ULVR.L", "Trading212") == "ULVR"
