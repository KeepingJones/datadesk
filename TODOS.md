# DataDesk TODOS

Items deferred from CEO review on 2026-06-14. Pick these up after the public showcase is live.

---

## P2 — Tiingo Gate 1 Honest Re-run

**What:** Subscribe to Tiingo or EODHD (~£20/mo) and re-run the Gate 1 backtest with a point-in-time universe to correct for survivorship bias.

**Why:** Gate 1 MaxDD currently fails with a biased universe (-14% vs SPY -9%). Documented awareness of the bias is sufficient for the initial showcase, but a hiring manager who asks "have you corrected for survivorship bias?" will want a real number, not just an acknowledgement.

**Current state:** DEVELOPMENT.md §16 documents the bias sources honestly (~25% of gross CAGR attributed to survivorship + NVDA concentration). The honest estimated range is 13-15% after correction, but this is an estimate.

**Pros:**
- Gate 1 result with real numbers is a legitimately rare claim for a retail portfolio
- Shows willingness to pay for correctness, not just document the problem
- MaxDD may still fail after correction (honest failure is explainable and credible)

**Cons:**
- ~£20/mo ongoing cost
- 3-5 days of work to plumb Tiingo point-in-time data into backfill.py and re-run sweep
- Real numbers may be worse than the estimate (downside: Gate 1 still fails; upside: now definitively documented)

**When to revisit:** After 2-3 interviews at target firms. If quants ask for corrected numbers beyond what DEVELOPMENT.md §16 already provides, prioritise this. If the documented awareness is sufficient, defer further.

**Effort:** M (human: 3-5 days / CC: ~4 hours)
**Depends on:** Public showcase live + stable demo execution + at least 1 interview at target firm

---

## P3 — Options Overlay (Covered Calls + Cash-Secured Puts)

**What:** Add Black-Scholes synthetic pricing and covered call / CSP overlays to the equity book.

**Why:** Options on momentum portfolio is rare in candidate projects. Maximum interview conversation surface — shows knowledge of derivatives pricing and execution.

**Current state:** Not started. Synthetic BS pricing is the technically differentiated work not yet built (Design Doc Premise 5).

**When to revisit:** After live ISA capital is running stably and Gate 1 is passed. Options without equity book stability adds operational risk.

**Effort:** L-XL (human: 3-4 weeks / CC: ~1 week)
**Depends on:** Stable live execution (not demo) + Gate 1 passed on corrected universe
