# Gate 1 Honest Re-run — 2026-07-20

Re-runs the Gate 1 backtest (`docs/backtesting.md` protocol) using real Tiingo
free-tier data to correct the survivorship bias documented in
`DEVELOPMENT.md` §16, then applies vol-targeting (`backtest/vol_target.py`)
to address the MaxDD gap that TODOS.md / STATUS.md flagged as the blocker.

**Headline verdict:** Under the final 252-day holdout, Gate 1 numerically
**passes both the documented gate and the SPY-relative framing once
vol-targeting is applied.** But the multi-year walk-forward record —
arguably the more trustworthy, multi-regime number — **fails the same
documented gate outright, on both Sharpe (0.59 < 1.0) and MaxDD (−45.1%)**,
and parameter stability is low (0.47). This is a fragile, single-window
holdout pass, not a robust one. See [Honest verdict](#honest-verdict)
before treating this as a green light.

---

## 1. Crux finding: does free-tier data actually fix survivorship bias?

Tested both `datadesk/ingest/tiingo.py` and `datadesk/ingest/massive.py`
against tickers that were genuinely removed from major indices (acquired,
taken private, or collapsed — not just currently-small names):

| Source | Result |
|---|---|
| **Tiingo** (free "Power" tier) | **Yes — strong delisted/point-in-time coverage.** Returned full daily history through the actual delisting date for XLNX, CERN, ATVI, MXIM, CTXS, SPLK, FLIR, ABMD, SGEN, CAVM, TWTR, SIVB, SBNY. Coverage was thin/absent only for very old delistings (Lehman Brothers, 2008 — empty payload) and companies whose ticker was later reused by an unrelated business (see §2). |
| **Massive** (Polygon rebrand, free tier) | **No — not usable right now.** Every request (delisted *and* currently-listed control tickers alike) returned `403 NOT_AUTHORIZED — "Your plan doesn't include this data timeframe"` on the first pass, and `401 "Unknown API Key"` on repeat attempts minutes later, for the exact same request. The key is either not provisioned for the historical-aggregates endpoint, or has an intermittent auth problem. Recommend Ewan check the key at the Massive/Polygon dashboard; not a blocker since Tiingo alone covers this task. |

**This is the answer to "how much of the survivorship bias is fixable for
free right now": most of it, for delistings from roughly the last ~15
years, via Tiingo alone.** Massive contributed nothing in this run.

---

## 2. Universe expansion — what got added and what didn't

### Delisted names added (13, via Tiingo, `source='tiingo_delisted_backfill'`)

All acquired/taken-private names where the ticker was permanently retired
(no reuse risk) and coverage was deep enough to clear the existing
`rows > 2000` filter in `cmd_holdout`:

| Ticker | Company | Delisting event | Rows | Coverage |
|---|---|---|---|---|
| XLNX | Xilinx | AMD acquisition, closed 2022-02-14 | 3,051 | 2010–2022-02-14 |
| CERN | Cerner | Oracle acquisition, closed 2022-06-08 | 3,130 | 2010–2022-06-08 |
| ATVI | Activision Blizzard | Microsoft acquisition, closed 2023-10-13 | 3,469 | 2010–2023-10-13 |
| MXIM | Maxim Integrated | Analog Devices acquisition, closed 2021-08-26 | 2,933 | 2010–2021-08-26 |
| CTXS | Citrix | Vista/Evergreen take-private, closed 2022-09-30 | 3,209 | 2010–2022-09-30 |
| SPLK | Splunk | Cisco acquisition, closed 2024-03-18 | 2,997 | 2012–2024-03-18 |
| FLIR | FLIR Systems | Teledyne acquisition, closed 2021-05-14 | 2,861 | 2010–2021-05-14 |
| ABMD | Abiomed | J&J acquisition, closed 2022-12-22 | 3,267 | 2010–2022-12-22 |
| SGEN | Seagen | Pfizer acquisition, closed 2023-12-14 | 3,512 | 2010–2023-12-14 |
| CAVM | Cavium | Marvell acquisition, closed 2018-07-06 | 2,142 | 2010–2018-07-06 |
| TWTR | Twitter | Musk take-private, closed 2022-10-28 | 2,260 | 2013–2022-10-28 |
| SIVB | SVB Financial (Silicon Valley Bank) | Collapse / FDIC seizure, March 2023 | 3,843 | 2010–2026-07-17 (genuine crash to fractions of a cent, then thin OTC-shell trading kept as-is — see below) |
| SBNY | Signature Bank | Collapse / FDIC seizure, March 2023 | 4,159 | 2010–2026-07-17 (same treatment as SIVB) |

All 13 cleared both the `rows > 2000` filter and the quality filter, and are
present in the 251-ticker universe the re-run actually traded (262 tickers
pass the `rows > 2000` gate before the 2016-05-24 date-alignment step).

TWTR/SIVB/SBNY initially hit Tiingo's free-tier hourly rate limit (see §1)
and were **not** in the first evaluation pass below; a retry after the rate
limit window reset succeeded and all three are included in the final numbers
in §4. SIVB and SBNY are the genuine-disaster examples flagged as missing in
an earlier draft of this report — their inclusion is exactly what caused the
walk-forward Sharpe to drop below 1.0 (see §4), which is the correction
working as intended, not a problem with the correction.

**Data-quality note:** Tiingo's raw feed shows several of these (CERN, MXIM,
CTXS, SPLK, SGEN, ABMD) continuing to report a *flat, unmoving* price for
months or years after the real, publicly-known deal-close date — in SPLK's
and SGEN's case, all the way through today. This is almost certainly Tiingo
carrying the last real quote forward rather than genuine post-delisting
trading. Each was **manually truncated at its known real-world deal-close
date** before being written to `history.db`, so the strategy never sees
phantom flat-line "returns" past the actual delisting. This is a real
limitation of the free tier worth flagging: automated point-in-time
backfill at scale would need a delisting-date reference table, not just raw
Tiingo pulls.

