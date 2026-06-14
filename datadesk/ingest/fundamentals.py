"""
Fundamental data ingestion via yfinance.

Fetches and stores three layers:
  equity_info        — static: name, sector, industry, country, exchange, description
  equity_ratios      — snapshot ratios: PE, PB, PS, EV/EBITDA, dividend_yield, beta,
                        market_cap, revenue_growth, gross_margin, roe, debt_to_equity
  equity_financials  — annual income statement: revenue, gross_profit, ebit, net_income
  equity_balance     — annual balance sheet: total_assets, liabilities, cash, total_debt

All tables live in ALTDATA_DB.  equity_info is upserted (one row per ticker).
equity_ratios is append-only (timestamped snapshots).
equity_financials and equity_balance are keyed by (ticker, fiscal_year).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import yfinance as yf

from datadesk.config import ALTDATA_DB

logger = logging.getLogger(__name__)


# ── Schema ───────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS equity_info (
    ticker              TEXT PRIMARY KEY,
    name                TEXT,
    sector              TEXT,
    industry            TEXT,
    country             TEXT,
    exchange            TEXT,
    currency            TEXT,
    market_cap          REAL,
    shares_outstanding  REAL,
    employees           INTEGER,
    website             TEXT,
    description         TEXT,
    updated_at          TEXT
);

CREATE TABLE IF NOT EXISTS equity_ratios (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    market_cap      REAL,
    trailing_pe     REAL,
    forward_pe      REAL,
    price_to_book   REAL,
    price_to_sales  REAL,
    ev_to_ebitda    REAL,
    dividend_yield  REAL,
    payout_ratio    REAL,
    beta            REAL,
    revenue         REAL,
    revenue_growth  REAL,
    gross_margin    REAL,
    operating_margin REAL,
    net_margin      REAL,
    roe             REAL,
    roa             REAL,
    debt_to_equity  REAL,
    current_ratio   REAL,
    free_cashflow   REAL,
    week52_high     REAL,
    week52_low      REAL,
    week52_change   REAL,
    short_pct_float REAL
);

CREATE TABLE IF NOT EXISTS equity_financials (
    ticker          TEXT NOT NULL,
    fiscal_year     TEXT NOT NULL,
    revenue         REAL,
    gross_profit    REAL,
    ebit            REAL,
    net_income      REAL,
    eps             REAL,
    PRIMARY KEY (ticker, fiscal_year)
);

CREATE TABLE IF NOT EXISTS equity_balance (
    ticker          TEXT NOT NULL,
    fiscal_year     TEXT NOT NULL,
    total_assets    REAL,
    total_liabilities REAL,
    cash            REAL,
    total_debt      REAL,
    book_value      REAL,
    PRIMARY KEY (ticker, fiscal_year)
);

CREATE INDEX IF NOT EXISTS idx_eq_ratios_ticker ON equity_ratios(ticker);
CREATE INDEX IF NOT EXISTS idx_eq_fin_ticker    ON equity_financials(ticker);
CREATE INDEX IF NOT EXISTS idx_eq_bal_ticker    ON equity_balance(ticker);
"""


