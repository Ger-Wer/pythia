# Pythia R3.1.5: Complete Lineage Schema Validation and Provenance Report (Revision 5)

**Date:** 2026-10-20  
**Execution Environment:** `amper` (Ubuntu 24.04, Linux 6.8.0-45-generic x86_64)  
**Worktree:** `/home/pythia/pythia-evidence-r31`  
**Branch:** `codex/evidence-r31-statements`  
**Base Commit:** `395d288a0ecb23829b0232debb29865a00e6ef38` (`codex/evidence-r3-extraction` after R3.0.2)  
**Parent Candidate Under Review:** `21c14b7b3f24a00667ae0c3086bdd56b7769ecec` (R3.1.4 CHANGES_REQUIRED)  
**Python Runtime:** `/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python` (Python 3.10.12)  
**Executor Verdict:** **IMPLEMENTED** (with honest **PARTIAL** on historical acquisition evidence)

---

## 1. Executive Summary

This increment (R3.1.5) resolves the remaining **F1** structural lineage schema and provenance contract residues identified in architectural review `r314-21c14b7` ([`REVIEW_RU.md`](file:///Users/gerwer/Desktop/MemPalace/project/pythia/reviews/2026-10-20/r314-21c14b7/REVIEW_RU.md)):
1. **Sub-case 1 (Supported RSS Item Locator):** Enforced syntactic offline validation of `feed_item_locator`, requiring `type="xml_xpath"` and path matching `^/rss/channel/item\[([1-9][0-9]*)\]$` (1-based positive integer index). Non-RSS paths (`/html/body/p[1]`), CSS selectors (`#article`), non-positive item index (`/rss/channel/item[0]`), unsupported locator types, and non-structural values are rejected with typed `status="invalid_manifest"`.
2. **Sub-case 2 (Independent Known Identifiers Validation in Form A / Form B):** Every present known identifier (`receipt_id`, `feed_receipt_id`, `revision_id`, `feed_revision_id`, `source_id`) is validated independently of whether another form is already satisfied. Nulls, booleans, integers, and empty strings are rejected. Coexisting aliases must agree; conflicting aliases are rejected.
3. **Sub-case 3 (Synthetic Parent Marker Contract):** Explicit synthetic fixtures require `fixture_kind` to be exactly `"synthetic_fixture"` if present; contradictory markers (`"downloaded_research_fixture"`, `"not_synthetic"`, `None`) are rejected with typed `invalid_manifest`. Honest explicit synthetic fixtures without fake real IDs remain accepted.
4. **Sub-case 4 (Parent Feed Datetime Type Gating):** Lineage date fields (`feed_published_at`, `feed_raw_pub_date`) are strictly validated as nullable strings before slicing/parsing. Non-string types (e.g. `feed_published_at=42`) yield typed `status="invalid_manifest"` without uncaught exceptions (`TypeError`), while valid dates or missing/null values remain safely handled.

**Boundaries and Preserved Contracts:**
- **F2** statement body profile (`fomc_press_release_v1`) remains closed in its documented scope.
- **F3** comment-closing boundaries, **F4** single-entity decoding, **F5** comparable precision instant comparison, and **F6** destination directory / temp symlink protection are strictly preserved.
- **Minor Report Correction for F4:** As confirmed in review, `test_f4_single_entity_decode` preserves the literal entity paragraph verbatim (`"Literal &amp;#49; percent; literal &amp;lt;5."`) and adds an accompanying Committee policy paragraph to satisfy the body profile simultaneously, preserving the exact entity decoding assertion.
- All code modifications, test runs, matrix verifications, and bundle publications were executed exclusively on server `amper` under zero-network socket enforcement.
- Accepted development baseline remains **`395d288a0ecb23829b0232debb29865a00e6ef38`**. Entire R3 is `IN_PROGRESS`; R3.1 is not accepted; R3.2 is not opened.
- Historical acquisition evidence remains honestly marked as **`PARTIAL`** (`usable_for_forecast = False`).
- All prior revision artifacts (`initial/`, `revision1/`, `revision2/`, `revision3/`, `revision4/`) remain preserved. All revision 5 artifacts reside in **`docs/research/2026-10-20/r3/increment1/revision5/`**.

---

## 2. Definitive Lineage Schema Contract (F1)

The extractor enforces structural offline validation of `parent_feed_reference` according to the unified contract below:

| Field Name | Type | Value Constraints | Applicable Forms | Rejection Rule |
|:---|:---|:---|:---:|:---|
| `feed_fact_id` | `str` | Non-empty string, not bool | Real (A & B) | Required; missing/empty/non-str -> `invalid_manifest` |
| `feed_blob_sha256` | `str` | Exactly 64 hex characters | Real (A & B) | Required; missing/malformed hex -> `invalid_manifest` |
| `feed_item_locator` | `dict` | `type="xml_xpath"`, `path="/rss/channel/item[N]"` (N >= 1) | Real & Synthetic | Required on Real; invalid type/path/index -> `invalid_manifest` |
| `archive_name` | `str` | Non-empty string, not bool | Form A | Required if Form A; invalid -> `invalid_manifest` |
| `parent_bundle_digest` | `str` | Exactly 64 hex characters | Form A | Required if Form A; invalid -> `invalid_manifest` |
| `receipt_id` / `feed_receipt_id` | `str` | Non-empty string, not bool | Form B (optional on A) | If present, must be non-empty str; conflict -> `invalid_manifest` |
| `revision_id` / `feed_revision_id` | `str` | Non-empty string, not bool | Form B (optional on A) | If present, must be non-empty str; conflict -> `invalid_manifest` |
| `source_id` | `str` | Non-empty string, not bool | Form B (optional on A) | If present, must be non-empty str; null/empty -> `invalid_manifest` |
| `fixture_kind` | `str` | Must equal `"synthetic_fixture"` | Synthetic only | Required if present; forbidden on real; mismatch -> `invalid_manifest` |
| `feed_published_at` | `str` \| `None` | ISO-8601 string or `None` | Real & Synthetic | If present and not null, must be non-empty str; bad type -> `invalid_manifest` |
| `feed_raw_pub_date` | `str` \| `None` | RFC-2822 string or `None` | Real & Synthetic | If present and not null, must be non-empty str; bad type -> `invalid_manifest` |

---

## 3. Four F1 Sub-Cases Resolutions

### 3.1 Sub-case 1: Supported RSS Item Locator Validation
- Function `_validate_rss_item_locator` checks:
  1. `locator` is a non-empty dictionary.
  2. `locator["type"] == "xml_xpath"` (rejects `"html_css"`, arbitrary types).
  3. `locator["path"]` matches `^/rss/channel/item\[([1-9][0-9]*)\]$` (rejects `/html/body/p[1]`, `/rss/channel/item[0]`, `#article`, non-XPath strings).
- Fails closed with typed rejection `status="invalid_manifest"`.

### 3.2 Sub-case 2: Known Identifiers Independent Validation & Aliases
- Evaluates `known_id_fields = ("receipt_id", "feed_receipt_id", "revision_id", "feed_revision_id", "source_id")`.
- If any known identifier is present in `parent_feed_reference`, it must be a non-empty string.
- If both `receipt_id` and `feed_receipt_id` are present, they must be identical strings.
- If both `revision_id` and `feed_revision_id` are present, they must be identical strings.
- Form A or Form B completeness is evaluated independently: Form A requires `archive_name` + `parent_bundle_digest`; Form B requires receipt + revision. Adding partial Form B fields to Form A does not bypass type validation.

### 3.3 Sub-case 3: Synthetic Parent Marker Validation
- If `is_synthetic` is true and `parent_feed_reference` contains `"fixture_kind"`:
  - Value must be string `"synthetic_fixture"`.
  - Values `"downloaded_research_fixture"`, `"not_synthetic"`, or `None` trigger typed rejection `status="invalid_manifest"`.
- Real manifests containing `"fixture_kind"` trigger typed rejection `status="invalid_manifest"`.

### 3.4 Sub-case 4: Parent Feed Datetime Type Gating
- `feed_published_at` and `feed_raw_pub_date` are validated in `extract_fed_statement`:
  - If present and not `None`, must be non-empty strings.
  - Types such as `int` (`feed_published_at=42`), `bool`, or `list` trigger typed rejection `status="invalid_manifest"` before date logic is entered.
  - Slicing `feed_pub_at[:10]` is guarded with `isinstance(feed_pub_at, str)`.

---

## 4. Verification Matrix & Probe Results

### 4.1 Finite Requirements Matrix (`semantics/requirements_matrix.py`)
Executed on server `amper` with strict socket guard active:
- **Total Cases:** 64
- **Met:** 64
- **Unmet:** 0
- **Network Attempts:** 0
- **Summary:** 100% PASS across all 50 previous positive/negative cases and all 14 previously unmet cases.

### 4.2 Independent Root Controls (`root_controls/lineage_confirmation.py`)
Executed on server `amper`:
- **Total Controls:** 8
- **Unmet Requirements:** 0
- **Network Attempts:** 0
- **Summary:** 100% PASS on Form A positive, Form B positive, explicit synthetic positive, 3 locator negatives, Form A with bad receipt type negative, and contradictory synthetic parent marker negative.

### 4.3 Test Suite Executions on `amper`
- **Statement Extractor Test Suite:** **42 passed in 1.15s** (`test_fed_statement_extract.py`, 4 new dedicated test functions added).
- **Full Evaluation Test Suite:** **255 passed in 12.50s** (0 network calls, socket guard active).
- **Historical Demo Replay:** 35/35 facts valid, digest `cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde` (100% match).
- **CAS Archives:** 32/32 blobs across all 4 live archives (`live-r2-run1`, `live-r2-revision1`, `live-r2-revision2`, `live-r2-revision3`) verified intact before and after demo run.

---

## 5. Execution Command Log & Reproducible Output

All commands executed on server `amper` via SSH:

### 5.1 Demonstration Script (`run_demo.py`)
```bash
$ /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python docs/research/2026-10-20/r3/increment1/revision5/run_demo.py
```
**Output:**
```
======================================================================
Pythia R3.1.5: Offline FOMC Statement Extractor Demonstration (Revision 5)
======================================================================

[Step 1] Verifying 32 blobs across 4 live archives BEFORE demo...
Total blobs verified: 32; Healthy: True
All 32 original CAS blobs across all 4 live archives are 100% intact.

[Step 2] Processing Real Official FOMC Statements...

  --- Extracting monetary20260916a ---
  All 10 ProofSpans verified against raw HTML blob (100% valid).
  Structural locators verified: unambiguous (cardinality 1) for container and all 6 paragraphs.
  Bundle published: statement_bundle_74f81ba3b9b9c94eda27d4520ffd2091687851b476ed4b862d62b6664d6d76e3.json (digest: 74f81ba3b9b9c94eda27d4520ffd2091687851b476ed4b862d62b6664d6d76e3)
  Preview published: statement_preview_monetary20260916a.md

  --- Extracting monetary20260729a ---
  All 11 ProofSpans verified against raw HTML blob (100% valid).
  Structural locators verified: unambiguous (cardinality 1) for container and all 7 paragraphs.
  Bundle published: statement_bundle_912c2d774bc34e076138881a9a1ad6c8a919e1921db26f0c43b09b3bbc1c8cc1.json (digest: 912c2d774bc34e076138881a9a1ad6c8a919e1921db26f0c43b09b3bbc1c8cc1)
  Preview published: statement_preview_monetary20260729a.md

[Step 3] Running Residual Boundary Controls (F1 - F6)...
  Testing F1: Typed Manifest Validation & Lineage Checks...
  Testing F2: Statement Profile & Body Gating...
  Testing F3: Comment Closing Tag Boundaries...
  Testing F4: Single Entity HTML Decoding...
  Testing F5: Temporal & Date Controls...
  Testing F6: Destination Directory Safety & Symlink Protection...

[Step 4] Verifying 32 blobs across 4 live archives AFTER demo...
Total blobs verified: 32; Healthy: True
All 32 original CAS blobs across all 4 live archives are 100% intact and unchanged.

[Step 5] Demo Summary written to: docs/research/2026-10-20/r3/increment1/revision5/demo_summary.json

======================================================================
R3.1.5 REVISION 5 DEMO COMPLETED SUCCESSFULLY (100% PASS, ZERO NETWORK)
======================================================================
```
**Exit Code:** 0

### 5.2 Full Test Suite Execution
```bash
$ PYTHONPATH=/home/pythia/pythia-evidence-r31 /home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests -v
```
**Output:**
```
============================= 255 passed in 12.50s =============================
```
**Exit Code:** 0

---

## 6. Published Artifact Digests (Revision 5)

- **Extractor Version:** `pythia-statement-extractor-r3/0.1.5`
- **Bundle Version:** `pythia-statement-bundle-r3/0.1.5`
- **Schema Version:** `pythia-statement-schema-r3/1.2`
- **Supported Profile:** `fomc_press_release_v1`
- **September 16, 2026 Statement (`monetary20260916a`):**
  - Raw HTML Blob SHA-256: `8252bf02066af1c379bdcad3b9b70cc8cdbf8e21183eb3f00c8df0a2dba9391f` (84,741 bytes)
  - Bundle File: `statement_bundle_74f81ba3b9b9c94eda27d4520ffd2091687851b476ed4b862d62b6664d6d76e3.json`
  - Bundle Digest: `74f81ba3b9b9c94eda27d4520ffd2091687851b476ed4b862d62b6664d6d76e3`
  - Paragraphs: 6
  - Total ProofSpans: 10 (all 10 verified valid)
  - Preview File: `statement_preview_monetary20260916a.md`
- **July 29, 2026 Statement (`monetary20260729a`):**
  - Raw HTML Blob SHA-256: `f3cfa831daa2e63b0c370bd0de3b58cccc30439ee3e8378bc0a6c2dbf650ef49` (83,318 bytes)
  - Bundle File: `statement_bundle_912c2d774bc34e076138881a9a1ad6c8a919e1921db26f0c43b09b3bbc1c8cc1.json`
  - Bundle Digest: `912c2d774bc34e076138881a9a1ad6c8a919e1921db26f0c43b09b3bbc1c8cc1`
  - Paragraphs: 7
  - Total ProofSpans: 11 (all 11 verified valid)
  - Preview File: `statement_preview_monetary20260729a.md`
- **Total ProofSpans Verified:** 21 valid.

---

## 7. Status & Architectural Boundaries

- **R3.1.5 Scope:** Confined strictly to confirmed F1 lineage schema residues.
- **Accepted Baseline:** `395d288a0ecb23829b0232debb29865a00e6ef38` remains the accepted development baseline.
- **R3 Lifecycle:** `IN_PROGRESS`. R3.1 is not unilaterally accepted; R3.2 is **NOT opened**.
- **Historical Acquisition:** Explicitly documented as **`PARTIAL`** (`usable_for_forecast = False`).
- **Next Step:** Stand by for independent architectural review.
