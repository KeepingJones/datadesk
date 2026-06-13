"""Tests for the thesis generator."""

import pytest

from datadesk.analysis.thesis import ThesisResult, generate_thesis


class TestGenerateThesis:
    def test_returns_thesis_result(self, tmp_path):
        # With no DB data the function should return gracefully
        db = tmp_path / "test.db"
        result = generate_thesis("FAKE", db_path=db)
        assert isinstance(result, ThesisResult)
        assert result.ticker == "FAKE"
        assert result.data_quality == "no_data"

    def test_has_required_fields(self, tmp_path):
        db = tmp_path / "test.db"
        result = generate_thesis("XYZ", db_path=db)
        assert hasattr(result, "bull")
        assert hasattr(result, "bear")
        assert hasattr(result, "risk")
        assert hasattr(result, "summary")
        assert isinstance(result.bull, list)
        assert isinstance(result.bear, list)
        assert isinstance(result.risk, list)

    def test_no_data_returns_bullet(self, tmp_path):
        db = tmp_path / "test.db"
        result = generate_thesis("NONE", db_path=db)
        # Should still give at least a summary
        assert len(result.summary) > 0

    def test_with_fundamentals_data(self, tmp_path):
        import sqlite3
        from datetime import datetime

        db = tmp_path / "test.db"
        con = sqlite3.connect(db)
        con.executescript("""
            CREATE TABLE equity_info (
                ticker TEXT PRIMARY KEY, name TEXT, sector TEXT, industry TEXT,
                country TEXT, exchange TEXT, currency TEXT, market_cap REAL,
                shares_outstanding REAL, employees INTEGER, website TEXT,
                description TEXT, updated_at TEXT
            );
            CREATE TABLE equity_ratios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, fetched_at TEXT,
                market_cap REAL, trailing_pe REAL, forward_pe REAL, price_to_book REAL,
                price_to_sales REAL, ev_to_ebitda REAL, dividend_yield REAL,
                payout_ratio REAL, beta REAL, revenue REAL, revenue_growth REAL,
                gross_margin REAL, operating_margin REAL, net_margin REAL,
                roe REAL, roa REAL, debt_to_equity REAL, current_ratio REAL,
                free_cashflow REAL, week52_high REAL, week52_low REAL,
                week52_change REAL, short_pct_float REAL
            );
            CREATE TABLE equity_financials (
                ticker TEXT, fiscal_year TEXT, revenue REAL, gross_profit REAL,
                ebit REAL, net_income REAL, eps REAL, PRIMARY KEY (ticker, fiscal_year)
            );
            CREATE TABLE equity_balance (
                ticker TEXT, fiscal_year TEXT, total_assets REAL,
                total_liabilities REAL, cash REAL, total_debt REAL,
                book_value REAL, PRIMARY KEY (ticker, fiscal_year)
            );
        """)
        con.execute(
            "INSERT INTO equity_info VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("TEST", "Test Corp", "Technology", "Software", "United States",
             "NASDAQ", "USD", 50e9, 1e9, 5000, "https://test.com", "A test company.", datetime.utcnow().isoformat())
        )
        con.execute(
            "INSERT INTO equity_ratios (ticker, fetched_at, market_cap, trailing_pe, forward_pe, "
            "price_to_book, revenue_growth, gross_margin, net_margin, roe, debt_to_equity, beta, week52_change) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("TEST", datetime.utcnow().isoformat(), 50e9, 30.0, 22.0, 8.0, 0.25, 0.70, 0.18, 0.35, 0.5, 1.2, 0.45)
        )
        con.commit()
        con.close()

        result = generate_thesis("TEST", db_path=db)
        assert result.ticker == "TEST"
        assert result.data_quality == "ok"
        assert any("growth" in b.lower() or "%" in b for b in result.bull), "Should mention growth"
        assert any("margin" in b.lower() or "%" in b for b in result.bull), "Should mention margins"

    def test_high_pe_triggers_bear(self, tmp_path):
        import sqlite3
        from datetime import datetime

        db = tmp_path / "test.db"
        con = sqlite3.connect(db)
        con.executescript("""
            CREATE TABLE equity_info (ticker TEXT PRIMARY KEY, name TEXT, sector TEXT, industry TEXT,
                country TEXT, exchange TEXT, currency TEXT, market_cap REAL, shares_outstanding REAL,
                employees INTEGER, website TEXT, description TEXT, updated_at TEXT);
            CREATE TABLE equity_ratios (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT,
                fetched_at TEXT, market_cap REAL, trailing_pe REAL, forward_pe REAL,
                price_to_book REAL, price_to_sales REAL, ev_to_ebitda REAL,
                dividend_yield REAL, payout_ratio REAL, beta REAL, revenue REAL,
                revenue_growth REAL, gross_margin REAL, operating_margin REAL,
                net_margin REAL, roe REAL, roa REAL, debt_to_equity REAL,
                current_ratio REAL, free_cashflow REAL, week52_high REAL,
                week52_low REAL, week52_change REAL, short_pct_float REAL);
            CREATE TABLE equity_financials (ticker TEXT, fiscal_year TEXT, revenue REAL,
                gross_profit REAL, ebit REAL, net_income REAL, eps REAL,
                PRIMARY KEY (ticker, fiscal_year));
            CREATE TABLE equity_balance (ticker TEXT, fiscal_year TEXT, total_assets REAL,
                total_liabilities REAL, cash REAL, total_debt REAL, book_value REAL,
                PRIMARY KEY (ticker, fiscal_year));
        """)
        con.execute(
            "INSERT INTO equity_info VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("PRICEY", "Pricey Corp", "Technology", "SaaS", "United States",
             "NYSE", "USD", 200e9, 1e9, 2000, None, "Overvalued.", datetime.utcnow().isoformat())
        )
        con.execute(
            "INSERT INTO equity_ratios (ticker, fetched_at, market_cap, trailing_pe, beta) VALUES (?,?,?,?,?)",
            ("PRICEY", datetime.utcnow().isoformat(), 200e9, 120.0, 2.1)
        )
        con.commit()
        con.close()

        result = generate_thesis("PRICEY", db_path=db)
        # High PE should produce a bear bullet
        assert any("PE" in b or "valuation" in b.lower() or "premium" in b.lower() for b in result.bear)
        # High beta should produce a risk bullet
        assert any("beta" in r.lower() for r in result.risk)
