"""
Trump post classifier — taxonomy from research-handoff-v3.

Keyword-rule first pass (deterministic, testable, no hallucination); the local
LLM (Ollama/Phi) can be layered on later for recall, but a post only ever
becomes a signal through these rules. Default verdict is NOISE.
"""

import re
from dataclasses import dataclass, field

# Company name → ticker (only names Trump actually posts about; extend as observed)
COMPANY_TICKERS = {
    "apple": "AAPL",
    "tesla": "TSLA",
    "ford": "F",
    "general motors": "GM",
    "boeing": "BA",
    "disney": "DIS",
    "john deere": "DE",
    "deere": "DE",
    "harley": "HOG",
    "harley-davidson": "HOG",
    "intel": "INTC",
    "nvidia": "NVDA",
    "amazon": "AMZN",
    "facebook": "META",
    "meta": "META",
    "google": "GOOGL",
    "microsoft": "MSFT",
    "walmart": "WMT",
    "caterpillar": "CAT",
    "pfizer": "PFE",
    "exxon": "XOM",
    "carrier": "CARR",
    "us steel": "X",
    "nippon steel": "X",  # acquisition target — the tradeable side is X
}

_TARIFF = re.compile(r"\btariff", re.I)
_THREAT = re.compile(r"\b(tariff|100%|200%|tax(ed|es)? (on|them)|will pay|sanction)", re.I)
_ENDORSE = re.compile(
    r"\b(great company|tremendous|fantastic job|doing a great|very successful|incredible (company|work)|congratulations)",
    re.I,
)
_GRIEVANCE = re.compile(
    r"\b(disgrace|terrible|failing|fake|boycott|worst|rip(ping|ped)? (us )?off|disaster|crooked)",
    re.I,
)
_MACRO = re.compile(
    r"\b(fed|federal reserve|interest rate|powell|inflation|economy|gdp|dollar|stock market|trade deal|china deal)",
    re.I,
)


@dataclass
class PostClassification:
    impact_class: (
        str  # TARIFF_THREAT | COMPANY_ENDORSEMENT | COMPANY_GRIEVANCE | MACRO_COMMENTARY | NOISE
    )
    actionable_tickers: list[str] = field(default_factory=list)
    sentiment: str = "NEUTRAL"  # POSITIVE | NEGATIVE | NEUTRAL
    confidence: float = 0.0


def _find_tickers(text: str) -> list[str]:
    low = text.lower()
    found = []
    for name, ticker in COMPANY_TICKERS.items():
        pattern = r"\b" + re.escape(name) + r"\b"
        if re.search(pattern, low) and ticker not in found:
            found.append(ticker)
    return found


def classify_post(text: str) -> PostClassification:
    """Deterministic rule classification. NOISE unless rules positively match."""
    if not text or not text.strip():
        return PostClassification("NOISE")

    tickers = _find_tickers(text)

    if tickers and _TARIFF.search(text) and _THREAT.search(text):
        return PostClassification("TARIFF_THREAT", tickers, "NEGATIVE", 0.8)

    if tickers and _GRIEVANCE.search(text):
        return PostClassification("COMPANY_GRIEVANCE", tickers, "NEGATIVE", 0.7)

    if tickers and _ENDORSE.search(text):
        return PostClassification("COMPANY_ENDORSEMENT", tickers, "POSITIVE", 0.6)

    if _MACRO.search(text):
        # Macro is never a single-stock trade — implies index/vol hedging only
        return PostClassification("MACRO_COMMENTARY", [], "NEUTRAL", 0.5)

    return PostClassification("NOISE", [], "NEUTRAL", 0.9)
