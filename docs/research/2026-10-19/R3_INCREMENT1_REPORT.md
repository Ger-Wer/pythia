# Pythia R3.1: Offline Official Fed Statements Extractor Report

**Date:** 2026-10-19  
**Execution Environment:** `amper` (Ubuntu 24.04, Linux 6.8.0-45-generic x86_64)  
**Worktree:** `/home/pythia/pythia-evidence-r31`  
**Branch:** `codex/evidence-r31-statements`  
**Base Commit:** `395d288a0ecb23829b0232debb29865a00e6ef38` (from `codex/evidence-r3-extraction` after R3.0.2)  
**Python Runtime:** `/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3` (Python 3.12.3)

---

## 1. Executive Summary

Increment R3.1 implements a pure offline, deterministic parser and research bundler for official Federal Reserve FOMC statements (`python/evaluation/fed_statement_extract.py`).

Key achievements in R3.1:
1. **Document Profile `fomc_press_release_v1`**:
   - Validates official FOMC press release layout (`#article`, `.heading`, `.title`, `p.article__time`, `div.col-xs-12.col-sm-8.col-md-8`).
   - Discards boilerplate navigation, headers, footers, social share widgets, and `<meta>` duplicate text.
   - Detects and rejects unsupported document types (minutes, projections, speeches, discount rate, task force) with explicit typed rejection reasons.
   - Detects and rejects bot challenge / JavaScript barriers (`blocked_or_challenge`).
2. **ProofSpan Fidelity & Verbatim Exactness**:
   - 100% of substantive statement text is mapped to exact raw byte spans (`ProofSpan`: `byte_start`, `byte_end`, `byte_length`, `sha256`) pointing directly into the immutable raw HTML blob.
   - Slices container element, date, title, release time, and every substantive paragraph.
   - Zero-hallucination guarantee: every proof span is cryptographically verified against the raw fixture before bundle publication.
3. **Strict Date & Timezone Provenance**:
   - Parses official calendar date into ISO `YYYY-MM-DD` (`published_date`) with precision `day`.
   - Release time string (e.g. `For release at 2:00 p.m. EDT`) is parsed into timezone-aware UTC offset (e.g. `2026-09-16T14:00:00-04:00`) only when explicit timezone is present.
   - Zoneless or non-timestamped release strings leave `published_at = None` with precision `day`, preventing false precision.
4. **Deterministic Research Bundles & Previews**:
   - Research bundle format: `statement_bundle_{digest}.json` written atomically via temp-file + hardlink (`os.link`), with `FileExistsError` collision detection (idempotent on identical content, error on conflicting digest).
   - Bundles carry complete provenance back to the parent feed receipt, revision, blob SHA, and XPath locator.
   - Human-readable markdown preview generation (`generate_statement_preview`).
5. **Zero Network & Safety Invariants**:
   - Zero live network requests occurred during test execution or extraction runs (`socket.socket.connect` guarded).
   - All 32 CAS blobs across all 4 live production archives (`live-r2-run1`, `live-r2-revision1`, `live-r2-revision2`, `live-r2-revision3`) were cryptographically verified before and after demo execution and remain 100% intact.
   - Baseline commit `ee6460f`, docker container `pythia-v2`, and R2 collector registry (8 canonical sources) remain untouched.

---

## 2. Research Fixture Acquisition Log

Under the R3.1 protocol, a one-time bounded research acquisition on `amper` was permitted solely for the two specific statement URLs referenced by the canonical Fed Monetary feed facts in the R3.0 demo.

### Acquisition Parameters:
- **Timestamp:** 2026-10-19T07:47:00Z
- **Network Policy:** Verifying HTTPS, no credentials, <=2 MiB, 20s timeout, max 2 redirects.
- **Collector Policy:** Research fixture only (`provenance_class="research_fixture"`, `usable_for_forecast=false`). No changes to production allowlist or collector loop.

### Fixtures Stored:
1. **Fixture 1: September 16, 2026 Statement (`monetary20260916a`)**:
   - **Source URL:** `https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm`
   - **HTTP Status:** 200 OK (Content-Type: `text/html; charset=utf-8`)
   - **Size:** 84,741 bytes
   - **SHA-256:** `8252bf02066af1c379bdcad3b9b70cc8cdbf8e21183eb3f00c8df0a2dba9391f`
   - **Parent RSS Fact:** `fact-b7cdc057cc6ec28d`
   - **Receipt ID:** `39bf5ee5e5dbbb74` (live-r2-run1, seq 3)
   - **Parent Feed Blob:** `69b63803023023ebcba8973b75c879d749964467d5cb1017efad8b0ae423b44b`
   - **RSS Item Locator:** `/rss/channel/item[3]`