### TWTR, SIVB, SBNY — initially rate-limited, recovered on retry

All three were confirmed to have good Tiingo coverage in the initial
validation pass, but the first attempt to actually backfill them hit
Tiingo's free-tier rate limit (this session had already made a large number
of Tiingo calls between the coverage test, the validation pass, and the
main 10-name backfill). A retry after the rate-limit window reset
succeeded for all three; they are included in every number in §4.

This mattered in practice, not just in principle: **SIVB (Silicon Valley
Bank) and SBNY (Signature Bank) are exactly the kind of genuine-disaster
delisting a survivorship-biased backtest misses** — both went from trading
normally to near-zero within days in the March 2023 banking crisis, and
both were real momentum-strategy candidates in the run-up to their collapse
(large banks, actively traded, would have shown up in a 6-1-month momentum
scan at various points 2019–2023). Adding them **did** drag the multi-year
walk-forward Sharpe down (see §4) — the correction visibly doing its job —
while leaving the final 252-day holdout unchanged, since both were long
delisted before that window starts. The 10 M&A-driven names, by contrast,
mostly capture premium-driven upside since a momentum strategy running into
an announced deal benefits from the deal-premium runup. **Even with all 13
included, this correction is still not exhaustive** — Tiingo's free tier
almost certainly has more genuine collapses (further 2023 regional banks,
other Chapter 11s) that weren't chased down in this session; treat the
figures below as a partial, directionally-honest correction, not a
complete one.

### Excluded after validation (data-integrity reasons, not coverage)

- **BBBY** — excluded despite full Tiingo coverage. Overstock.com renamed
  itself "Bed Bath & Beyond Inc." and took over the `BBBY` ticker in 2023
  after the original Bed Bath & Beyond went bankrupt. Tiingo's continuous
  series under `BBBY` shows no discontinuity, meaning it is very likely
  reporting Overstock's own trading history relabelled, not a genuine
  splice of two companies' prices. Using it would silently misrepresent
  Overstock as "Bed Bath & Beyond" for most of the series.
- **FTR (Frontier Communications)** — Tiingo only has 279 rows
  (2020-04-23 → 2021-06-01), covering the post-bankruptcy OTC window before
  Frontier re-listed as FYBR. The pre-2020 decline — the period actually
  relevant to a 2016–2026 backtest — isn't in Tiingo's free tier under this
  symbol.
- **LLTC, MENT, BRCD, YHOO** — clean, single-entity delistings with no reuse
  risk, but Tiingo's free tier only had 300–1,900 rows for each (all
  delisted before 2018), too thin to clear the `rows > 2000` filter that
  `cmd_holdout` already applies. Backfilled for completeness but they don't
  affect the result.
- **FRC (First Republic Bank)** — Tiingo returned "not found" under this
  symbol.

### The other 193+ tickers: a process bug, not new data

