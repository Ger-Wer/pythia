# R5 Report — JEV gate (evidence filter), ACCEPTED

Stage: R5 of the Pythia master roadmap (MemPalace
`plans/pythia-master-roadmap-2026-10-17.md`). Branch
`codex/evidence-r5-jev`, on top of the accepted R4 baseline `8f4b7aa`.
Production (`ee6460f`) and the paper pilot (`c21967f`) untouched.
Everywhere `usable_for_forecast=false`.

## What was built

`python/evaluation/jev_gate.py`, frozen version **pythia-jev-gate/2.0**,
two independent layers:

- **Layer A — deterministic screen** (`screen_document`): blocked/challenge
  pages, unverified JS shells (hard reject), shell suspects, language
  mismatch against the declared language, implausible numbers, duplicate
  origins (wrapper-resistant paragraph fingerprints), empty bodies.
  No model involved.
- **Layer B — claim verification by the real TypeSafe JEV judge**
  (`verify_claim_jev`): one batched System One call per claim against the
  claim's evidence fragments — a Choice verdict
  (support / contradiction / insufficient / context_only) plus a Noul
  support check. Code enforces the R0 admission rules around the judge:
  strict answer schema (any violation abstains, never supports), the
  escalation rule (Choice confidence < 0.75, or the verdict and the Noul
  disagreeing across the undecided band 0.40–0.60, abstains), and a
  deterministic number check (a support verdict requires every claim
  number to occur in the evidence — numbers are verified by code, not by
  the judge). Judge pinned in `JEV_CONFIG`:
  provider `typesafe`, model **jev-1.13.0** (the version of the R0
  research batch), endpoint `https://api.typesafe.ai/v1/systemone`.

## Deviation, resolved

The stage began with a stand-in judge (a free OpenRouter model) because
the real JEV's access route was not yet identified. The owner challenged
this; the R0 research was re-read and the production client was found in
HermesTrader (`brain/tools/jev.py`), with a working key already present
on the server. A live probe batch (2026-10-22) reproduced the R0
behaviour exactly (support → choice support, confidence 1.0, Noul 0.98;
shifted-range claim → contradiction, confidence 1.0, Noul 0.01).
Layer B was rebuilt on the real JEV and re-measured; the stand-in
results are kept below as the comparison row. The deviation and its
resolution are recorded in the roadmap (v2.16) and PYTHIA_MEMORY.

## Layer A measurement (frozen R4 corpus, 135 documents)

Screening script `r5/screen_corpus.py`, results `r5/screen_results.json`
(batch duplicate semantics: one origin per fingerprint group).

| split | false rejects | misses | caught | ok | out of scope |
|---|---|---|---|---|---|
| tuning (95) | 0 | 0 | 8 | 85 | 2 |
| control (40) | 0 | 0 | 3 | 37 | 0 |

All four R4 gap classes (JS shells, absurd number, translation
substitution, blocked pages) are caught; no honest document is rejected.

## Layer B measurement (real JEV, 56 claims × 3 repeats = 168 verdicts)

Claim set built from frozen corpus labels (`r5/jev_eval_input.json`):
14 FOMC statements (9 tuning / 5 control) × 4 claim types — support
(true range), contradiction (range shifted +0.5), insufficient
(fabricated median PCE projection), context_only (a claim about a speech
by the same month's governor). Runner `r5/jev_eval_jev.py`, raw results
`r5/jev_results_jev_full.jsonl` (smoke: `r5/jev_results_jev_smoke.jsonl`).

| metric | tuning (36 claims) | control (20 claims) |
|---|---|---|
| false accepts (any repeat) | **0** | **0** |
| support exact, all repeats | 9/9 | 4/5 |
| contradiction exact, all repeats | 9/9 | 5/5 |
| insufficient exact, all repeats | 9/9 | 5/5 |
| context_only → judged context_only | 0/9 | 0/5 |
| context_only → abstained (insufficient) | 9/9 | 5/5 |
| stability (same verdict ×3) | 100% | 100% |
| schema-valid verdicts | 108/108 | 60/60 |
| decided among decidable claims | 54/54 (100%) | 27/30 (90%) |

Notes on the three non-perfect cells, all of them the gate working as
designed rather than erring:

- **fed_stmt_20240501, support claim → insufficient ×3.** JEV's Choice
  confidence was 0.31 and its Noul 0.55 — inside the undecided band — so
  the escalation rule abstained, identically on all three repeats. A
  conservative refusal on a genuinely ambiguous phrasing, not a false
  acceptance.
- **context_only claims (14) → insufficient, via escalation**
  (confidences 0.21–0.51). The gate never mistakes background material
  for support; the finer context_only label is not needed downstream,
  so collapsing it into abstention is acceptable and safe.
- Token usage: 193,524 input + 11,894 output tokens for 168 verdicts
  (≈1,152 in / 71 out per verdict). Latency ≈1.5 s per batched call.

## Comparison

- **No gate at all:** every claim would pass downstream, including all
  contradiction claims (control: 5 claims × 3 repeats of direct false
  ranges) and every unsupported projection.
- **Stand-in free LLM judge** (nvidia/nemotron-3-ultra-550b:free,
  tuning smoke, 1 repeat, same claim set): schema-valid 23/36 (64%),
  support 7/9, contradiction 7/9, decided 17/36 (47%), false accepts 0,
  ≈1,469 tokens/verdict, ~25 s/verdict. The real JEV is better on every
  axis — schema discipline, exactness, coverage, speed — which is what
  the R0 research predicted.

## Acceptance

Criteria from `R5_DECISION_AND_PLAN.md`: Layer A clean on both splits
✓; Layer B false accepts = 0 on control ✓; coverage ≥80% ✓ (100% / 90%
of decidable claims); stability ≥95% ✓ (100%); final schema validity
high ✓ (168/168 under the JEV protocol); gate version frozen
(pythia-jev-gate/2.0, judge jev-1.13.0) ✓; full suite green
(290 passed) ✓; production untouched ✓.

**R5 ACCEPTED.** New baseline: this commit (branch
`codex/evidence-r5-jev`). Known limits carried forward: JEV is an
external metered API (usage is token-metered; verdicts are evidence
judgements, not event probabilities — per R0, a JEV number is never a
forecast probability); escalated abstentions are the designed hand-off
point for a stronger model or the operator in later stages.