def _init_db(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.executescript(_DDL)
    con.commit()
    return con


# ── Helpers ──────────────────────────────────────────────────────────────────

def _g(d: dict, *keys, default=None):
    """Get first non-None value from a dict across multiple key names."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return default


def _pct_to_float(v) -> float | None:
    if v is None:
        return None
    return float(v)


def _div_yield_to_float(v) -> float | None:
    """Normalise dividendYield to a decimal fraction (0.04 = 4%).
    yfinance returns US yields as decimals (0.04) but some foreign tickers
    as percentage form (4.0). Values > 0.5 are clearly in pct form already."""
    if v is None:
        return None
    f = float(v)
    return f / 100.0 if f > 0.5 else f


def _safe_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── Main fetch ────────────────────────────────────────────────────────────────

def fetch_fundamentals(
    tickers: list[str],
    db_path: Path | None = None,
    verbose: bool = True,
) -> dict[str, bool]:
    """
    Fetch fundamentals for each ticker and upsert into ALTDATA_DB.
    Returns {ticker: success}.
    """
    db = db_path or ALTDATA_DB
    con = _init_db(db)
    now = datetime.utcnow().isoformat(timespec="seconds")
    results: dict[str, bool] = {}

    for t in tickers:
        try:
            tk = yf.Ticker(t)
            info = tk.info or {}

            if not info or info.get("quoteType") is None:
                logger.warning(f"fundamentals: no info for {t}")
                results[t] = False
                continue

            # ── equity_info ──────────────────────────────────────────────
            con.execute(
                """
                INSERT INTO equity_info
                    (ticker, name, sector, industry, country, exchange, currency,
                     market_cap, shares_outstanding, employees, website, description, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ticker) DO UPDATE SET
                    name=excluded.name, sector=excluded.sector,
                    industry=excluded.industry, country=excluded.country,
                    exchange=excluded.exchange, currency=excluded.currency,
                    market_cap=excluded.market_cap,
                    shares_outstanding=excluded.shares_outstanding,
                    employees=excluded.employees, website=excluded.website,
                    description=excluded.description, updated_at=excluded.updated_at
                """,
                (
                    t,
                    info.get("longName") or info.get("shortName"),
                    info.get("sector"),
                    info.get("industry"),
                    info.get("country"),
                    info.get("exchange"),
                    info.get("currency"),
                    _safe_float(info.get("marketCap")),
                    _safe_float(info.get("sharesOutstanding")),
                    info.get("fullTimeEmployees"),
                    info.get("website"),
                    (info.get("longBusinessSummary") or "")[:2000],
                    now,
                ),
            )

            # ── equity_ratios ────────────────────────────────────────────
            con.execute(
                """
                INSERT INTO equity_ratios
                    (ticker, fetched_at, market_cap, trailing_pe, forward_pe,
                     price_to_book, price_to_sales, ev_to_ebitda,
                     dividend_yield, payout_ratio, beta,
                     revenue, revenue_growth, gross_margin, operating_margin, net_margin,
                     roe, roa, debt_to_equity, current_ratio, free_cashflow,
                     week52_high, week52_low, week52_change, short_pct_float)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    t, now,
                    _safe_float(info.get("marketCap")),
                    _safe_float(_g(info, "trailingPE")),
                    _safe_float(_g(info, "forwardPE")),
                    _safe_float(info.get("priceToBook")),
                    _safe_float(info.get("priceToSalesTrailingTwelveMonths")),
                    _safe_float(_g(info, "enterpriseToEbitda")),
                    _div_yield_to_float(info.get("dividendYield")),
                    _pct_to_float(info.get("payoutRatio")),
                    _safe_float(info.get("beta")),
                    _safe_float(info.get("totalRevenue")),
                    _pct_to_float(info.get("revenueGrowth")),
                    _pct_to_float(info.get("grossMargins")),
                    _pct_to_float(info.get("operatingMargins")),
                    _pct_to_float(info.get("profitMargins")),
                    _pct_to_float(info.get("returnOnEquity")),
                    _pct_to_float(info.get("returnOnAssets")),
                    _safe_float(info.get("debtToEquity")),
                    _safe_float(info.get("currentRatio")),
                    _safe_float(info.get("freeCashflow")),
                    _safe_float(info.get("fiftyTwoWeekHigh")),
                    _safe_float(info.get("fiftyTwoWeekLow")),
                    _pct_to_float(info.get("52WeekChange")),
                    _pct_to_float(info.get("shortPercentOfFloat")),
                ),
            )

            # ── annual financials ────────────────────────────────────────
            try:
                fin = tk.financials  # rows: metric, cols: fiscal-year-end date
                if fin is not None and not fin.empty:
                    for col in fin.columns:
                        fy = str(col.date()) if hasattr(col, "date") else str(col)[:10]
                        row = fin[col]
                        rev = _safe_float(row.get("Total Revenue"))
                        gp  = _safe_float(row.get("Gross Profit"))
                        ebit = _safe_float(row.get("EBIT") or row.get("Operating Income"))
                        ni  = _safe_float(row.get("Net Income"))
                        eps = _safe_float(info.get("trailingEps"))
                        con.execute(
                            """
                            INSERT INTO equity_financials
                                (ticker, fiscal_year, revenue, gross_profit, ebit, net_income, eps)
                            VALUES (?,?,?,?,?,?,?)
                            ON CONFLICT(ticker, fiscal_year) DO UPDATE SET
                                revenue=excluded.revenue, gross_profit=excluded.gross_profit,
                                ebit=excluded.ebit, net_income=excluded.net_income, eps=excluded.eps
                            """,
                            (t, fy, rev, gp, ebit, ni, eps),
                        )
            except Exception as e:
                logger.debug(f"fundamentals: financials parse failed for {t}: {e}")

            # ── annual balance sheet ─────────────────────────────────────
            try:
                bal = tk.balance_sheet
                if bal is not None and not bal.empty:
                    for col in bal.columns:
                        fy = str(col.date()) if hasattr(col, "date") else str(col)[:10]
                        row = bal[col]
                        ta   = _safe_float(row.get("Total Assets"))
                        tl   = _safe_float(row.get("Total Liabilities Net Minority Interest")
                                           or row.get("Total Liabilities"))
                        cash = _safe_float(row.get("Cash And Cash Equivalents")
                                           or row.get("Cash"))
                        debt = _safe_float(row.get("Total Debt")
                                           or row.get("Long Term Debt"))
                        bv   = _safe_float(row.get("Stockholders Equity")
                                           or row.get("Total Equity Gross Minority Interest"))
                        con.execute(
                            """
                            INSERT INTO equity_balance
                                (ticker, fiscal_year, total_assets, total_liabilities,
                                 cash, total_debt, book_value)
                            VALUES (?,?,?,?,?,?,?)
                            ON CONFLICT(ticker, fiscal_year) DO UPDATE SET
                                total_assets=excluded.total_assets,
                                total_liabilities=excluded.total_liabilities,
                                cash=excluded.cash, total_debt=excluded.total_debt,
                                book_value=excluded.book_value
                            """,
                            (t, fy, ta, tl, cash, debt, bv),
                        )
            except Exception as e:
                logger.debug(f"fundamentals: balance sheet parse failed for {t}: {e}")

            con.commit()
            results[t] = True
            if verbose:
                name = info.get("longName") or info.get("shortName") or t
                mkt  = info.get("marketCap")
                mkt_s = f"£{mkt/1e9:.1f}bn" if mkt else "—"
                sector = info.get("sector") or "—"
                pe = info.get("trailingPE")
                pe_s = f"PE {pe:.1f}" if pe else "PE —"
                print(f"  {t:<12} {name[:30]:<30} {mkt_s:>8}  {sector:<22} {pe_s}")

        except Exception as e:
            logger.exception(f"fundamentals: failed for {t}: {e}")
            results[t] = False

    con.close()
    ok = sum(v for v in results.values())
    logger.info(f"fundamentals: {ok}/{len(tickers)} tickers stored")
    return results


