# R6 Event Cards — CPI / GDP / AI

Companion to R6_DECISION_AND_PLAN.md. A card is a frozen specification:
the exact question, its resolution source, the outcome space, the fixed
horizons, the market baseline source, the model source, and an explicit
data-sufficiency verdict. A card without a defensible model says so —
inventing one to fill the slot is out of scope for this programme.

All cards inherit the R6 rules: fixed horizons T-7d / T-3d / T-1d,
point-in-time inputs only, the market baseline recorded at the same
instant, scoring by the independent scorer (Brier + log loss), and
`usable_for_forecast=false`.

## Card CPI-1 — US CPI release (monthly)

- **Question**: the bucket of the monthly CPI-U index change (m/m,
  seasonally adjusted, first print) as contracted by the prediction
  market for that release.
- **Resolution source**: Bureau of Labor Statistics release, as
  referenced by the market's own resolution rules.
- **Outcome space**: the market's buckets for that release
  (e.g. ≤0.1 / 0.2 / 0.3 / ≥0.4 percent), frozen into the forecast
  record at T-7d.
- **Baseline**: the market's bucket prices at the same instant.
- **Model source**: none yet. The Cleveland Fed inflation nowcast
  (`cpi_nowcast.py`, already in the repo) is a *point* estimate;
  turning it into bucket probabilities requires a measured error
  distribution of the nowcast against first prints, which has not
  been built or validated.
- **Verdict: BASELINE-ONLY.** The nowcast value is recorded in the
  forecast record as a diagnostic input. A model may be proposed
  only with a backtested nowcast-error distribution attached.

## Card GDP-1 — US GDP advance release (quarterly)

- **Question**: the bucket of quarterly real GDP growth (annualised,
  advance estimate) as contracted by the prediction market for that
  release.
- **Resolution source**: Bureau of Economic Analysis advance
  estimate, as referenced by the market's resolution rules.
- **Outcome space**: the market's buckets for that release, frozen
  at T-7d.
- **Baseline**: the market's bucket prices at the same instant.
- **Model source**: none yet. Atlanta Fed GDPNow (`gdpnow.py`,
  already in the repo) is a *point* estimate with the same
  unmeasured-error problem as the CPI nowcast.
- **Verdict: BASELINE-ONLY**, GDPNow value recorded as a diagnostic.
  Same condition for a future model: a backtested error
  distribution first.

## Card AI-1 — Scheduled AI events (model releases, dated announcements)

- **Question**: per event, the binary (or bucketed) outcome as
  contracted by the prediction market (e.g. "model X released by
  date Y").
- **Resolution source**: the market's own resolution rules and the
  vendor's public release.
- **Outcome space**: as contracted (usually binary).
- **Baseline**: the market price at the same instant.
- **Model source**: none exists. There is no mechanical data feed —
  no futures curve, no official nowcast — from which a defensible
  distribution could be derived at this stage.
- **Verdict: OBSERVATION-ONLY.** Records may be kept (question,
  baseline, resolution) to accumulate a scoring history; no model
  distribution is issued, and no edge is implied.

## What would change a verdict

A card moves from baseline-only to modelled when a candidate model
arrives with (1) point-in-time inputs that are archived, (2) a
backtested error/calibration measurement on resolved releases, and
(3) the same frozen-version discipline as pythia-probability/1.0.
Until then the honest deliverable is the baseline record itself:
market prices, archived at fixed horizons, scored after resolution.