2. **Fixture 2: July 29, 2026 Statement (`monetary20260729a`)**:
   - **Source URL:** `https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm`
   - **HTTP Status:** 200 OK (Content-Type: `text/html; charset=utf-8`)
   - **Size:** 83,318 bytes
   - **SHA-256:** `f3cfa831daa2e63b0c370bd0de3b58cccc30439ee3e8378bc0a6c2dbf650ef49`
   - **Parent RSS Fact:** `fact-23bc4c0fc0243b9e`
   - **Receipt ID:** `39bf5ee5e5dbbb74` (live-r2-run1, seq 3)
   - **Parent Feed Blob:** `69b63803023023ebcba8973b75c879d749964467d5cb1017efad8b0ae423b44b`
   - **RSS Item Locator:** `/rss/channel/item[7]`

All raw HTML and manifests are frozen in `docs/research/2026-10-19/r3/increment1/fixtures/`.

---

## 3. Architecture & Extraction Methodology

### Data Model (`python/evaluation/fed_statement_extract.py`):
```
StatementDocument
├── statement_id: str (stmt-{sha256[:16]})
├── profile: "fomc_press_release_v1"
├── source_url: str
├── parent_fact_id: str
├── fixture_blob_sha256: str
├── title: str
├── title_proof: ProofSpan
├── published_date: "YYYY-MM-DD"
├── published_date_precision: "day"
├── date_proof: ProofSpan
├── release_time_text: Optional[str]
├── release_time_proof: Optional[ProofSpan]
├── published_at: Optional[str] (ISO 8601 with offset, e.g. 2026-09-16T14:00:00-04:00)
├── published_at_provenance: Optional[str] ("header_release_time" | None)
├── container_locator: "#article div.col-xs-12.col-sm-8.col-md-8"
├── container_proof: ProofSpan
└── paragraphs: List[StatementParagraph]
    ├── paragraph_index: int
    ├── locator: str ("#article div.col-xs-12.col-sm-8.col-md-8 p:nth-of-type(k)")
    ├── text: str (clean decoded display text)
    ├── classification: str ("vote_approval" | "policy_decision" | "economic_assessment" | "inflation_mandate" | "dissenting_votes" | "implementation_note" | "media_inquiries" | "body")
    └── proof: ProofSpan
```

### Extraction Pipeline:
1. **Integrity Pre-check:** Blob size and SHA-256 match the manifest; input bytes are verified valid UTF-8.
2. **Profile Validation:** Verifies `<div id="article">`, `.heading`, `.title`, and substantive paragraph body container.
3. **Negative Rejections:**
   - Detects non-statement titles (e.g. `Minutes of the...`, `Federal Reserve Board announces approval...`).
   - Detects challenge / bot barriers (e.g. `cf-browser-verification`, `Just a moment...`).
   - Rejects missing elements (`missing_article_container`, `missing_title`, `missing_published_date`, `empty_content`).
4. **Anchor & Proof Slicing:**
   - To avoid slicing `<head>` meta tags (`meta[name="description"]` has identical prefix), search begins at `#article` byte offset.
   - Byte offsets index directly into raw `bytes` (preserving UTF-8 BOM `\xef\xbb\xbf` and multibyte entity offsets).
   - Paragraph DOM locators maintain exact 1-based `nth-of-type` positions corresponding to the raw DOM.
5. **Atomic Publication:**
   - Serializes canonical JSON (sorted keys, no extraneous whitespace).
   - Computes deterministic `bundle_digest = sha256(canonical_json)`.
   - Writes to a unique temporary file and hardlinks (`os.link`) to `statement_bundle_{digest}.json`.
   - Handles collisions: if destination exists and content is byte-identical, returns existing; if content differs, raises `StatementPublishError`.

---

## 4. Verifications & Demonstration Results

