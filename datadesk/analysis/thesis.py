"""
Ticker thesis generator.

Produces a structured investment thesis for each ticker from stored fundamentals.
Template-based — no LLM required. Uses valuation multiples, growth rates,
margins, and sector context to generate bull/bear/risk bullets.

Output is a ThesisResult dataclass that the API serialises to JSON.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from datadesk.config import ALTDATA_DB


@dataclass
class ThesisResult:
    ticker: str
    name: str
    sector: str
    country: str
    summary: str
    bull: list[str] = field(default_factory=list)
    bear: list[str] = field(default_factory=list)
    risk: list[str] = field(default_factory=list)
    data_quality: str = "ok"   # "ok" | "sparse" | "no_data"


def _pct(v) -> str:
    if v is None:
        return "—"
    return f"{v*100:+.1f}%"


def _x(v, digits=1) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}x"


def generate_thesis(ticker: str, db_path: Path | None = None) -> ThesisResult:
    """Generate a structured thesis for one ticker from stored fundamentals."""
    db = db_path or ALTDATA_DB
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    def _fetch(sql, params=()):
        try:
            return con.execute(sql, params).fetchone()
        except Exception:
            return None

    def _fetch_all(sql, params=()):
        try:
            return con.execute(sql, params).fetchall()
        except Exception:
            return []

    info   = _fetch("SELECT * FROM equity_info WHERE ticker=?", (ticker,))
    ratios = _fetch("SELECT * FROM equity_ratios WHERE ticker=? ORDER BY fetched_at DESC LIMIT 1", (ticker,))
    fins   = _fetch_all("SELECT * FROM equity_financials WHERE ticker=? ORDER BY fiscal_year DESC LIMIT 2", (ticker,))
    bal    = _fetch("SELECT * FROM equity_balance WHERE ticker=? ORDER BY fiscal_year DESC LIMIT 1", (ticker,))
    con.close()

    info_d = dict(info) if info else {}
    name    = info_d.get("name") or ticker
    sector  = info_d.get("sector") or "Unknown"
    country = info_d.get("country") or "—"

    if not ratios and not fins:
        return ThesisResult(
            ticker=ticker, name=name, sector=sector, country=country,
            summary=f"{name} — no fundamental data available. Run `enrich` to fetch.",
            data_quality="no_data",
        )

    r = dict(ratios) if ratios else {}
    f0 = dict(fins[0]) if fins else {}
    f1 = dict(fins[1]) if len(fins) > 1 else {}
    b  = dict(bal) if bal else {}

    mkt_cap      = r.get("market_cap")
    rev_growth   = r.get("revenue_growth")
    gross_margin = r.get("gross_margin")
    net_margin   = r.get("net_margin")
    roe          = r.get("roe")
    de           = r.get("debt_to_equity")
    pe           = r.get("trailing_pe")
    fpe          = r.get("forward_pe")
    ev_eb        = r.get("ev_to_ebitda")
    beta         = r.get("beta")
    div_yield    = r.get("dividend_yield")
    week52_chg   = r.get("week52_change")

    cap_str = "—"
    if mkt_cap:
        if mkt_cap >= 1e12:  cap_str = f"${mkt_cap/1e12:.1f}T"
        elif mkt_cap >= 1e9: cap_str = f"${mkt_cap/1e9:.1f}B"
        else:                cap_str = f"${mkt_cap/1e6:.0f}M"

    summary = (
        f"{name} ({ticker}) — {sector}, {country}. "
        f"Market cap {cap_str}. "
        + (f"Revenue growth {_pct(rev_growth)}, " if rev_growth is not None else "")
        + (f"net margin {_pct(net_margin)}. " if net_margin is not None else "")
        + (f"Trades at {_x(pe)} trailing PE" if pe else "")
        + (f" / {_x(fpe)} forward PE" if fpe else "")
        + ("." if pe or fpe else "")
    ).strip()

    bull: list[str] = []
    bear: list[str] = []
    risk: list[str] = []

    # ── Bull case ─────────────────────────────────────────────────────────────
    if rev_growth is not None and rev_growth > 0.20:
        bull.append(f"Strong revenue growth ({_pct(rev_growth)} YoY) well above market average.")
    elif rev_growth is not None and rev_growth > 0.10:
        bull.append(f"Solid revenue growth at {_pct(rev_growth)} YoY.")

    if gross_margin is not None and gross_margin > 0.60:
        bull.append(f"Exceptional gross margin ({_pct(gross_margin)}) — high-moat business model.")
    elif gross_margin is not None and gross_margin > 0.40:
        bull.append(f"Healthy gross margin ({_pct(gross_margin)}).")

    if roe is not None and roe > 0.20:
        bull.append(f"High return on equity ({_pct(roe)}) demonstrates capital efficiency.")

    if net_margin is not None and net_margin > 0.15:
        bull.append(f"Strong profitability: {_pct(net_margin)} net margin.")

    if fpe and pe and pe > 0 and fpe > 0 and fpe < pe * 0.80:
        bull.append(f"Forward PE ({_x(fpe)}) significantly below trailing ({_x(pe)}) — earnings acceleration expected.")

    if div_yield is not None and 0.005 < div_yield < 0.15:  # 0.5%–15% plausible range
        bull.append(f"Income yield of {_pct(div_yield)} provides downside cushion.")

    if b.get("cash") and b.get("total_debt") and b["cash"] > b["total_debt"]:
        bull.append("Net cash position — zero financial distress risk, capacity for buybacks or M&A.")

    if week52_chg is not None and week52_chg > 0.30:
        bull.append(f"52-week momentum of {_pct(week52_chg)} — price confirms fundamental strength.")

    # Sector-specific bull
    if "Semiconductor" in sector or "Technology" in sector:
        bull.append("AI infrastructure build-out is a decade-long secular tailwind for the sector.")
    if "Energy" in sector and country in ("United States", "GB", "United Kingdom"):
        bull.append("Data centre power demand is driving a structural step-change in electricity consumption.")
    if "Financial" in sector and country in ("United Kingdom", "GB"):
        bull.append("UK rate normalisation should improve net interest margin over the medium term.")

    # ── Bear case ──────────────────────────────────────────────────────────────
    if pe is not None and pe > 50:
        bear.append(f"Premium valuation ({_x(pe)} PE) leaves limited margin of safety — any miss could re-rate sharply.")

    if ev_eb is not None and ev_eb > 40:
        bear.append(f"EV/EBITDA of {_x(ev_eb)} prices in aggressive growth that must be delivered.")

    if rev_growth is not None and rev_growth < 0:
        bear.append(f"Revenue is contracting ({_pct(rev_growth)}) — business faces structural or cyclical headwinds.")

    if net_margin is not None and net_margin < 0:
        bear.append(f"Loss-making at {_pct(net_margin)} net margin — cash burn must be monitored.")

    if de is not None and de > 2.0 and sector not in ("Financial Services", "Utilities", "Real Estate"):
        bear.append(f"Elevated leverage (D/E {de:.1f}x) may restrict financial flexibility in a higher-for-longer rate environment.")

    if week52_chg is not None and week52_chg < -0.20:
        bear.append(f"Down {_pct(week52_chg)} over 52 weeks — negative price momentum suggests unresolved issues.")

    if f0 and f1 and f0.get("revenue") and f1.get("revenue") and f1["revenue"] > 0:
        rev_chg = (f0["revenue"] - f1["revenue"]) / f1["revenue"]
        if rev_chg < 0:
            bear.append(f"Revenue fell {_pct(rev_chg)} in the most recent fiscal year reported.")

    # ── Risk factors ──────────────────────────────────────────────────────────
    if beta is not None and beta > 1.5:
        risk.append(f"High beta ({beta:.2f}) — amplifies market drawdowns; position-size accordingly.")
    if beta is not None and beta < 0.3:
        risk.append(f"Low beta ({beta:.2f}) — may underperform in momentum/bull regimes.")

    if "Semiconductor" in sector:
        risk.append("US-China trade policy and export controls are an ongoing binary risk for the sector.")
    if "China" in (info_d.get("description") or "") or country in ("China", "HK"):
        risk.append("Geopolitical risk — US regulatory action, delisting threat, or Taiwan-strait escalation.")
    if country in ("United Kingdom", "GB"):
        risk.append("GBP/USD and GBP/EUR FX exposure adds 0.30% round-trip cost in a T212 ISA for non-GBP investors.")

    if not bull:
        bull.append("Insufficient fundamental data to identify specific bull catalysts — run enrich to refresh.")
    if not bear:
        bear.append("No material fundamental red flags identified at current data snapshot.")

    data_quality = "ok" if (rev_growth is not None and pe is not None) else "sparse"

    return ThesisResult(
        ticker=ticker,
        name=name,
        sector=sector,
        country=country,
        summary=summary,
        bull=bull,
        bear=bear,
        risk=risk,
        data_quality=data_quality,
    )


def generate_all_theses(db_path: Path | None = None) -> dict[str, ThesisResult]:
    """Generate theses for all tickers in equity_info."""
    db = db_path or ALTDATA_DB
    con = sqlite3.connect(db)
    tickers = [r[0] for r in con.execute("SELECT ticker FROM equity_info").fetchall()]
    con.close()
    return {t: generate_thesis(t, db_path=db) for t in tickers}
