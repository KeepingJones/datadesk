"""Tests for portfolio phase logic."""

from datadesk.strategies.phase import (
    PHASES,
    _THRESHOLDS,
    portfolio_phase,
    simulate_nav_series,
    top_n_for_nav,
)


class TestPortfolioPhase:
    def test_phase1_at_zero(self):
        p = portfolio_phase(0)
        assert p.top_n == 3
        assert "Accumulation" in p.label

    def test_phase1_just_below_threshold(self):
        p = portfolio_phase(4_999)
        assert p.top_n == 3

    def test_phase2_at_threshold(self):
        p = portfolio_phase(5_000)
        assert p.top_n == 6
        assert "Growth" in p.label

    def test_phase3_at_25k(self):
        p = portfolio_phase(25_000)
        assert p.top_n == 10

    def test_phase4_above_100k(self):
        p = portfolio_phase(100_001)
        assert p.top_n == 15
        assert "Scale" in p.label

    def test_phases_are_monotone(self):
        navs = [0, 4_999, 5_000, 24_999, 25_000, 99_999, 100_000, 500_000]
        top_ns = [portfolio_phase(n).top_n for n in navs]
        assert top_ns == sorted(top_ns), "top_n should be non-decreasing with NAV"

    def test_top_n_for_nav_convenience(self):
        assert top_n_for_nav(1_000) == 3
        assert top_n_for_nav(10_000) == 6
        assert top_n_for_nav(50_000) == 10
        assert top_n_for_nav(200_000) == 15

    def test_thresholds_are_ascending(self):
        assert _THRESHOLDS == sorted(_THRESHOLDS)

    def test_phases_count(self):
        assert len(PHASES) == len(_THRESHOLDS) + 1


class TestSimulateNavSeries:
    def test_returns_correct_length(self):
        rows = simulate_nav_series(monthly_contribution_gbp=500, years=5)
        assert len(rows) == 60  # 5 years * 12 months

    def test_nav_grows_with_contributions(self):
        rows = simulate_nav_series(monthly_contribution_gbp=500, annual_cagr=0.0, initial_nav_gbp=0)
        # zero return: nav at month N = 500*N
        month, nav, _ = rows[11]  # month 12
        assert month == 12
        assert nav == pytest.approx(500 * 12, rel=0.01)

    def test_nav_grows_with_positive_cagr(self):
        rows_0 = simulate_nav_series(monthly_contribution_gbp=500, annual_cagr=0.0, years=10)
        rows_20 = simulate_nav_series(monthly_contribution_gbp=500, annual_cagr=0.20, years=10)
        # 20% CAGR should produce higher NAV than 0%
        assert rows_20[-1][1] > rows_0[-1][1]

    def test_phase_transitions_occur_in_order(self):
        rows = simulate_nav_series(monthly_contribution_gbp=500, annual_cagr=0.20, years=15)
        phases_seen = []
        for _, _, phase in rows:
            if not phases_seen or phase != phases_seen[-1]:
                phases_seen.append(phase)
        # Phases should appear in ascending order (1, 2, 3, 4)
        phase_nums = [int(p.split()[1]) for p in phases_seen]
        assert phase_nums == sorted(phase_nums)

    def test_initial_nav_respected(self):
        rows = simulate_nav_series(
            monthly_contribution_gbp=0, annual_cagr=0.0, initial_nav_gbp=10_000, years=1
        )
        # Zero contribution, zero return: NAV should stay at 10_000
        for _, nav, _ in rows:
            assert nav == pytest.approx(10_000, rel=0.01)


import pytest
