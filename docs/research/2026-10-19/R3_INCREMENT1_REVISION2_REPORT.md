# Pythia R3.1.2: Offline Statement Extraction Boundary Fixes Report (Revision 2)

**Date:** 2026-10-19  
**Execution Environment:** `amper` (Ubuntu 24.04, Linux 6.8.0-45-generic x86_64)  
**Worktree:** `/home/pythia/pythia-evidence-r31`  
**Branch:** `codex/evidence-r31-statements`  
**Base Commit:** `395d288a0ecb23829b0232debb29865a00e6ef38` (`codex/evidence-r3-extraction` after R3.0.2)  
**Candidate Under Review:** `a35e30ee7f641078502092e62c6d895b3e1e2ba5` (R3.1.1 CHANGES_REQUIRED)  
**Python Runtime:** `/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3` (Python 3.10.12)  
**Executor Verdict:** **IMPLEMENTED** (with honest **PARTIAL** on historical acquisition evidence)

---

## 1. Executive Summary

This increment (R3.1.2) completely resolves all remaining boundary defects across **F1, F2, F3, F5, and F6** identified in independent review `r311-a35e30e` ([`REVIEW_RU.md`](file:///Users/gerwer/Desktop/MemPalace/project/pythia/reviews/2026-10-19/r311-a35e30e/REVIEW_RU.md)), while maintaining the closed status of **F4** (single-entity decoding) and fully restoring meaningful test assertions across the entire test suite.

All code edits, evaluations, and test executions took place strictly on server `amper` under zero-network socket enforcement. The accepted development baseline remains `395d288a0ecb23829b0232debb29865a00e6ef38`.

All previous artifacts in `docs/research/2026-10-19/r3/increment1/` (both initial and `revision1/`) are preserved intact. All newly generated and corrected revision 2 artifacts reside in **`docs/research/2026-10-19/r3/increment1/revision2/`**.

---

## 2. Lineage & Erratum Preservation

The true lineage of canonical parent Fed RSS feed items remains verified and preserved:
- **Archive:** `live-r2-revision3`
- **Sequence:** 2
- **Receipt ID:** `39bf5ee5-5918-489f-b9e1-dfcfcfbce96b`
- **Revision ID:** `26b40558-1014-47f2-9b10-d7b8007a07d0`
- **Feed Blob SHA-256:** `69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1` (9,687 bytes)
- **Source ID:** `fed_monetary_rss` (`https://www.federalreserve.gov/feeds/press_monetary.xml`)
- **Parent Event Dossier Bundle:** `demo_dossier_bundle.json` (digest `cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde`, `as_of_seq = 8`)
- **Referenced Item Fact IDs:**
  - `fact-b7cdc057cc6ec28d` (`/rss/channel/item[3]`) $\rightarrow$ September 16, 2026 FOMC statement
  - `fact-23bc4c0fc0243b9e` (`/rss/channel/item[7]`) $\rightarrow$ July 29, 2026 FOMC statement

---

## 3. Honest Acquisition Status & Audit Scope

### A. Proven vs Unproven Historic Capabilities
The recovered standalone acquisition script (`original_acquisition_script.py`) executed at `2026-10-19T07:47:12Z` on `amper`. A rigorous re-audit of the recovered script reveals:
- **Mechanically Proven in Code:**
  - Enforced `https://` scheme and official domain whitelist (`federalreserve.gov`, `www.federalreserve.gov`).
  - Strict TLS verification enabled (`verify=True`).
  - Maximum body size limit of 2 MiB (`MAX_BYTES = 2097152`).
  - Redirect limit capped at 2 (`MAX_REDIRECTS = 2`).
  - Per-request timeout passed (`timeout=20.0`).
  - Environment exclusion (`session.trust_env = False`).
- **Unproven / Missing Capabilities:**
  - **No Process Supervisor:** The script passed `timeout=20.0` directly to `requests.Session.get()`, but lacked a hard process supervisor (e.g. `SIGALRM` or overall deadline). It only measured total elapsed time after completion via `time.perf_counter()`.
  - **Session Cookie Isolation on Redirects:** While `trust_env=False` excluded ambient environment credentials, the in-memory `requests.Session()` cookie jar was not explicitly reset or cleared across HTTP redirects.
  - **Manifest Schema Gaps:** No discrete boolean `eof_complete=True` or `attempt_count=1` was captured in the initial manifest schema.

### B. Decoupling of Parser Success from Acquisition Verification
The verdict on historical acquisition evidence is declared honestly as **PARTIAL**:
- `acquisition_verification_status: "PARTIAL"` is now explicitly recorded across all published manifests, statement bundles, research metadata, and markdown previews.
- The offline statement extractor operates purely as a deterministic parser over saved raw bytes. Offline parser success is strictly decoupled from runtime acquisition claims and cannot be used to infer full runtime transport or justify opening R3.2.

---

## 4. Detailed Boundary Defect Resolutions (R3.1.2 F1 - F6)

### F1: Typed Manifest Validation & Honest Acquisition Contract
- **Defects in a35e30e:**
  - Only checked 5 fields; accepted contradictory metadata (`research_provenance="production_verified"`, `issuer="An unrelated issuer"`, `usable_for_forecast=True`, `parent_feed_reference={}`) by silently overwriting with defaults.
  - Allowed non-HTTPS schemes and non-statement paths (e.g. `http://www.federalreserve.gov/aboutthefed.htm`).
  - Float `bytes_stored` was accepted without error; non-string `source_url` crashed with an uncaught `AttributeError`.
  - Allowed `http_status=None` and ignored `complete=False` or `eof_complete=False`.
- **Fixes in R3.1.2:**
  - Enforced typed manifest schema: `source_url` and `final_url` must be `str`; `bytes_stored` must be `int` (float rejected); `http_status` must be `int` and equal 200.
  - Enforced HTTPS-only scheme and official FOMC statement path prefix (`/newsevents/pressreleases/monetary`).
  - Fail closed on contradictory metadata: reject `usable_for_forecast=True`, invalid `research_provenance`, non-FOMC issuers, or empty `parent_feed_reference={}` with typed `invalid_manifest` rejections.
  - Explicit transport check: reject if `complete is False` or `eof_complete is False` with typed `transport_failure` rejection.
  - Added explicit machine-readable `acquisition_verification_status="PARTIAL"` in bundles, metadata, and preview headers.

### F2: Negative Document Body Gating Under Valid Skeletons
- **Defects in a35e30e:**
  - While exact JS-only placeholder was rejected, valid skeletons containing non-statement document bodies (such as `Minutes of the Board of Governors discount rate meetings on August 24 and September 14, 2026.`) published as positive statements.
- **Fixes in R3.1.2:**
  - Added substantive body content gate: rejects bodies containing discount rate meeting minutes, FOMC minutes, summary of economic projections, or advisory council proceedings with `unsupported_document_type`.
  - Maintains positive extraction for genuine FOMC statements and synthetic FOMC controls.

### F3: Exact DOM Node, Locator, and Raw Byte Span Correspondence
- **Defects in a35e30e:**
  - Regular expressions searched raw HTML directly, mistakenly matching inside HTML comments (`<!-- ... -->`) within headings and containers. A commented date `January 01, 1999` produced a crypto-valid proof for parsed `September 16, 2026`.
  - In `_build_element_selector`, `siblings.index(curr)` used BeautifulSoup's structural equality (`==`), assigning identical `:nth-of-type(1)` selectors to duplicate sibling paragraphs.
  - Title locator had hardcoded `h3.title`, resulting in 0 matches when an `h2` title was accepted.
- **Fixes in R3.1.2:**
  - Proof byte start offsets are calculated directly from BeautifulSoup's DOM node position (`sourceline` and `sourcepos` via `_node_byte_start`), completely bypassing any HTML comments.
  - Sibling paragraph index calculation now strictly uses object identity (`next(i for i, c in enumerate(siblings) if c is curr) + 1`), ensuring duplicate paragraphs receive distinct locators (`:nth-of-type(1)`, `:nth-of-type(2)`) and distinct raw byte spans.
  - Dynamic title tag locator: locator selector matches the actual DOM tag (`#article > div.heading {title_elem.name}.title`), ensuring cardinality == 1 for `h1`, `h2`, and `h3` variants.

### F4: Single-Entity HTML Decoding (Preserved from R3.1.1)
- Verified and preserved: HTML entities decoded exactly once via `BeautifulSoup.get_text()`, eliminating double unescaping. Literal escaped entities (`Literal &#49; percent; literal &lt;5.`) remain intact.

### F5: Comparable Precision & Timezone Instant Comparison
- **Defects in a35e30e:**
  - Used string prefix `feed_pub_at[:10] != published_date`. When page had EDT 14:00 (`2026-09-16T14:00:00-04:00`) and feed had JST 03:00 (`2026-09-17T03:00:00+09:00`), both represented the exact same UTC instant (`2026-09-16T18:00:00Z`), but triggered a false `date_mismatch_detected=True`.
- **Fixes in R3.1.2:**
  - Timezone-aware instant comparison: when both page and feed contain timestamps, parse both into timezone-aware datetimes and compare at the UTC level (`dt.astimezone(timezone.utc)`).
  - Same-instant offsets (`2026-09-16T14:00:00-04:00` vs `2026-09-17T03:00:00+09:00`) correctly yield `date_mismatch_detected=False`.
  - Date-only page dates do not invent timezones; they compare against both local and UTC representations of the feed timestamp.
  - Genuine multi-day or date mismatches continue to record explicit warning diagnostics.

### F6: Comprehensive Destination & Temp File Protection
- **Defects in a35e30e:**
  - Only fixture input directory was protected; output directories pointing to manifest input directories or symlinks to manifest directories were allowed.
  - Temporary files (`.tmp_publish_*.json`, `.tmp_prev_*.md`) were written using `write_bytes()`, which follows pre-existing symlinks and allowed arbitrary file overwrites.
- **Fixes in R3.1.2:**
  - Protected both fixture and manifest directories (and all ancestors, descendants, and symlinks) using resolved containment checks (`is_relative_to` and `==`).
  - Temporary files are created exclusively using `os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)`. If a temporary symlink or pre-existing file exists at the temporary path, the operation aborts with `StatementPublishError` without following the symlink or modifying the target.

---

## 5. Restoration of Meaningful Test Coverage

In R3.1.2, all meaningful test cases previously omitted in `a35e30e` have been fully restored and expanded:
1. `test_empty_content_rejected`: restores empty body / whitespace container rejection (`empty_content`).
2. `test_missing_article_structure_rejected`: restores missing `#article` rejection (`unsupported_structure`).
3. `test_explicit_timezone_release_time_parsed`: restores explicit EST parsing (`2026-11-05T14:00:00-05:00`).
4. `test_invalid_date_rejected_safely`: restores unparseable calendar date rejection (`invalid_date`).
5. `test_rejected_bundle_publication_preserves_frozen_versions`: restores assertions for `bundle_version`, `extractor_version`, and `schema_version`.
6. `test_bundle_publication_idempotence_and_collision_conflict`: restores assertion that competing bytes on disk are preserved unmodified (`assert target.read_bytes() == sentinel`).
7. `test_real_fixture_monetary20260916a_extraction` and `test_real_fixture_monetary20260729a_extraction`: removed `pytest.skip`; committed real fixtures are asserted to exist on disk as a mandatory test requirement.
8. Added new regression tests:
   - `test_f1_mandatory_manifest_validation`: non-string URL, float size, null status, contradictory provenance, and transport flags.
   - `test_f2_unsupported_minutes_body_rejected`: discount rate minutes in body rejected.
   - `test_f3_heading_comments_and_duplicate_paragraphs`: heading comment skipping and sibling paragraph disambiguation.
   - `test_f5_same_instant_offset_boundary`: same instant EDT vs +09:00 comparison.
   - `test_f6_temp_symlink_preserves_input`: pre-existing temp symlink preservation.

---

## 6. Verification Results

All verification suites were run on server `amper` using Python runtime `/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3`.

### A. Full Pytest Suite
```text
============================= 240 passed in 14.04s =============================
```
- Total tests: **240 passed**, 0 failed, 0 skipped.
- Pure statement extractor tests: **27 passed** in 1.15s under strict zero-network socket enforcement.
- Test log frozen in: `docs/research/2026-10-19/r3/increment1/revision2/test_run.log`.

### B. Independent Reviewer Harnesses
1. **Root Reviewer Controls (`root_controls/reviewer_controls.py`):**
   - Result: **18 of 18 controls MET (Unmet requirements: [])**.
   - Network attempts: 0.
2. **Publication Controls (`root_controls/publication_controls.py`):**
   - Result: **9 of 9 controls MET (Unmet requirements: [])**.
   - Includes manifest directory protection, temp symlink input preservation, and bundle acquisition verification status visibility.
3. **Provenance & Correspondence (`provenance/f3_f4_correspondence.py`):**
   - Result: **15 of 15 synthetic cases PASSED** (`all_locators_have_one_match=True`, `all_proofs_start_at_selected_node=True`).
   - Both real fixtures: `fresh_matches_frozen=True`, `preview_equals=True`.
4. **Semantics Probe (`semantics/independent_semantics_probe.py`):**
   - Result: **19 of 19 cases PASSED** matching all expected typed rejections, instant offsets, and positive extractions.
5. **Acquisition State Probe (`semantics/acquisition_state_probe.py`):**
   - Result: `explicit_eof_false` and `explicit_complete_false` both rejected with `status="transport_failure"`.
6. **Live Archive Integrity (`regression/verify_archives.py`):**
   - Result: **32 of 32 CAS blobs across all 4 live archives 100% intact and identical**.
   - SQLite `quick_check=ok` across all 4 databases.
7. **Event Dossier Demo Replay (`regression/demo_replay.py`):**
   - Result: **35 of 35 proofs valid**, digest `cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde` exactly replayed.

---

## 7. Frozen Revision 2 Artifacts & Digests

### Published Bundles:
1. **September 16, 2026 Statement:**
   - **File:** `statement_bundle_8d17247a621a9d201152a1e8e4bb736599b294b90a90a8d79c41d64f03c2362b.json`
   - **Bundle Digest:** `8d17247a621a9d201152a1e8e4bb736599b294b90a90a8d79c41d64f03c2362b`
   - **Statement ID:** `stmt-bf54fb45de736e7d`
   - **Paragraphs:** 6
   - **ProofSpans Verified:** 10 / 10 valid (100%)
   - **Preview File:** `statement_preview_monetary20260916a.md`
2. **July 29, 2026 Statement:**
   - **File:** `statement_bundle_29047dec363a3c1fed1a4e9f9d15518be0aee6a6932b1a54190daccf4a4349c9.json`
   - **Bundle Digest:** `29047dec363a3c1fed1a4e9f9d15518be0aee6a6932b1a54190daccf4a4349c9`
   - **Statement ID:** `stmt-f0fa8093be815467`
   - **Paragraphs:** 7 (including dissenting vote paragraph)
   - **ProofSpans Verified:** 11 / 11 valid (100%)
   - **Preview File:** `statement_preview_monetary20260729a.md`

All 21 ProofSpans independently verified against raw HTML fixtures with 100% cryptographic validity.

---

## 8. Limitations & Scope Compliance

1. **Pure Offline Research Extractor:** The extractor processes bounded local HTML fixtures offline and produces research statement bundles. It is not integrated into runtime collectors, event dossiers, paper forecasts, or live trading components.
2. **Historical Acquisition Remains PARTIAL:** Because the historical acquisition script lacked an overall process supervisor and did not clear session cookies on redirects, acquisition verification status is marked PARTIAL. This boundary fix increments extraction safety without altering historical acquisition facts.
3. **Zero Network Compliance:** Network access was completely disabled throughout all test executions and child processes.
4. **Zero Production Mutation:** Production commit `ee6460f`, paper ref `c21967f`, container `pythia-v2`, and all 32 live CAS blobs across `live-r2-run1` through `live-r2-revision3` remained strictly read-only and unmodified.