### Demonstration Run Log (`docs/research/2026-10-19/r3/increment1/run_demo.py`):
```text
======================================================================
PYTHIA R3.1 OFFLINE FOMC STATEMENT EXTRACTOR DEMONSTRATION
======================================================================

[Step 1] Verifying 32 blobs across 4 live archives BEFORE demo...
Total blobs verified: 32; Healthy: True
All 32 original CAS blobs across all 4 live archives are 100% intact.

[Step 2] Extracting and publishing real official FOMC statements...
  Extracted: monetary20260916a
    Bundle:     statement_bundle_b2991de58a918c6cf3d14015bb539b0fcb0f27bf45eee30ee9f2c874a64bf27b.json
    Digest:     b2991de58a918c6cf3d14015bb539b0fcb0f27bf45eee30ee9f2c874a64bf27b
    Title:      Federal Reserve issues FOMC statement
    Date:       2026-09-16 (precision: day)
    Published:  2026-09-16T14:00:00-04:00
    Paragraphs: 6
    Preview:    statement_preview_monetary20260916a.md
  Extracted: monetary20260729a
    Bundle:     statement_bundle_d9b46d553cf8bac213c8e0950c2cbab969aaa7421f5400c8432b08820ff3ee99.json
    Digest:     d9b46d553cf8bac213c8e0950c2cbab969aaa7421f5400c8432b08820ff3ee99
    Title:      Federal Reserve issues FOMC statement
    Date:       2026-07-29 (precision: day)
    Published:  2026-07-29T14:00:00-04:00
    Paragraphs: 7
    Preview:    statement_preview_monetary20260729a.md

[Step 3] Processing explicitly marked synthetic test fixtures...
  Synthetic: synthetic_date_only -> extracted (Digest: 2991fbb38b10336a...)
  Synthetic: synthetic_unsupported_minutes -> rejected (unsupported_document_type) (Digest: 72bd51d92975a63a...)
  Synthetic: synthetic_cf_challenge -> rejected (blocked_or_challenge) (Digest: fa7ef06cfeac9cb6...)

[Step 4] Verifying 32 blobs across 4 live archives AFTER demo...
Total blobs verified: 32; Healthy: True
All 32 original CAS blobs across all 4 live archives are 100% intact and unchanged.

[Step 5] Demo Summary written to: docs/research/2026-10-19/r3/increment1/demo_summary.json

======================================================================
R3.1 DEMONSTRATION COMPLETED SUCCESSFULLY (100% PROOFS VALID, ZERO NETWORK)
======================================================================
```

### Full Pytest Suite Run:
```text
============================= 228 passed in 13.72s =============================
```
- Total test count: 228 (213 existing + 15 new R3.1 tests in `test_fed_statement_extract.py`).
- Zero failures, zero warnings, zero network attempts.

---

## 5. Sample Extracted Statement Preview

Below is the verbatim extracted research preview for the September 16, 2026 FOMC Statement (`monetary20260916a`):

```markdown
# FOMC Statement Research Preview

- **Bundle Digest:** `b2991de58a918c6cf3d14015bb539b0fcb0f27bf45eee30ee9f2c874a64bf27b`
- **Extractor Version:** `pythia-statement-extractor-r3/0.1`
- **Provenance Class:** `research_fixture`
- **Usable For Forecast:** `False`

## Document Header
- **Statement ID:** `stmt-bf54fb45de736e7d`
- **Title:** Federal Reserve issues FOMC statement
- **Source URL:** https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm
- **Parent Feed Fact ID:** `fact-b7cdc057cc6ec28d`
- **Raw Blob SHA-256:** `8252bf02066af1c379bdcad3b9b70cc8cdbf8e21183eb3f00c8df0a2dba9391f`
- **Page Date:** `2026-09-16` (raw: `September 16, 2026`, precision: `day`)
- **Published At:** `2026-09-16T14:00:00-04:00` (provenance: `header_release_time`)
- **Release Time Text:** `For release at 2:00 p.m. EDT`

## Statement Content (6 paragraphs)

### Paragraph 0 [`vote_approval`]
> The Federal Open Market Committee approved the following statement for release by a 12 – 0 vote:

- *Locator:* `#article div.col-xs-12.col-sm-8.col-md-8 p:nth-of-type(1)`
- *Raw Byte Slice:* `[73647:73752]` (105 bytes, SHA: `ebc203933f0f...`)

### Paragraph 1 [`policy_decision`]
> The Committee decided to raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent, in support of the Federal Reserve's dual mandate. The Committee is continuing its policy of maintaining ample reserves in the banking system.

- *Locator:* `#article div.col-xs-12.col-sm-8.col-md-8 p:nth-of-type(2)`
- *Raw Byte Slice:* `[73756:74060]` (304 bytes, SHA: `ce16d2e4c5d1...`)

### Paragraph 2 [`economic_assessment`]
> Economic activity is expanding at a solid pace. While uncertainty remains elevated owing, in part, to geopolitical developments, domestic spending has been resilient. Productivity growth is strong, and capital investment is robust. Job gains have kept pace with the workforce, and the unemployment rate has changed little.

- *Locator:* `#article div.col-xs-12.col-sm-8.col-md-8 p:nth-of-type(3)`
- *Raw Byte Slice:* `[74064:74410]` (346 bytes, SHA: `423741ab9405...`)