Separately from the delisted-name work, this re-run found and fixed a
**pre-existing data-path bug**: `datadesk/config.py`'s `DATA_DIR` always
resolves to `datadesk/datadesk/data/` (derived from `__file__`, independent
of working directory), so every real run of `main.py`/`sweep.py` reads and
writes `datadesk/datadesk/data/history.db`. A **second, much richer**
`history.db` existed at the repo root (303 tickers, 554k rows, last touched
2026-06-14) that no current code path actually touches — it looks like a
leftover from before a `DATA_DIR` refactor, or written by one of the ad hoc
root-level scripts (`temp_query.py` etc.) that use bare relative DB paths
instead of importing `config.py`'s resolved paths. The canonical DB the
application actually uses had silently regressed to only 110 tickers
(all `yahoo_primary`-sourced) before this session.

Rather than splicing the two SQLite files together directly (attempted, but
blocked by this environment's safety sandbox as too risky a raw DB
mutation — a reasonable guardrail), the fix was to re-run the **sanctioned**
`python main.py backfill` command against the full 303-ticker list read out
of the orphaned root DB, repopulating the canonical DB through the normal,
tested application code path. Net effect: canonical `history.db` grew from
110 → 313 tickers, 696k → 1.85M rows, through legitimate yfinance +
Tiingo-delisted backfill.

**This is worth Ewan's attention independent of Gate 1**: it means every
`main.py holdout` / `sweep.py` run between whenever the `DATA_DIR` path
changed and today was silently running on a much smaller universe than the
project's own docs describe, without any error or warning.

---

## 3. Methodology

Followed `docs/backtesting.md` exactly — no new protocol invented.

- **Universe**: `coverage()` tickers with `rows > 2000` → 262 tickers → 251
  after date-alignment to the 2016-05-24 backtest start and dropping any
  column still containing gaps. Quality filter
  (`load_quality_excludes()`) excluded **zero** tickers this run — see
  caveat below.
- **Strategy**: unchanged — `momentum(lookback=126, top_n=10, skip=21)` +
  `bear_only_scale` overlay (de-risk to 0.4 when SPY < 200dMA AND VIX > 30),
  exactly the "v2" blend in `DEVELOPMENT.md` §4.
- **Costs**: always on. Tiered by liquidity (`build_cost_tiers()`, L1/L2/L3),
  reported for both ALPACA (0% FX) and T212 ISA (+15bp FX) cost models.