def load_quality_excludes(
    db_path: Path | None = None,
    min_market_cap: float = 100_000_000,
    min_net_margin: float = -1.0,
) -> frozenset[str]:
    """
    Return set of tickers to EXCLUDE based on quality gates.

    Tickers NOT in the fundamentals DB pass through (opt-in exclusion philosophy).
    D/E ratio is NOT a gate — financial companies and buyback-heavy tech have structurally
    high D/E that is not a quality signal.

    Gates:
      - market_cap < min_market_cap (default $100M) — exclude micro-caps for liquidity
      - net_margin < min_net_margin (default -100%) — exclude deeply cash-burning companies

    Usage in callers:
        excluded = load_quality_excludes()
        eligible = set(prices.columns) - excluded
    """
    db = db_path or ALTDATA_DB
    try:
        con = sqlite3.connect(db)
        rows = con.execute(
            """
            SELECT r.ticker, r.market_cap, r.net_margin
            FROM equity_ratios r
            INNER JOIN (
                SELECT ticker, MAX(id) AS max_id FROM equity_ratios GROUP BY ticker
            ) latest ON r.id = latest.max_id
            """
        ).fetchall()
        con.close()
    except Exception as e:
        logger.warning(f"load_quality_excludes: DB read failed ({e}) — no exclusions applied")
        return frozenset()

    excluded: set[str] = set()
    for ticker, mkt_cap, net_margin in rows:
        if mkt_cap is not None and mkt_cap < min_market_cap:
            excluded.add(ticker)
        elif net_margin is not None and net_margin < min_net_margin:
            excluded.add(ticker)

    logger.info(
        f"load_quality_excludes: {len(excluded)}/{len(rows)} tickers excluded "
        f"(mkt_cap<{min_market_cap/1e6:.0f}M or net_margin<{min_net_margin:.0%})"
    )
    return frozenset(excluded)


def load_quality_universe(
    db_path: Path | None = None,
    min_market_cap: float = 100_000_000,
) -> frozenset[str]:
    """Deprecated alias — use load_quality_excludes() instead."""
    return load_quality_excludes(db_path=db_path, min_market_cap=min_market_cap)
