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
    # New formula: pct_return × allocated_capital = (10/100) × (0.10 × 100_000) = 1_000
    assert HISTORIC_TRADES[-1]["pnl"] == pytest.approx(1_000.0)  # 10% return on $10k allocated


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

def test_t212_armed_is_armed_true(monkeypatch, tmp_path):
    monkeypatch.setenv("T212_DEMO_API_KEY", "fake_key")
    monkeypatch.setenv("DATADESK_ARM_BROKER", "1")
    monkeypatch.setattr(shadow, "PLATFORM_DB", tmp_path / "p.db")
    # Mock T212Client so it doesn't actually hit network
    class FakeT212:
        mode = "demo"
        def get_equity(self): return 1000.0
    import sys
    sys.modules['datadesk.ingest.t212_client'] = type('t212_client', (), {'T212Client': FakeT212})
    o = OMSFastPath()
    assert o.is_armed is True

def test_t212_never_auto_executes_even_when_armed(oms, monkeypatch):
    """The gap this fix closes: DATADESK_ARM_BROKER=1 + a valid, ready T212
    signal must NOT place a real order. It must land as a pending proposal
    requiring a separate, explicit confirm_t212_order() call."""
    monkeypatch.setenv("T212_DEMO_API_KEY", "fake_key")
    monkeypatch.setenv("DATADESK_ARM_BROKER", "1")

    class FakeT212Client:
        mode = "demo"
        submitted = False

        def get_equity(self):
            return 10000.0

        def place_market_order(self, ticker, quantity):
            FakeT212Client.submitted = True  # would only flip if a real order fired
            return {"orderId": "t212_12345"}

    oms.t212 = FakeT212Client()
    result = oms.submit_signal("ULVR.L", "BUY", 0.05, price=40.0, source="manual")

    assert result is True  # signal accepted (risk checks passed)...
    assert FakeT212Client.submitted is False  # ...but no order reached the broker
    assert len(oms.pending_t212_orders) == 1  # instead it's queued for confirmation

    df = shadow.load_signals(db_path=oms._shadow_db)
    assert df.iloc[0]["executed"] == 0
    assert df.iloc[0]["order_id"] is None


def test_t212_order_id_persisted_after_explicit_confirm(oms, monkeypatch):
    """Once queued, confirm_t212_order() is the only path that can submit —
    and it must be called separately, one proposal at a time."""
    monkeypatch.setenv("T212_DEMO_API_KEY", "fake_key")
    monkeypatch.setenv("DATADESK_ARM_BROKER", "1")

    class FakeT212Client:
        mode = "demo"
        def get_equity(self): return 10000.0
        def place_market_order(self, ticker, quantity): return {"orderId": "t212_12345"}

    oms.t212 = FakeT212Client()
    oms.submit_signal("ULVR.L", "BUY", 0.05, price=40.0)
    assert len(oms.pending_t212_orders) == 1
    (proposal_id,) = oms.pending_t212_orders.keys()

    order_id = oms.confirm_t212_order(proposal_id)

    assert order_id == "t212_12345"
    assert oms.pending_t212_orders == {}  # consumed, can't be double-submitted
    df = shadow.load_signals(db_path=oms._shadow_db)
    assert df.iloc[0]["order_id"] == "t212_12345"


def test_t212_confirm_unknown_proposal_is_noop(oms):
    assert oms.confirm_t212_order("does-not-exist") is None


def test_t212_reject_discards_without_submitting(oms, monkeypatch):
    monkeypatch.setenv("T212_DEMO_API_KEY", "fake_key")
    monkeypatch.setenv("DATADESK_ARM_BROKER", "1")

    class FakeT212Client:
        mode = "demo"
        submitted = False

        def get_equity(self):
            return 10000.0

        def place_market_order(self, ticker, quantity):
            FakeT212Client.submitted = True
            return {"orderId": "should-never-happen"}

    oms.t212 = FakeT212Client()
    oms.submit_signal("ULVR.L", "BUY", 0.05, price=40.0)
    (proposal_id,) = oms.pending_t212_orders.keys()

    assert oms.reject_t212_order(proposal_id) is True
    assert oms.pending_t212_orders == {}
    assert oms.confirm_t212_order(proposal_id) is None  # already gone
    assert FakeT212Client.submitted is False

def test_rebalancer_queries_db(tmp_path, monkeypatch):
    from datadesk.live.monitors.rebalancer import _get_best_run
    db = tmp_path / "platform.db"
    import sqlite3
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE backtest_runs (name TEXT, params TEXT, metrics TEXT)")
        con.execute("INSERT INTO backtest_runs VALUES ('test1', '{\"a\":1}', '{\"sharpe\": 1.5, \"max_drawdown\": -0.20}')")
        con.execute("INSERT INTO backtest_runs VALUES ('test2', '{\"a\":2}', '{\"sharpe\": 2.5, \"max_drawdown\": -0.25}')")
        con.execute("INSERT INTO backtest_runs VALUES ('test3', '{\"a\":3}', '{\"sharpe\": 3.5, \"max_drawdown\": -0.40}')") # Invalid MaxDD
    monkeypatch.setattr("datadesk.db.PLATFORM_DB", db)
    best = _get_best_run()
    assert best["name"] == "test2"