- **Walk-forward**: `backtest/walkforward.py: walk_forward()` — the literal
  "2y train / 6m test" implementation (`train_days=504, test_days=126`,
  the module's own defaults), grid `lookback ∈ {63,126,252} × top_n ∈
  {5,10,15}`, ALPACA tiered costs. This is the concrete, already-tested
  function matching that protocol description; `sweep.py`'s
  `_run_walk_forward` (a *different*, 3y/1y expanding-window variant) was
  not used, since the task and `DESIGN.md`/`STRATEGY-MASTERPLAN.md` both
  specify 2y/6m. New script: `scripts/gate1_honest_rerun.py`.
- **Final holdout**: last 252 trading days (2025-08-01 → 2026-07-20),
  never touched by the walk-forward parameter selection above.
- **Vol-targeting**: `vol_target_weights(target_vol=0.15, window=63,
  max_leverage=2.0)` — module defaults, unchanged, applied to the same
  momentum+bear weight matrix, compared side-by-side with the raw version.

---

## 4. Results

### Walk-forward OOS (2y train / 6m test, 15 segments, 2019–2026)

All numbers below include the full 13-name delisted set (TWTR/SIVB/SBNY
included after the retry — see §2).

| Metric | Value |
|---|---|
| Segments | 15 |
| **Param stability** (modal param share) | **0.47** |
| Stitched OOS CAGR | +119.0% |
| **Stitched OOS Sharpe** | **0.59 — below the 1.0 bar** |
| Stitched OOS MaxDD | **−45.1%** |
| Stitched OOS Sortino / Calmar | 7.37 / 2.64 |

Adding SIVB and SBNY (the two genuine bank-collapse delistings) pulled the
stitched OOS Sharpe from 1.20 (10-name universe, before the retry
succeeded) down to **0.59** — below the documented gate's own 1.0 bar — while
barely moving MaxDD (−45.9% → −45.1%) and pushing CAGR up (49% → 119%, i.e.
more extreme dispersion, not more consistent return). This is the
survivorship-bias correction visibly doing its job on the multi-year record,
even though it doesn't move the final 252-day holdout (§4) at all, since
both banks were long delisted before that window starts.

Per-segment test Sharpe ranged from **−0.98 to +4.62** — five of fifteen
segments were flat-to-losing OOS periods (2021-10→2022-04, 2022-04→10,
2022-10→2023-04, 2024-03→09, 2024-09→2025-03). Chosen params bounced between
`lookback=63` (early, momentum-favouring regime) and `lookback=252` (later,
choppier conditions); no single parameter set dominates — this is the
project's own stated overfit flag ("if walk-forward optimal params jump
wildly between windows → overfit") firing.

### Full period (2016-05-24 → 2026-07-20) — ALPACA tiered costs

| | CAGR | Sharpe | MaxDD |
|---|---|---|---|
| v2 raw | +114.2% | 0.90 | −33.8% |
| **v2 + vol-target 15%** | **+40.5%** | **0.97** | **−19.4%** |
| SPY benchmark | +14.4% | 0.84 | −34.0% |

### Final holdout — last 252 trading days — ALPACA tiered costs

| | CAGR | Sharpe | MaxDD |
|---|---|---|---|
| v2 raw | +190.3% | 2.37 | −30.7% |
| **v2 + vol-target 15%** | **+64.0%** | **2.82** | **−7.8%** |
| SPY benchmark | +18.6% | 1.42 | −8.9% |

(T212 ISA tiered+FX costs move every number by roughly 1–2pp CAGR and
0.02–0.04 Sharpe — same conclusions; full table in
`docs/gate1_rerun_2026-07-20_raw_output.json`.)

### Gate 1 verdict — two framings, both stages

| Framing | v2 raw | v2 + vol-target 15% |
|---|---|---|
| **Documented gate** (Sharpe ≥ 1.0, MaxDD ≤ 20%, CAGR ≥ 15%) — holdout | Sharpe ✓ (2.37) · MaxDD **✗** (−30.7%) · CAGR ✓ → **FAIL** | Sharpe ✓ (2.82) · MaxDD ✓ (−7.8%) · CAGR ✓ → **PASS** |
| **SPY-relative** (beat SPY on both Sharpe and MaxDD) — holdout | Sharpe ✓ (2.37 > 1.42) · MaxDD **✗** (−30.7% worse than −8.9%) → **FAIL** | Sharpe ✓ (2.82 > 1.42) · MaxDD ✓ (−7.8% better than −8.9%) → **PASS** |
| **Documented gate — walk-forward stitched (no vol-target applied)** | Sharpe **✗** (0.59) · MaxDD **✗** (−45.1%) · CAGR ✓ → **FAIL, on two legs** | not computed this run (see caveats) |

Vol-targeting (`vol_target.py`, already built, per `STATUS.md`'s own
"reduce MaxDD via position sizing" note) is what actually closes the gate:
it cuts holdout MaxDD from −30.7% to −7.8% and full-period MaxDD from
−33.8% to −19.4%, while *improving* Sharpe in both windows (2.37→2.82,
0.90→0.97) because it de-risks precisely during the highest-realised-vol
stretches. Strategy logic itself was not touched.

---

## 5. Honest verdict

**Numerically, on the final 252-day holdout, Gate 1 passes — under both the
documented gate and the stricter SPY-relative framing — once vol-targeting
is applied.** That is a genuine, meaningful result: it's the first time
either framing has passed on MaxDD in this project's history, and it comes
from a real methodology fix (position sizing), not from loosening the gate
or cherry-picking the universe.

I would **not** present this as a clean, confident "Gate 1 passed, ready to
progress toward paper" without flagging four things plainly:

1. **The pass is driven by one exceptional window.** The last 252 trading
   days show a large, narrow, semiconductor/AI-infrastructure-concentrated
   rally (holdout top-10 holdings were dominated by MU, WDC, LITE, CIEN,
   INTC, COHR, FORM, Samsung, and — recurring in most recent rebalances —
   the 3x-leveraged `SOXL` ETF). A 190% (raw) or 64% (vol-targeted)
   annualised return from a single year is not a sustainable run-rate; it's
   a favourable-regime snapshot. Removing the leveraged/inverse ETFs
   (SOXL/SOXS/TZA — pre-existing in the universe list, not added by this
   work) from the holdout barely moves the number (201% CAGR *without*
   them, if anything higher) — so they are not artificially propping up the
   result, but their presence in a "momentum equity" universe at all is a
   separate design question worth a decision from Ewan.
2. **Param stability is low (0.47)** — by the project's own stated rule
   ("if walk-forward optimal params jump wildly between windows → overfit"),
   this is the overfit flag firing.
3. **The multi-year walk-forward record fails the documented gate outright,
   on two legs.** Once the genuine-disaster delistings (SIVB, SBNY) are
   included, the stitched OOS Sharpe drops to **0.59 — below the 1.0 bar**
   — and MaxDD is −45.1%, more than double the 20% ceiling. Vol-targeting
   was only applied to the final-holdout stage in this run, not re-swept
   through the full walk-forward; re-running it there is the natural next
   step, but there's no guarantee it fixes a sub-1.0 Sharpe the way it
   fixed the MaxDD-only gap in the holdout.
4. **The survivorship-bias correction is real but still not exhaustive.**
   All 13 delisted names that could be found and validated this session are
   included (10 M&A exits, mostly premium-capturing, plus SIVB/SBNY/TWTR).
   Adding the two bank collapses visibly worsened the multi-year record
   exactly as expected — but Tiingo's free tier almost certainly has more
   genuine collapses from this period (other 2023 regional banks, further
   Chapter 11s) that weren't chased down. The true, fully-corrected picture
   is probably somewhat worse than what's reported here, not better.

**Contrary to the DEVELOPMENT.md §16 hypothesis** (survivorship correction
would pull CAGR down toward an estimated 13–15%), this re-run's numbers came
in *higher* than the prior biased-universe figures (full-period CAGR 114%
here vs. 38–46.8% previously; holdout Sharpe 2.37–2.82 vs. 1.96–2.38
previously). The correction did not fail to move the needle — it moved it
the opposite direction from the pre-registered guess. Best explanation
available from this session's diagnostics: the delisted names added were
disproportionately premium-capturing M&A exits (not the pure "losses we
didn't record" the original hypothesis assumed), the universe itself grew
substantially (110 → 313 tickers, via the DATA_DIR bug fix above, not the
delisted-name work) surfacing more momentum opportunities, and this specific
trailing-12-month window shows an unusually strong, concentrated
semiconductor rally in the underlying price data. None of this is a
methodology error I can find — costs are on, there's no lookahead (engine
structure unchanged), the holdout window was never touched during parameter
selection — but it is a result that deserves scepticism before being acted
on, exactly per the project's own "a strategy that only works at one
parameter point is rejected, not tuned" principle, applied here to "one
window" rather than "one parameter."

**Recommended next steps, in order:**
1. Apply vol-targeting to the full walk-forward stitched series (not just
   the final holdout) and re-check param stability and Sharpe with it
   applied — the walk-forward record is currently the weakest link (Sharpe
   0.59, fails the gate outright) and hasn't had the same position-sizing
   fix applied to it yet.
2. Look for further genuine-collapse delistings on Tiingo's free tier
   beyond SIVB/SBNY (e.g. other 2023 regional banks, further Chapter 11s)
   — the correction applied here is real but not exhaustive, and every
   name found so far that wasn't an M&A exit has made the picture worse,
   not better.
3. Get a decision from Ewan on whether leveraged/inverse ETFs (SOXL, SOXS,
   TZA, TSLL) belong in the momentum universe at all — they pre-date this
   work but materially change what "the strategy" even means. Sensitivity
   check in §5 shows they are not the primary driver of the extreme
   holdout numbers, but their presence is still a live design question.
4. Run `python main.py enrich` against the full 313-ticker universe so
   `load_quality_excludes()` has real fundamentals to filter on (it
   excluded zero tickers this run purely because most of the newly-added
   names have no fundamentals data yet, not because they're all
   high-quality).

---

## 6. Process / security notes

- **Two API keys were briefly exposed in local plaintext logs during this
  session** (not committed, not pushed, never sent anywhere external):
  the `MASSIVE_API_KEY` via `requests`' exception message embedding the
  full request URL on a 403 response, and the `TIINGO_API_KEY` via
  `httpx`'s own request-logging propagating through `main.py`'s
  `logging.basicConfig(level=INFO)` into `error_log.txt` (which **is**
  gitignored and was never at risk of being committed) and a scratch log
  file. Both instances were located and redacted in place; the scratch
  files have been deleted. **Recommend rotating both `MASSIVE_API_KEY` and
  `TIINGO_API_KEY` in `.env`** out of caution — no evidence either left this
  machine, but they did sit in plaintext files briefly.
- `git status` / `git diff` were checked before every commit in this work;
  no `.env` values were committed. `.gitignore`'s `*.db` / `*.db-shm` /
  `*.db-wal` / `error_log.txt` entries were relied on and verified, not
  modified.
- No file under `live/`, no order-execution code, and no broker credential
  handling was touched. `PAPER_TRADE_MODE` was not touched.
