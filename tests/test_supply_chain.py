"""Supply-chain lead-lag logic — injected moves, no network."""

from datadesk.live.monitors.supply_chain import find_laggards, load_matrix

MATRIX = {
    "NVDA": {"suppliers": ["TSM", "SMCI"], "customers": ["MSFT"]},
    "AAPL": {"suppliers": ["QCOM"], "customers": []},
}


def test_spike_with_unmoved_supplier_flags_laggard():
    moves = {"NVDA": 0.03, "TSM": 0.001, "SMCI": 0.04, "MSFT": 0.01}
    laggards = find_laggards(MATRIX, moves)
    assert ("TSM", "NVDA", 0.03) in laggards
    assert all(name != "SMCI" for name, _, _ in laggards)  # already moved
    assert all(name != "MSFT" for name, _, _ in laggards)  # moved past laggard band


def test_no_spike_no_signals():
    assert find_laggards(MATRIX, {"NVDA": 0.01, "TSM": 0.0}) == []


def test_negative_spike_ignored_long_only():
    assert find_laggards(MATRIX, {"NVDA": -0.05, "TSM": 0.0}) == []


def test_missing_laggard_data_is_skipped():
    laggards = find_laggards(MATRIX, {"NVDA": 0.03})  # no data for related names
    assert laggards == []


def test_shipped_matrix_loads_and_has_nvda():
    matrix = load_matrix()
    assert "NVDA" in matrix
    assert "TSM" in matrix["NVDA"]["suppliers"]
