# R6 Decision and Plan — Verifiable probability models

Stage: R6 of the Pythia master roadmap. Baseline: accepted R5
`e05ca6c`. Branch: `codex/probability-r6-models`. Production (`ee6460f`)
and the paper pilot (`c21967f`) are not touched. Everything produced
here remains `usable_for_forecast=false` until a later stage says
otherwise; this stage builds and validates machinery, it does not
publish forecasts.

## What the roadmap requires

"First the FOMC constraints and a correct baseline; then separate
CPI/GDP/AI cards when the data suffices. Fixed horizons, a market
baseline, and independent follow-up evaluation." And the standing
warning: there is no magic minimum number of outcomes — one FOMC
proves no edge. This stage therefore accepts *machinery and honesty*,
not performance.

## Definitions (frozen for this stage)

- **Probability model**: a versioned, deterministic function from
  point-in-time inputs to a probability distribution over a contract's
  outcome space. Inputs must be archived snapshots with timestamps;
  a forecast at horizon H may only use inputs timestamped ≤ H.
  No look-ahead, ever; the scorer audits this.
- **Fixed horizons**: T-7d, T-3d, T-1d before the event (T-1h only
  where snapshot density honestly supports it).
- **Market baseline**: the Polymarket price of the same question at
  the same timestamp (Kalshi as cross-check where both exist).
  The model is always reported *against* the baseline, never alone.
- **Scoring**: Brier score and log loss, computed by an **independent
  scorer** (`score_forecasts.py`) from archived forecast records and
  resolution records. Model code never scores itself.
- **Forecast record**: immutable JSON: event id, contract question,
  horizon, forecast timestamp, model name+version, input snapshot
  hashes, the distribution, the baseline price at the same instant,
  and (added only by the scorer, in a separate file) the resolution.

## Part 1 — FOMC baseline, done right

The existing code has two known ceilings, both marked "ponytail" by
their authors: `fedwatch.py` clamps the nearest meeting to a binary
25bp move, and `zq_contracts.py` fixes the rate extraction (CME
day-weighting, meeting-month contract) but keeps the binary model.

R6 replaces the model, reusing the extraction:

1. `probability_model.py` (frozen version `pythia-probability/1.0`):
   outcome space for the nearest meeting {≤ −50, −25, 0, +25, ≥ +50} bp,
   derived from the meeting-month ZQ implied post-meeting rate and the
   following month's contract (two-contract tree), with explicit,
   documented interpolation assumptions. Degenerate inputs (missing
   contract, stale quote) → the model refuses (no distribution), it
   never fabricates.
2. Point-in-time reconstruction on the **resolved 2026-09-16 meeting**
   from the archived snapshot corpus (`snapshots/`, n=49 snapshots
   2026-07-30 → 2026-09-16, the corpus edge_diff_v2 already validated):
   forecasts at T-7d / T-3d / T-1d, scored against the actual decision
   (from the archived FOMC statement, R4 corpus label) and against the
   Polymarket baseline at the same instants.
3. A live point-in-time forecast for the **2026-10-28 meeting** at
   T-3d and T-1d, recorded before the meeting and left untouched —
   the first genuinely prospective record, feeding R9.

## Part 2 — CPI / GDP / AI cards

A card is a frozen specification, not a promise: exact question,
resolution source, outcome space, horizons, baseline source, model
source, and an explicit data-sufficiency verdict.

- **CPI**: `cpi_nowcast.py` already exists. Card verdict depends on
  what its inputs honestly support: a nowcast-implied distribution,
  or baseline-only (market price, no model).
- **GDP**: `gdpnow.py` (Atlanta Fed GDPNow) — same treatment.
- **AI events**: no mechanical model exists. Card = specification +
  market baseline, labeled "no model — observation only". Inventing a
  model to fill the card is explicitly out of scope.

## Acceptance criteria

1. `probability_model.py` + `score_forecasts.py` frozen and versioned;
   unit tests cover the distribution math, the refusal paths, and the
   no-look-ahead audit; full suite green.
2. Sep-2026 reconstruction produced from archived snapshots only;
   scorer independently reproduces Brier / log-loss for model and
   market at every fixed horizon; input timestamps audited ≤ horizon.
3. CPI/GDP/AI cards written, each with an explicit data-sufficiency
   verdict (model / baseline-only / observation-only).
4. The report states plainly what one resolved meeting does and does
   not prove; no edge claim is made.
5. Production and the paper pilot untouched; forecast records carry
   `usable_for_forecast=false`.

## Out of scope

Publishing forecasts, connecting to the paper cycle (R7), any live
trading implication, CPI/GDP/AI models beyond what their data
honestly supports, and re-opening accepted R1–R5 artifacts.
