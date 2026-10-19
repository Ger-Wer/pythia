# Pythia R3.1.4: Lineage Schema Validation and Statement Body Profile Report (Revision 4)

**Date:** 2026-10-19  
**Execution Environment:** `amper` (Ubuntu 24.04, Linux 6.8.0-45-generic x86_64)  
**Worktree:** `/home/pythia/pythia-evidence-r31`  
**Branch:** `codex/evidence-r31-statements`  
**Base Commit:** `395d288a0ecb23829b0232debb29865a00e6ef38` (`codex/evidence-r3-extraction` after R3.0.2)  
**Parent Candidate Under Review:** `d829982584c61036a9ca43dcb2135bb8a28fb747` (R3.1.3 CHANGES_REQUIRED)  
**Python Runtime:** `/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python` (Python 3.10.12)  
**Executor Verdict:** **IMPLEMENTED** (with honest **PARTIAL** on historical acquisition evidence)

---

## 1. Executive Summary

This increment (R3.1.4) resolves the confirmed residues of **F1 and F2** identified in architectural review `r313-d829982` ([`REVIEW_RU.md`](file:///Users/gerwer/Desktop/MemPalace/project/pythia/reviews/2026-10-19/r313-d829982/REVIEW_RU.md)):
1. **F1:** Enforcing typed schema validation on `parent_feed_reference` (requiring non-empty strings for IDs, valid 64-hex SHA-256 for hashes, and structural dictionaries for locators), supporting legacy receipt/revision aliases (`feed_receipt_id`/`receipt_id` and `feed_revision_id`/`revision_id`) with alias conflict rejection, and rejecting contradictory provenance flags (`downloaded_research_fixture` + `synthetic_fixture=True`) without bypassing real parent lineage checks.
2. **F2:** Defining a positive supported body profile (`fomc_press_release_v1`) requiring institutional Committee actor references and monetary policy stance / decision vocabulary, with fail-closed rejection of placeholder/unavailable pages (`"This page is temporarily unavailable. Please try again later."`) and non-policy content.

**Boundaries and Preserved Contracts:**
- **F3** comment-safe closing boundaries remain closed.
- **F4** single-entity decoding, **F5** comparable precision instant comparison, and **F6** destination directory/temp file protection are strictly preserved.
- All code modifications, test runs, independent probe validations, and bundle publications were executed exclusively on server `amper` under zero-network socket enforcement.
- Accepted development baseline remains **`395d288a0ecb23829b0232debb29865a00e6ef38`**. Entire R3 is `IN_PROGRESS`; R3.1 is not accepted; R3.2 is not opened.
- Historical acquisition evidence remains honestly marked as **`PARTIAL`** (`usable_for_forecast = False`).
- All prior revision artifacts (`initial/`, `revision1/`, `revision2/`, `revision3/`) remain preserved. All revision 4 artifacts reside in **`docs/research/2026-10-19/r3/increment1/revision4/`**.

---

## 2. Lineage Schema & Provenance Specification (F1)

### 2.1 Contract Schema Table

The extractor enforces strict structural offline validation of `parent_feed_reference`. The table below specifies the documented contract and type requirements:

| Field Name | Type | Constraints | Supported Forms | Description |
|:---|:---|:---|:---:|:---|
| `feed_fact_id` | `str` | Non-empty string | Form A, Form B | Unique feed fact identifier |
| `feed_blob_sha256` | `str` | Exactly 64 hex characters | Form A, Form B | SHA-256 digest of the feed item raw blob |
| `feed_item_locator` | `dict` | Non-empty dict; `type` (str) + `path` (str) | Form A, Form B | Structural pointer within the feed blob (e.g. `{"type":"xml_xpath","path":"/rss/channel/item[1]"}`) |
| `archive_name` | `str` | Non-empty string | Form A | Name of the parent CAS archive |
| `parent_bundle_digest` | `str` | Exactly 64 hex characters | Form A | SHA-256 digest of the parent bundle |
| `feed_receipt_id` / `receipt_id` | `str` | Non-empty string | Form B | Feed receipt identifier (aliases supported; must agree if both present) |
| `feed_revision_id` / `revision_id` | `str` | Non-empty string | Form B | Feed revision identifier (aliases supported; must agree if both present) |
| `source_id` | `str` | Non-empty string (optional) | Form B | Source identifier from original feed manifest |

### 2.2 Parent Reference Forms (Form A & Form B)

Two real reference forms are recognized as first-class valid lineage:
- **Form A (Archive Bundle Context):** Contains common fields (`feed_fact_id`, `feed_blob_sha256`, `feed_item_locator`) plus `archive_name` and `parent_bundle_digest`.
- **Form B (Receipt/Revision Context):** Contains common fields plus receipt and revision identifiers (using `feed_receipt_id`/`receipt_id` and `feed_revision_id`/`revision_id`), with optional `source_id`.

If both aliases are provided (e.g. `feed_receipt_id` and `receipt_id`), they must have identical values; contradictory aliases trigger typed rejection `status="invalid_manifest"`.

### 2.3 Strict Rejection of Non-Typed Lineage

Checking mere truthiness is replaced by individual type checking:
- Bools, ints, lists, or dicts in string/hash fields (e.g., `{"archive_name": 17, "feed_blob_sha256": True, "feed_fact_id": ["not", "a", "fact-id"]}`) are strictly rejected with `status="invalid_manifest"`.
- Malformed hex hashes (length != 64 or non-hex characters) in `feed_blob_sha256` or `parent_bundle_digest` are rejected with `status="invalid_manifest"`.
- Locators that are strings, empty dicts, or missing `type`/`path` strings are rejected with `status="invalid_manifest"`.

### 2.4 Provenance Consistency & Explicit Synthetic Fixtures

- **Contradictory Provenance Rejection:** When `research_provenance="downloaded_research_fixture"`, setting `synthetic_fixture=True` while omitting valid parent lineage is rejected with `status="invalid_manifest"` (`reason="Contradictory manifest provenance: 'downloaded_research_fixture' cannot declare synthetic_fixture=True"`). Real parent lineage cannot be bypassed using synthetic flags.
- **Explicit Synthetic Acceptance:** Honest synthetic fixtures declaring `fixture_kind="synthetic_fixture"` or `research_provenance="synthetic_fixture"` (without declaring downloaded provenance) are accepted without fabricating fake archive hashes or feed IDs. Bundle metadata explicitly records `provenance_class="synthetic_fixture"`, `acquisition_verification_status="NOT_APPLICABLE_SYNTHETIC"`, and notes indicating offline testing.

---

## 3. Supported Statement Body Profile Specification (F2)

### 3.1 Supported Profile: `fomc_press_release_v1`

A substantive statement body must conform to the positive profile `fomc_press_release_v1`. To pass extraction, the article paragraphs must satisfy three positive criteria:
1. **Institutional Actor:** Contains at least one Committee actor reference:
   - `"federal open market committee"`
   - `"the committee"`
   - `"fomc"`
2. **Policy Decision / Stance Vocabulary:** Contains at least one policy stance, rate decision, or dual mandate marker:
   - `"target range"`
   - `"federal funds rate"`
   - `"monetary policy"`
   - `"policy action"`
   - `"decided to maintain"` / `"decided to raise"` / `"decided to lower"` / `"decided to reduce"` / `"decided to keep"`
   - `"dual mandate"`
   - `"price stability"`
   - `"maximum employment"`
   - `"approved the statement"` / `"approved the following statement"`
   - `"ample reserves"`
   - `"rates maintained"`
3. **Substantive Length:** Paragraph text must total at least 30 non-whitespace characters.

### 3.2 Honest Fail-Closed Behavior

Any document failing to satisfy this positive profile fails closed with typed rejection:
```json
{
  "status": "unsupported_document_type",
  "reason": "Article body does not match supported FOMC statement profile ('fomc_press_release_v1'): missing Committee policy decision or stance terms"
}
```
- **Placeholder Rejection:** The unavailable page (`"This page is temporarily unavailable. Please try again later."`) is rejected with `status="unsupported_document_type"`.
- **Non-Policy Rejection:** Generic announcements lacking monetary policy decisions (e.g. administrative task forces, conference schedules, generic board notices) fail closed.
- **Preservation of Real Texts:** 100% of the raw HTML paragraphs, numbers, and conditions in both official historical statements (September 16, 2026 and July 29, 2026) satisfy this profile and remain completely intact without alterations.

---

## 4. Verification Matrix

| Category | Control Case | Expected Outcome | Actual Result on `amper` | Status |
|:---|:---|:---|:---|:---:|
| **F1** | `lineage_wrong_types` (archive: 17, sha: true, list) | Typed rejection `invalid_manifest` | `status="invalid_manifest"`, rejected on integer `archive_name` | **PASS** |
| **F1** | `lineage_malformed_sha` (not 64-hex) | Typed rejection `invalid_manifest` | `status="invalid_manifest"`, rejected on malformed SHA | **PASS** |
| **F1** | `lineage_invalid_locator` (string instead of dict) | Typed rejection `invalid_manifest` | `status="invalid_manifest"`, rejected on string locator | **PASS** |
| **F1** | `lineage_form_b_legacy` (receipt_id / revision_id) | Positive extraction | Extracted; `success=true`, ProofSpans valid | **PASS** |
| **F1** | `lineage_contradictory_aliases` (receipt_id != feed_receipt_id) | Typed rejection `invalid_manifest` | `status="invalid_manifest"`, contradictory aliases | **PASS** |
| **F1** | `provenance_contradiction` (downloaded + synthetic_fixture=True) | Typed rejection `invalid_manifest` | `status="invalid_manifest"`, contradictory provenance rejected | **PASS** |
| **F1** | `positive_explicit_synthetic` (clean synthetic manifest) | Extracted with honest synthetic status | Extracted; `NOT_APPLICABLE_SYNTHETIC`, no fetch claims | **PASS** |
| **F2** | `body_temporarily_unavailable` placeholder | Typed rejection `unsupported_document_type` | `status="unsupported_document_type"`, profile match failed | **PASS** |
| **F2** | `body_generic_non_policy` | Typed rejection `unsupported_document_type` | `status="unsupported_document_type"`, profile match failed | **PASS** |
| **F2** | `positive_real_september_2026` | 10 ProofSpans valid, bundle published | 10/10 ProofSpans valid, digest `36fa921f...` | **PASS** |
| **F2** | `positive_real_july_2026` | 11 ProofSpans valid, bundle published | 11/11 ProofSpans valid, digest `802767e5...` | **PASS** |
| **F3** | `closing_tags_with_comments` (`<!-- </p> -->`, `<!-- </div> -->`) | Full text preserved, container covers all spans | Full text preserved, container covers all spans | **PASS** |
| **F4** | `single_entity_decode` (no double unescaping) | Preserves `&amp;#49;` as literal `&#49;` | Verified; single decode intact | **PASS** |
| **F5** | `comparable_precision_instant` | Offset boundary verified | Verified; temporal comparison intact | **PASS** |
| **F6** | `destination_and_temp_symlink_protection` | Rejects pre-existing symlinks | Verified; safe atomic write intact | **PASS** |

---

## 5. Execution Command Log & Reproducible Output

All commands executed on server `amper` via SSH:

### 5.1 Demonstration Script (`run_demo.py`)
```bash
$ /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python docs/research/2026-10-19/r3/increment1/revision4/run_demo.py
```
**Output:**
```
======================================================================
Pythia R3.1.4: Offline FOMC Statement Extractor Demonstration (Revision 4)
======================================================================

[Step 1] Verifying 32 blobs across 4 live archives BEFORE demo...
Total blobs verified: 32; Healthy: True
All 32 original CAS blobs across all 4 live archives are 100% intact.

[Step 2] Processing Real Official FOMC Statements...

  --- Extracting monetary20260916a ---
  All 10 ProofSpans verified against raw HTML blob (100% valid).
  Structural locators verified: unambiguous (cardinality 1) for container and all 6 paragraphs.
  Bundle published: statement_bundle_36fa921ff1daefa31826f204876d99701b5e1db83eb35cbc2e9f2d8ded77d620.json (digest: 36fa921ff1daefa31826f204876d99701b5e1db83eb35cbc2e9f2d8ded77d620)
  Preview published: statement_preview_monetary20260916a.md

  --- Extracting monetary20260729a ---
  All 11 ProofSpans verified against raw HTML blob (100% valid).
  Structural locators verified: unambiguous (cardinality 1) for container and all 7 paragraphs.
  Bundle published: statement_bundle_802767e51c8d20ff567f1ad8adca33b86d2cc5348e840e90eaccc088fe9ac52f.json (digest: 802767e51c8d20ff567f1ad8adca33b86d2cc5348e840e90eaccc088fe9ac52f)
  Preview published: statement_preview_monetary20260729a.md

[Step 3] Running Residual Boundary Controls (F1 - F6)...
  Testing F1: Typed Manifest Validation & Lineage Checks...
  Testing F2: Statement Profile & Body Gating...
  Testing F3: Comment-closing boundaries & full text preservation...
  Testing F4: Single Entity Decode (no double unescaping)...
  Testing F5: Comparable Precision Instant Comparison...
  Testing F6: Destination Directory & Temp File Protection...

[Step 4] Verifying 32 blobs across 4 live archives AFTER demo...
Total blobs verified: 32; Healthy: True
All 32 original CAS blobs across all 4 live archives are 100% intact and unchanged.

[Step 5] Demo Summary written to: docs/research/2026-10-19/r3/increment1/revision4/demo_summary.json

======================================================================
R3.1.4 REVISION 4 DEMO COMPLETED SUCCESSFULLY (100% PASS, ZERO NETWORK)
======================================================================
```
**Exit Code:** 0

### 5.2 Statement Extractor Test Suite Execution
```bash
$ PYTHONPATH=/home/pythia/pythia-evidence-r31 /home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests/test_fed_statement_extract.py -v
```
**Output:**
```
============================== 38 passed in 2.87s ==============================
```
**Exit Code:** 0

### 5.3 Full Evaluation Test Suite Execution
```bash
$ PYTHONPATH=/home/pythia/pythia-evidence-r31 /home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests -v
```
**Output:**
```
============================= 251 passed in 21.43s =============================
```
**Exit Code:** 0

### 5.4 Historical Replay & CAS Verification
- Historical demo replay: 35/35 facts valid, digest `cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde` (100% match).
- All 32 original CAS blobs across 4 live archives: 100% intact before and after run.

---

## 6. Test Adaptations Log

To conform to the positive statement body profile (`fomc_press_release_v1`) without altering any selector or entity decoding logic, minimal adaptations were made to synthetic dummy text in two existing test functions:
1. `test_f3_unambiguous_structural_locators`:
   - Updated dummy paragraphs from `"First statement paragraph."` to include Committee policy context (`"The Federal Open Market Committee decided to maintain the target range for the federal funds rate at 4 to 5 percent in support of its dual mandate."`).
   - Selector cardinality assertions (`len(matches) == 1`) and containment checks remain 100% preserved.
2. `test_f4_single_entity_decode`:
   - Updated dummy paragraph from `"Literal &amp;#49; percent &amp;amp; rate."` to include Committee policy context (`"The Committee decided to maintain the federal funds rate at &amp;#49; percent &amp;amp; target range."`).
   - Entity assertions (`"&#49; percent &amp; target range"` ensuring single unescaping without double decoding) remain 100% preserved.

---

## 7. Published Artifact Digests (Revision 4)

- **Extractor Version:** `pythia-statement-extractor-r3/0.1.4`
- **Bundle Version:** `pythia-statement-bundle-r3/0.1.4`
- **Schema Version:** `pythia-statement-schema-r3/1.2`
- **Supported Profile:** `fomc_press_release_v1`
- **September 16, 2026 Statement (`monetary20260916a`):**
  - Raw HTML Blob SHA-256: `8252bf02066af1c379bdcad3b9b70cc8cdbf8e21183eb3f00c8df0a2dba9391f` (84,741 bytes)
  - Bundle File: `statement_bundle_36fa921ff1daefa31826f204876d99701b5e1db83eb35cbc2e9f2d8ded77d620.json`
  - Bundle Digest: `36fa921ff1daefa31826f204876d99701b5e1db83eb35cbc2e9f2d8ded77d620`
  - Paragraphs: 6
  - Total ProofSpans: 10 (all 10 verified valid)
  - Preview File: `statement_preview_monetary20260916a.md`
- **July 29, 2026 Statement (`monetary20260729a`):**
  - Raw HTML Blob SHA-256: `f3cfa831daa2e63b0c370bd0de3b58cccc30439ee3e8378bc0a6c2dbf650ef49` (83,318 bytes)
  - Bundle File: `statement_bundle_802767e51c8d20ff567f1ad8adca33b86d2cc5348e840e90eaccc088fe9ac52f.json`
  - Bundle Digest: `802767e51c8d20ff567f1ad8adca33b86d2cc5348e840e90eaccc088fe9ac52f`
  - Paragraphs: 7
  - Total ProofSpans: 11 (all 11 verified valid)
  - Preview File: `statement_preview_monetary20260729a.md`
- **Total ProofSpans Verified:** 21 valid.

---

## 8. Status & Architectural Boundaries

- **R3.1.4 Scope:** Confined strictly to confirmed F1/F2 residues.
- **Accepted Baseline:** `395d288a0ecb23829b0232debb29865a00e6ef38` remains the accepted development baseline.
- **R3 Lifecycle:** `IN_PROGRESS`. R3.1 is not unilaterally accepted; R3.2 is **NOT opened**.
- **Historical Acquisition:** Explicitly documented as **`PARTIAL`** (`usable_for_forecast = False`).
- **Next Step:** Stand by for independent architectural review.