### Paragraph 3 [`inflation_mandate`]
> Inflation remains elevated. Today's policy action will support a timelier return to the Committee's 2 percent goal. The Committee will deliver price stability.

- *Locator:* `#article div.col-xs-12.col-sm-8.col-md-8 p:nth-of-type(4)`
- *Raw Byte Slice:* `[74414:74580]` (166 bytes, SHA: `4e4a1f4ea46a...`)

### Paragraph 4 [`media_inquiries`]
> For media inquiries, please email [email protected] or call 202-452-2955.

- *Locator:* `#article div.col-xs-12.col-sm-8.col-md-8 p:nth-of-type(5)`
- *Raw Byte Slice:* `[74584:74818]` (234 bytes, SHA: `4b5c9687ad25...`)

### Paragraph 5 [`implementation_note`]
> Implementation Note issued September 16, 2026

- *Locator:* `#article div.col-xs-12.col-sm-8.col-md-8 p:nth-of-type(6)`
- *Raw Byte Slice:* `[74822:74937]` (115 bytes, SHA: `0f488c723de4...`)

## Verifications
- Container Proof: bytes `[73482:74976]`
- Title Proof: bytes `[69165:69225]`
- Date Proof: bytes `[69100:69147]`
- All proof spans independently verified against raw bytes: **VALID**
```

---

## 6. Live Archive & System Invariant Audit

| Check | Target | Expected | Actual | Status |
|---|---|---|---|---|
| Live Archive 1 Receipts | `live-r2-run1/archive.db` | 8 | 8 | PASS |
| Live Archive 1 Blobs | `live-r2-run1/blobs` | 8 | 8 | PASS |
| Live Archive Revision 1 Receipts | `live-r2-revision1/archive.db` | 8 | 8 | PASS |
| Live Archive Revision 1 Blobs | `live-r2-revision1/blobs` | 8 | 8 | PASS |
| Live Archive Revision 2 Receipts | `live-r2-revision2/archive.db` | 8 | 8 | PASS |
| Live Archive Revision 2 Blobs | `live-r2-revision2/blobs` | 8 | 8 | PASS |
| Live Archive Revision 3 Receipts | `live-r2-revision3/archive.db` | 8 | 8 | PASS |
| Live Archive Revision 3 Blobs | `live-r2-revision3/blobs` | 8 | 8 | PASS |
| Total CAS Blobs | All 4 archives | 32 | 32 | PASS |
| Docker Container `pythia-v2` | Docker state | Untouched | Untouched | PASS |
| Main Branch / Commit | `ee6460f` | Untouched | Untouched | PASS |
| Production Allowlist | `CANONICAL_SOURCES` | 8 sources | 8 sources | PASS |
| Network Requests during Extraction | Socket Guard | 0 | 0 | PASS |

---

## 7. Deliverables & File Layout

- Extractor core: `python/evaluation/fed_statement_extract.py`
- Test suite: `python/evaluation/tests/test_fed_statement_extract.py`
- Research increment files:
  - `docs/research/2026-10-19/r3/increment1/fixtures/raw_monetary20260916a.html`
  - `docs/research/2026-10-19/r3/increment1/fixtures/manifest_monetary20260916a.json`
  - `docs/research/2026-10-19/r3/increment1/fixtures/raw_monetary20260729a.html`
  - `docs/research/2026-10-19/r3/increment1/fixtures/manifest_monetary20260729a.json`
  - `docs/research/2026-10-19/r3/increment1/fixtures/fixtures_summary.json`
  - `docs/research/2026-10-19/r3/increment1/bundles/statement_bundle_b2991de58a918c6cf3d14015bb539b0fcb0f27bf45eee30ee9f2c874a64bf27b.json`
  - `docs/research/2026-10-19/r3/increment1/bundles/statement_bundle_d9b46d553cf8bac213c8e0950c2cbab969aaa7421f5400c8432b08820ff3ee99.json`
  - `docs/research/2026-10-19/r3/increment1/previews/statement_preview_monetary20260916a.md`
  - `docs/research/2026-10-19/r3/increment1/previews/statement_preview_monetary20260729a.md`
  - `docs/research/2026-10-19/r3/increment1/synthetic/` (synthetic HTMLs and manifests)
  - `docs/research/2026-10-19/r3/increment1/demo_summary.json`
  - `docs/research/2026-10-19/r3/increment1/test_run.log`
  - `docs/research/2026-10-19/r3/increment1/run_demo.py`
  - `docs/research/2026-10-19/R3_INCREMENT1_REPORT.md`
