# Pythia R3.1.6: Synthetic Nullable Dates Contract and Lineage Schema Report (Revision 6)

**Date:** 2026-10-20  
**Execution Environment:** `amper` (Ubuntu 24.04, Linux 6.8.0-45-generic x86_64)  
**Worktree:** `/home/pythia/pythia-evidence-r31`  
**Branch:** `codex/evidence-r31-statements`  
**Base Commit:** `395d288a0ecb23829b0232debb29865a00e6ef38` (`codex/evidence-r3-extraction` after R3.0.2)  
**Parent Candidate Under Review:** `a1232a74f5ccb3ceac57a9b349a5542a9bfd643b` (R3.1.5 CHANGES_REQUIRED)  
**Python Runtime:** `/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python` (Python 3.10.12)  
**Executor Verdict:** **IMPLEMENTED** (with honest **PARTIAL** on historical acquisition evidence)

---

## 1. Executive Summary

This increment (R3.1.6) resolves the single confirmed regression from R3.1.5 identified in architectural review `r315-a1232a7` ([`REVIEW_RU.md`](file:///Users/gerwer/Desktop/MemPalace/project/pythia/reviews/2026-10-20/r315-a1232a7/REVIEW_RU.md)):
- **Defect Mechanism:** In `fed_statement_extract.py:763–779`, the synthetic parent reference loop previously executed a blanket `if v is None: return invalid_manifest` check before reaching the nullable date validator (`lines 1020–1035`). As a consequence, valid explicit synthetic manifests providing `feed_published_at: null` or `feed_raw_pub_date: null` were erroneously rejected with `invalid_manifest: Synthetic parent_feed_reference field ... cannot be null`.
- **Resolution:** Gated date fields (`feed_published_at`, `feed_raw_pub_date`) within the synthetic field loop, explicitly allowing `None` as normal unknown date while validating non-null values as non-empty strings (and rejecting invalid types such as `int`, `bool`, `list`). Non-date synthetic fields (`fixture_kind`, `feed_item_locator`, identifiers) strictly maintain their non-null constraint (`if v is None: return invalid_manifest`).
- **Version Bumps:** Extractor and bundle versions bumped to `0.1.6`:
  - `STATEMENT_EXTRACTOR_VERSION = "pythia-statement-extractor-r3/0.1.6"`
  - `STATEMENT_BUNDLE_VERSION = "pythia-statement-bundle-r3/0.1.6"`
  - `STATEMENT_SCHEMA_VERSION = "pythia-statement-schema-r3/1.2"` (unchanged)

**Verification & Invariants:**
- **Nullable Dates Contract Probe (`nullable_dates_probe.py`):** **24 of 24 cases met (100%)**, 0 unmet.
- **Finite Requirements Matrix (`requirements_matrix.py`):** **64 of 64 cases met (100%)**, 0 unmet.
- **Root Controls Confirmation (`lineage_confirmation.py`):** **8 of 8 controls met (100%)**, 0 unmet.
- **Full Test Suite (`pytest python/evaluation/tests`):** **256 of 256 tests passed (100%)** in 13.28s, including new dedicated regression test `test_f1_synthetic_nullable_dates`.
- **Historical Demo Replay (`run_demo.py` revision 2):** **35 of 35 fact proofs valid**, bundle digest `cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde` 100% replayed.
- **Live CAS Archives Intact:** All 32 original CAS blobs across all 4 live archives (`live-r2-revision1`, `live-r2-revision2`, `live-r2-revision3`, `live-r2-run1`) verified 100% healthy and unmodified before and after demo execution.
- **Zero Network:** All imports, executions, tests, and demo runs executed under strict socket guard forbidding DNS / connect / connect_ex.
- **Accepted Baseline:** Accepted development baseline remains **`395d288a0ecb23829b0232debb29865a00e6ef38`**. Entire R3 is `IN_PROGRESS`; R3.1 is not accepted; R3.2 is not opened.
- **Historical Acquisition Evidence:** Explicitly and honestly preserved as **`PARTIAL`** (`usable_for_forecast = False`).

---

## 2. Parent Feed Reference Schema Contract

The parent feed reference contract uniformly treats `feed_published_at` and `feed_raw_pub_date` as nullable strings across both real research fixtures and synthetic fixtures:

| Field Name | Type | Value Constraints | Applicable Context | Rejection Status |
|:mask|:mask|:mask|:mask:|:mask|
| `feed_fact_id` | `str` | Non-empty string, not bool | Real (Form A & B) | `invalid_manifest` |
| `feed_blob_sha256` | `str` | Exactly 64 hex characters | Real (Form A & B) | `invalid_manifest` |
| `feed_item_locator` | `dict` | `type="xml_xpath"`, `path="/rss/channel/item[N]"` (N >= 1) | Real & Synthetic | `invalid_manifest` |
| `archive_name` | `str` | Non-empty string, not bool | Real Form A | `invalid_manifest` |
| `parent_bundle_digest` | `str` | Exactly 64 hex characters | Real Form A | `invalid_manifest` |
| `receipt_id` / `feed_receipt_id` | `str` | Non-empty string, not bool | Real Form B (optional on A) | `invalid_manifest` |
| `revision_id` / `feed_revision_id` | `str` | Non-empty string, not bool | Real Form B (optional on A) | `invalid_manifest` |
| `source_id` | `str` | Non-empty string, not bool | Real Form B (optional on A) | `invalid_manifest` |
| `fixture_kind` | `str` | Exactly `"synthetic_fixture"` | Synthetic only | `invalid_manifest` |
| `feed_published_at` | `str` | None | ISO-8601 string or `None` | Real & Synthetic | `invalid_manifest` |
| `feed_raw_pub_date` | `str` | None | RFC-2822 string or `None` | Real & Synthetic | `invalid_manifest` |

---

## 3. Code Modifications

In `python/evaluation/fed_statement_extract.py`:

```diff
 STATEMENT_EXTRACTOR_VERSION = "pythia-statement-extractor-r3/0.1.6"
 STATEMENT_SCHEMA_VERSION = "pythia-statement-schema-r3/1.2"
-STATEMENT_BUNDLE_VERSION = "pythia-statement-bundle-r3/0.1.5"
+STATEMENT_BUNDLE_VERSION = "pythia-statement-bundle-r3/0.1.6"
...
             for k, v in parent_feed_val.items():
+                if k in ("feed_published_at", "feed_raw_pub_date"):
+                    if v is not None:
+                        if not isinstance(v, str) or isinstance(v, bool) or not v.strip():
+                            return ExtractionResult(
+                                document=None,
+                                rejections=[
+                                     RejectionRecord(
+                                         status="invalid_manifest",
+                                         reason=f"Synthetic parent_feed_reference field {k!r} must be a nullable string, got {type(v).__name__}",
+                                         source_url=source_url,
+                                         raw_blob_sha256=actual_sha,
+                                     )],
+                                extractor_version=STATEMENT_EXTRACTOR_VERSION,
+                                schema_version=STATEMENT_SCHEMA_VERSION,
+                            )
+                    continue
+
                 if v is None:
                     return ExtractionResult(
                         document=None,
```

---

## 4. Residual Controls Verification

### 4.1 Nullable Dates Probe (24/24 MET)
All 24 cases in `project/pythia/reviews/2026-10-20/r315-a1232a7/semantics/nullable_dates_probe.py` evaluated on `amper`:
- `real_valid_strings`: MET
- `real_published_null_raw_valid`: MET
- `real_raw_null_published_valid`: MET
- `real_both_null`: MET
- `real_published_missing_raw_valid`: MET
- `real_raw_missing_published_valid`: MET
- `real_feed_published_at_wrong_int`: MET (rejected)
- `real_feed_published_at_wrong_bool`: MET (rejected)
- `real_feed_published_at_wrong_list`: MET (rejected)
- `real_feed_raw_pub_date_wrong_int`
: MET (rejected)
- `real_feed_raw_pub_date_wrong_bool`: MET (rejected)
- `real_feed_raw_pub_date_wrong_list`: MET (rejected)
- `synthetic_valid_strings`: MET
- `synthetic_published_null_raw_valid`: MET (previously unmet in R3.1.5; now resolved)
- `synthetic_raw_null_published_valid`: MET (previously unmet in R3.1.5; now resolved)
- `synthetic_both_null`: MET (previously unmet in R3.1.5; now resolved)
- `synthetic_published_missing_raw_valid`: MET
- `synthetic_raw_missing_published_valid`: MET
- `synthetic_feed_published_at_wrong_int`
: MET (rejected)
- `synthetic_feed_published_at_wrong_bool`: MET (rejected)
- `synthetic_feed_published_at_wrong_list`: MET (rejected)
- `synthetic_feed_raw_pub_date_wrong_int`
: MET (rejected)
- `synthetic_feed_raw_pub_date_wrong_bool`: MET (rejected)
- `synthetic_feed_raw_pub_date_wrong_list`: MET (rejected)

*jSummary:** Total: 24, Met: 24, Unmet: 0.

### 4.2 Requirements Matrix (64/64 MET)
All 64 cases from `requirements_matrix.py` evaluated on `amper`:
**Summary:** Total: 64, Met: 64, Unmet: 0.

### 4.3 Root Lineage Controls (8/8 MET)
All 8 root controls from `lineage_confirmation.py` evaluated on `amper`:
**Summary:** Total: 8, Met: 8, Unmet: 0.

---

## 5. Offline Demonstration & Test Suite Results

### 5.1 Revision 6 Offline Demonstration (`revision6/run_demo.py`)
```
=====================================================================
Pythia R3.1.6: Offline FOMC Statement Extractor Demonstration (Revision 6)
=====================================================================

[Step 1] Verifying 32 blobs across 4 live archives BEFORE demo...
Total blobs verified: 32; Healthy: True
All 32 original CAS blobs across all 4 live archives are 100% intact.

[Step 2] Processing Real Official FOMC Statements...

  --- Extracting monetary20260916a ---
  All 10 ProofSpans verified against raw HTML blob (100% valid).
  Structural locators verified: unambiguous (cardinality 1) for container and all 6 paragraphs.
  Bundle published: statement_bundle_205b11bdb0a13295217679b68dfffa4ba9a8d2dcddd683659fbdf9533a707381.json (digest: 205b11bdb0a13295217679b68dfffa4ba9a8d2dcddd683659fbdf9533a707381)
  Preview published: statement_preview_monetary20260916a.md

  --- Extracting monetary20260729a ---
  All 11 ProofSpans verified against raw HTML blob (100% valid).
  Structural locators verified: unambiguous (cardinality 1) for container and all 7 paragraphs.
  Bundle published: statement_bundle_958b23e56341b08b9b092e56868767af09389a1f3530cfd6414cfb6dab145441.json (digest: 958b23e56341b08b9b092e56868767af09389a1f3530cfd6414cfb6dab145441)
  Preview published: statement_preview_monetary20260729a.md

[Step 3] Running Residual Boundary Controls (F1 - Fe)...
  Testing F1: Typed Manifest Validation & Lineage Checks...
  Testing F2: Statement Profile & Body Gating...
  Testing F3: Comment Closing Tag Boundaries...
  Testing F4: Single Entity HTML Decoding...
  Testing F5: Temporal & Date Controls...
  Testing F6: Destination Directory Safety & Symlink Protection...

[Step 4] Verifying 32 blobs across 4 live archives AFTER demo...
Total blobs verified: 32; Healthy: True
All 32 original CAS blobs across all 4 live archives are 100% intact and unchanged.

[Step 5] Demo Summary written to: docs/research/2026-10-20/r3/increment1/revision6/demo_summary.json

=====================================================================
R3.1.6 REVISION 6 DEMO COMPLETED SUCCESSFULLY (100% PASS, ZERO NETWORK)
=====================================================================
```

### 5.2 Test Suite Execution
```bash
$ PYTHONPATH=/home/pythia/pythia-evidence-r31 /home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests -v
```
**Result:**
```
============================= 256 passed in 13.28s =============================
```
Saved to `docs/research/2026-10-20/r3/increment1/revision6/test_run.log`.

---

## 6. Published Artifact Digests (Revision 6)

- **Extractor Version:** `pythia-statement-extractor-r3/0.1.6`
- **Bundle Version:** `pythia-statement-bundle-r3/0.1.6`
- **Schema Version:** `pythia-statement-schema-r3/1.2`
- **Supported Profile:** `fomc_press_release_v1`
- **September 16, 2026 Statement (`monetary20260916a`):**
  - Raw HTML Blob SHA-256: `8252bf02066af1c379bdcad3b9b70cc8cdbf8e21183eb3f00c8df0a2dba9391f` (84,741 bytes)
  - Bundle File: `statement_bundle_205b11bdb0a13295217679b68dfffa4ba9a8d2dcddd683659fbdf9533a707381.json`
  - Bundle Digest: `205b11bdb0a13295217679b68dfffa4ba9a8d2dcddd683659fbdf9533a707381`
  - Paragraphs: 6
  - Total ProofSpans: 10 (all 10 verified valid)
  - Preview File: `statement_preview_monetary20260916a.md`
- **July 29, 2026 Statement (`monetary20260729a`):**
  - Raw HTML Blob SHA-256: `f3cfa831daa2e63b0c370bd0de3b58cccc30439ee3e8378bc0a6c2dbf650ef49` (83,318 bytes)
  - Bundle File: `statement_bundle_958b23e56341b08b9b092e56868767af09389a1f3530cfd6414cfb6dab145441.json`
  - Bundle Digest: `958b23e56341b08b9b092e56868767af09389a1f3530cfd6414cfb6dab145441`
  - Paragraphs: 7
  - Total ProofSpans: 11 (all 11 verified valid)
  - Preview File: `statement_preview_monetary20260729a.md`
- **Total ProofSpans Verified:** 21 valid.

---

## 7. Status & Architectural Boundaries

- **R3.1.6 Scope:** Confined strictly to synthetic nullable dates contract fix.
- **Accepted Baseline:** `395d288a0ecb23829b0232debb29865a00e6ef38` remains the accepted development baseline.
- **R3 Lifecycle:** `IN_PROGRESS`. R3.1 is not unilaterally accepted; R3.2 is **NOT opened**.
- **Historical Acquisition:** Explicitly documented as **`PARTIAL`** (`usable_for_forecast = False`).
- **Next Step:** Awaiting independent architectural review.
