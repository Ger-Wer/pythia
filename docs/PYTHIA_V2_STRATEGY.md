# Pythia v2 — Strategy, Audit & Roadmap

**Status:** ACTIVE — adopted direction (pre-implementation, awaiting Stage 0 result)
**Date:** 2026-07-01
**Author:** Engineering review session
**Supersedes direction of:** `docs/ORACLE_PUBLIC_CHANNEL.md`, `docs/ARCHITECTURE.md` (Pythia Capital LLM-signal channel)

> This document is the canonical record of the decision to pivot Pythia from a broad
> LLM-driven Polymarket "signal channel" to a narrow, quantitatively-grounded
> macro/economic forecasting tracker with a verifiable on-chain record. It captures the
> code audit that triggered the rethink, the market analysis, the data-feasibility study,
> the Estonia jurisdiction due diligence, and a staged go/no-go roadmap.
>
> **Nothing here is implemented yet.** The current production code (Pythia Capital oracle
> channel) is unchanged and still running on `amper`. See "Disposition of current code".

---

## 0. TL;DR

- The current product (AI-generated daily Polymarket "desk calls" to a Telegram channel,
  monetized via ads) is **technically fixable but strategically weak**: no real edge,
  broken track-record reporting, saturated/commoditized niche, and the most natural
  the most natural audience is geoblocked by Polymarket.
- A **viable** version exists, but it is a **different, narrower product**: a
  quantitatively-grounded tracker for **macro/economic markets** (FOMC rates, GDP, CPI)
  whose edge comes from arbitraging the gap between **professional nowcast models /
  fed funds futures** and Polymarket prices, with a **public, on-chain-verifiable track
  record** as the moat.
- Everything hinges on **one unanswered question: is there an edge?** This cannot be
  proven on history alone (too few markets). The plan is staged so the cheap experiments
  answer the existential question before any capital or audience spend.
- **Decision rule:** proceed stage-by-stage; each stage is a gate. If Stage 0 shows
  Polymarket is efficient relative to fed funds futures on FOMC markets, **stop honestly**.

---

## 1. Why this document exists — the audit that triggered the rethink

An independent expert audit (`pythia_audit_report_v2.md`) was reviewed against the **actual
code at commit `1d144e7`** (local = GitHub = `amper`, all synced) and against the **live
production database**. Method: raw-byte verification of source lines, `py_compile`,
and running the actual SQL against the real schemas (`sql/migrations/0011`, `0012`) and the
production DB on `amper`.

### 1.1 Quality of the expert audit: mixed (~5/10)

Of the expert's 6 "critical" findings:
- **2 are factually wrong** — the mechanism the expert says is "missing" is already
  implemented (dedup of same-day publications in `oracle_publisher.py:573-617`; retry/backoff
  in `send_to_channel` `oracle_publisher.py:460-477`).
- **2 are overstated in severity** (confidence-filter readability `oracle_publisher.py:141`;
  streak function `oracle_score_updater.py:360-369` is actually correct on all tested cases).
- **2 are real** (atomicity of `close_portfolio_position`; `mark_to_market` KeyError).

The expert evidently did **not run the code or check it against the DB schema**, which is
why several "criticals" describe already-implemented behavior, and why the two worst real
bugs were missed entirely.

### 1.2 Real blockers the expert MISSED (verified on production)

- **#A — `edit_telegram_post()` crashes on non-existent columns.** It `SELECT`s
  `market_prob, reasoning, key_risk`, but the real columns are `polymarket_prob`,
  `oracle_reasoning`, `oracle_key_risk`. Verified on the live DB:
  `Error: no such column: market_prob`. **Every post-resolution channel edit fails** — the
  audience never sees WIN/LOSS outcomes.
- **#B — cancelled markets (`outcome = -1`) crash the whole score-update run.**
  `get_market_outcome` can return `-1`; `update_prophecy_result` then writes
  `actual_outcome = -1`, violating `CHECK(actual_outcome IN (0,1))` →
  `IntegrityError`. The loop has no try/except, so one cancelled market **blocks all
  remaining pending prophecies** in that run.
