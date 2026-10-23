# R6 Report — Verifiable probability models

Stage R6 of the Pythia master roadmap. Baseline: accepted R5
`e05ca6c`; branch `codex/probability-r6-models`. Production (`ee6460f`)
and the paper pilot (`c21967f`) untouched. Everything here remains
`usable_for_forecast=false`.

## What was built

1. **`python/evaluation/probability_model.py`** — frozen model
   `pythia-probability/1.0`. For the nearest FOMC meeting it solves
   the post-meeting rate from the meeting-month ZQ contract with the
   CME day-weighting already in `zq_contracts`, takes the expected
   move against the pre-meeting target-range midpoint, and spreads it
   over the fixed grid {≤−50, −25, 0, +25, ≥+50} bp by linear
   interpolation (edge buckets absorb the tails). The following
   month's contract is recorded as a diagnostic only — it prices two
   meetings combined and does not identify the nearest one; the plan's
   "two-contract tree" wording is corrected here to that honest
   description. The model refuses (never fabricates) on look-ahead,
   stale (>3 days) or missing closes, and on last-day-of-month
   meetings.
2. **`python/evaluation/score_forecasts.py`** — an independent
   scorer: Brier + log loss for the model and the recorded market
   baseline, plus an audit that rejects records whose input close
   postdates the horizon or whose timestamp is not before the
   meeting. It shares no logic with the model beyond the grid.
3. **Forecast records** (`pythia-forecast/1`): event, question,
   horizon, timestamps, all inputs, diagnostics, the distribution,
   and the market baseline at the same instant.
4. **CPI / GDP / AI cards** (R6_CARDS.md): frozen specifications
   with explicit verdicts — CPI and GDP are baseline-only (the
   Cleveland Fed nowcast and Atlanta GDPNow are point estimates
   without a validated error distribution; both are recorded as
   diagnostics), AI events are observation-only (no mechanical
   model exists).

## The reconstruction (one resolved meeting)

Meeting 2026-09-16; the Committee **raised** the range by 25bp to
3.75–4.00 (statement archived in the R4 corpus; pre-meeting range
3.50–3.75 from the 2026-07-29 statement, midpoint 3.625). Inputs:
the committed ZQ fixture (the expired contract's live Yahoo history
no longer exists — verified 2026-10-23 — so the fixture is the
archive) and the tracker's daily snapshots for the market baseline.
No audit violations at any horizon.

| Horizon | Model Brier | Baseline Brier | Model log loss | Baseline log loss |
|---|---|---|---|---|
| T-7d | 0.2551 | 0.4164 | 0.4418 | 0.6360 |
| T-3d | 0.0123 | 0.0991 | 0.0818 | 0.2749 |
| T-1d | 0.0026 | 0.0351 | 0.0364 | 0.1574 |

On this single event the futures-implied distribution scored better
than the prediction-market buckets at every horizon, and far better
than a naive all-hold forecast (Brier 2.0). **This validates the
machinery — records, horizons, audit, scoring — not an edge.** One
meeting cannot carry an edge claim, per the roadmap's own warning;
the prospective record starts with the 2026-10-28 meeting (live
forecasts to be recorded at T-3d on 2026-10-25 and T-1d on
2026-10-27) and accumulates from there into R9.

An honest note on the incumbent: the old binary `move_probs` gives
*identical* distributions to the new model whenever the expected move
lies within ±25bp (its clamp is the same interpolation on the inner
segment). The new model's gain is real but specific: correct mass in
the tails (moves beyond one step), a full distribution to score, and
records an independent scorer can audit.

## Acceptance mapping

- Model + scorer frozen and versioned; unit tests cover the
  distribution math, the refusals and the audit — the full suite is
  green (302 tests: 290 previous + 12 new).
- Sep-2026 reconstruction from archived inputs only; the scorer
  independently reproduced Brier / log loss for model and market at
  all fixed horizons; input timestamps audited (no violations).
- CPI/GDP/AI cards written with explicit data-sufficiency verdicts.
- No edge claim made; production and the paper pilot untouched;
  all records carry `usable_for_forecast=false`.