- **AUM accounting is corrupted** (verified live): `current_aum=1060.20` while
  `cash=1102.48`, `positions_value=0.0`, with 0 open positions. The invariant
  `current_aum = cash + positions_value` is broken; the pinned channel message shows a
  wrong AUM.
- **`oracle_metrics` is empty on production** (`accuracy_* = NULL`, `total_prophecies = 0`)
  despite 35 prophecies — the metrics/track-record path has never succeeded in practice.
- **Cross-day duplicate publishing**: dedup only blocks within the same UTC day, so the same
  market is re-published daily (observed: market `540819` published 11× over 3 days).
- **Config drift**: prod `ORACLE_MIN_EDGE=0.08` vs docs `0.12`; channel content is
  joke markets ("Jesus before GTA VI"), not "reasoned trades".

### 1.3 Verdict from the audit

The pipeline *mechanically* works but does **not** deliver the stated goal — "potentially
well-reasoned, successful trades with a provable record". The single systemic root cause is
**code ↔ schema ↔ config drift**, with **zero tests** that run SQL against the live schema
(a schema-aware pytest would have caught #A and #B instantly).

**Crucial conclusion:** fixing these bugs makes the product *work*, not *win*. The bugs are
in code we intend to retire. Therefore we do **not** invest in fixing the current oracle
publisher; we redesign the core. See "Disposition of current code".

---

## 2. Market analysis — is such a channel even a good business?

Sourced web research (competitors, demand, mechanics, monetization, regulation) produced a
clear picture for the **original** product idea (broad signal channel + ad revenue,
geoblocked audience):

| Factor | Finding |
|---|---|
| Market exists & growing | ✅ Prediction markets ~$63.5B notional 2025 (~4× 2024); Polymarket+Kalshi ≈ 97.5% |
| Audience demand | ⚠️ Real but smaller than headline — ~200–480k active traders, weak retention, volume partly wash/airdrop-driven |
| Competition | 🔴 Saturated & commoditized — dozens of free/open-source bots (PolyWatch, PolyAlpha, PolyMate, …), multiple "best-of" lists |
| Geoblocked audience fit | 🔴 Polymarket geoblocks multiple countries — the natural audience cannot legally bet |
| Onboarding friction | 🔴 Wallet + USDC + Polygon, or KYC — large subscriber→bettor drop-off |
| Ad monetization | ⚠️ Viable; finance is the top Telegram niche (€15–20 CPM CIS) but needs scale (≈5–10k+ engaged subs) |
| Polymarket affiliate | ⚠️ Gated ($10k volume for 30%/180d; else $10 CPA) and excludes geoblocked CIS users |
| Regulatory wind | 🔴 Turning against paid prediction-market promotion (CFTC influencer probe; SEBI Telegram bans; RU Telegram pressure) |

**Business conclusion:** the macro story ("prediction markets are booming") is **misleading
as a thesis for a signal channel**. Product is commoditized, the best audience for us is
geoblocked, onboarding craters conversion, affiliate terms exclude that audience, and
regulation is tightening. A defensible version must: (a) target a **supported** geography,
(b) lead with a **real, on-chain-verifiable edge** in a **growing** category
(economics/macro, not politics), (c) monetize via **ads, not Polymarket conversions**.

---

## 3. The pivot — Pythia v2 product definition

| Dimension | v1 (current) | v2 (proposed) |
|---|---|---|
| Niche | Broad / all markets (often joke markets) | **Macro/economic, numeric resolution** (FOMC, GDP, CPI, jobs) |
| Edge source | LLM paraphrasing search results | **Quantitative nowcast-vs-price gap** (fed funds futures, Fed nowcasts) |
| Cadence | 5 posts/day | Few, high-quality posts (event-driven) |
| Track record | Virtual, broken/empty metrics | **Real on-chain wallet** (strongest moat), deterministic, verifiable |
| Audience geo | Geoblocked regions | English, Polymarket-supported geos; likely X/Twitter-first |
| Monetization | Ads (but no trust base) | Ads **on top of** a proven public record |

**Why this can have an edge where v1 cannot:** Polymarket's price is already an aggregate of
informed bets; an LLM paraphrase of Google cannot systematically beat it. But in
**numeric-resolution macro markets**, professional nowcast models (Atlanta Fed GDPNow,
Cleveland Fed inflation nowcast) and **fed funds futures** (ZQ) encode information that many
Polymarket participants do not price precisely. The edge is **arbitraging that gap**, not
"having opinions". This is achievable by one engineer because the heavy analytics already
exist (Fed banks publish them); we build a thin arbitrage/comparison layer.

**Honest constraint:** there are **few** such markets (dozens per series, not 50–150), so the
product is **narrow and slow** — good for a credible track record, bad for "farming degens by
volume". This trade-off is accepted deliberately.

---

## 4. Data feasibility — can we backtest the edge without look-ahead bias?

To validate edge **before** risking money we must backtest on already-resolved markets. The
critical risk is look-ahead bias (using a model value that wasn't known at the time).

| Data need | Availability | Source |
|---|---|---|
| Polymarket resolved-market catalog | ✅ | Gamma API (`gamma-api.polymarket.com`) |
| Polymarket historical prices | ✅ | CLOB `prices-history` (pass **YES asset id**, not conditionId) + Dune/Goldsky cross-check |
| Actual release values + dates | ✅ | FRED / **ALFRED** (vintage), BLS, BEA |
| **GDP point-in-time nowcast** | ✅ best-in-class | Atlanta Fed **GDPNow** vintage spreadsheet (`TrackingArchives`) |
| **Rate-decision point-in-time** | ✅ by construction | **ZQ fed funds futures** history (inherently point-in-time) + published FedWatch formula |
| **CPI point-in-time nowcast** | ⚠️ weakest link | Cleveland Fed nowcast has **no bulk vintage download** → scrape chart JSON or capture forward |
| NY Fed Staff Nowcast | ❌ | Suspended since Sept 2021 — do not rely on it |
| Market count / statistical power | 🔴 | Dozens per series → **suggestive, not conclusive**; live paper-track is the real proof |

**Single biggest obstacle:** vintage CPI nowcast data. **Mitigation:** start where both sides
are point-in-time clean.

**Cleanest starting subset = FOMC / rate-decision markets:**
1. Nowcast side = **ZQ fed funds futures** — inherently point-in-time, zero vintage exposure,
   deterministic FedWatch probability formula (published).
2. Market side = Polymarket per-meeting rate markets — best inventory, prices retrievable.
3. Resolution = unambiguous public fact, no revisions.
4. Measure: ZQ-implied P(cut) vs Polymarket P(cut) at T-minus {1,3,7} days → is Polymarket
   mispriced relative to futures, and in a systematic direction?

Then extend to **GDP** (GDPNow vintage ready-made), **CPI last** (vintage-scraping cost).

---

## 5. Jurisdiction due diligence — Estonia (NOT legal advice)

Potential Estonian residency/partnership for legal Polymarket access and a real on-chain
wallet. **This is research, not legal/tax advice — obtain an Estonian gambling+tax opinion
before funding anything.** Estonia's Gambling Act was materially tightened effective
**2026-01-01**.

| Question | Finding | Confidence |
|---|---|---|
| Is Estonia geoblocked by Polymarket? | **No** — absent from the 33-country list (unlike FR/DE/BE/PL) | High (2 secondary sources; **verify on Polymarket's own page**) |
| Access without VPN/ToS breach? | **Yes**, with genuine Estonian residency/IP | High |
| Is *player* use clearly legal? | **Unclear / untested** — likely viewed as unlicensed gambling; 2026 amendments pulled **crypto into the "bet" definition**; player liability not established | **Low — needs counsel** |
| MiCA / financial-instrument issue? | Probably not the operative lens; gambling law is | Medium |
| Personal tax friction | **High** — 22% on **gross** gains, **no loss offset** for non-MiCA platforms, per-transaction; Polymarket unlikely MiCA-registered | High (EMTA primary) |
| OÜ 0% retained-earnings benefit | Applies to **tax timing** only; does **not** cure any licensing issue | Med-High |

**Implication:** Estonia is a **workable access jurisdiction** (no geoblock), but the
**tax model must be designed with an Estonian advisor** — gross-gain taxation without loss
offset is brutal for active trading. The narrow macro niche (few, deliberate trades) mitigates
this. Two must-dos before capital: (1) confirm Estonia's absence on Polymarket's own
geo-restrictions page; (2) written counsel opinion on player liability post-2026 and on
gambling-winnings-vs-crypto-gains tax treatment.

---

## 6. Roadmap — staged go/no-go

Each stage is a **gate**. Do not proceed without passing the prior one.

### Stage 0 — Backtest feasibility & edge signal (1–2 weeks, cheap)
- Build a minimal backtest engine in this repo (new code, not a fix of the old publisher).
- Subset: **FOMC / rate markets**, ZQ futures vs Polymarket price at T-minus {1,3,7} days.
- Output: does Polymarket deviate from futures-implied probabilities systematically?
- **Gate:** if Polymarket is efficient relative to ZQ → **STOP honestly**. If a stable
  mispricing exists → hypothesis lives, continue.
- ⚠️ Backtest is **hypothesis filter, not capital green-light** (N too small for p<0.01).

### Stage 1 — Live paper track (2–3 months)
- Model publishes forecasts on **future** FOMC/economic events, deterministically, with
  pre-resolution timestamps. Accumulate honest Brier on fresh, bias-free data.
- **Gate:** model beats market Brier on live events → continue. Else stop/iterate.

### Stage 2 — Real on-chain wallet (only after Stage 1 passes)
- Resolve Estonia legal/tax structure with counsel first.
- Fund a real Polymarket wallet (supported geo) with small capital; bot trades its own
  signals. Public wallet address = unfakeable track record.

### Stage 3 — Audience & ads (only on top of a proven public record)
- English content, Polymarket-supported geos, likely X/Twitter-first.
- Monetize via finance-niche advertising once volume + trust exist.

---

## 7. Disposition of current code

- **Do NOT fix** the v1 oracle publisher bugs (#A/#B/AUM/dedup). That code implements the
  LLM-paraphrase approach we are retiring; fixing it polishes what we discard.
- The TypeScript projection pipeline (`src/`: sector_selector → research_runner → naked_r1 →
  ensemble → synthesis) is **removed from the probability-source role**. An LLM may remain only
  as a **post-text generator**, never as the probability source.
- Production v1 (oracle channel on `amper`) stays running untouched for now; no destructive
  Docker/DB ops on `amper`. Decommissioning happens only on explicit instruction, after v2
  has a working track record.
- New v2 code (backtest engine first) lives in this repo (`~/Desktop/Pythia`), under Git, so
  any engineer who clones the project finds it alongside this document.

---

## 8. Open questions / must-resolve before capital

1. Estonia: written legal opinion on player liability (post-2026 Gambling Act) + tax fork
   (gambling winnings vs crypto property gains).
2. Confirm Estonia absent on Polymarket's own geo-restrictions page (primary source).
3. Stage 0 result: is there a systematic Polymarket↔ZQ gap on FOMC markets?
4. CPI vintage-nowcast capture strategy (scrape vs forward-capture) — only relevant if Stage 0
   passes and we extend beyond FOMC/GDP.

---

## 9. References

- Code audit basis: commit `1d144e7`; live DB on `amper:/home/pythia/pythia/data/pythia.db`.
- Expert audit reviewed: `pythia_audit_report_v2.md` (external).
- Data sources: Atlanta Fed GDPNow (`atlantafed.org/cqer/research/gdpnow`); Cleveland Fed
  inflation nowcasting (`clevelandfed.org`); CME FedWatch methodology
  (`cmegroup.com/articles/2023/understanding-the-cme-group-fedwatch-tool-methodology.html`)
  + CME DataMine for ZQ history; FRED/ALFRED (`fred.stlouisfed.org/docs/api/fred/`,
  `alfred.stlouisfed.org`); BLS (`bls.gov/developers`); BEA (`apps.bea.gov/api`);
  Polymarket Gamma/CLOB (`gamma-api.polymarket.com`, `clob.polymarket.com/prices-history`).
- Jurisdiction: EMTA crypto-asset taxation; NJORD on 2026 Gambling Act amendments;
  Polymarket restricted-country list (verify on Polymarket's own geo-restrictions page).

---

**Status:** ACTIVE (adopted direction) — awaiting Stage 0 result before any v2
implementation or v1 decommissioning.
