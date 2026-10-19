# Pythia R3.1.3: Final Statement Contract Boundaries Fix Report (Revision 3)

**Date:** 2026-10-19  
**Execution Environment:** `amper` (Ubuntu 24.04, Linux 6.8.0-45-generic x86_64)  
**Worktree:** `/home/pythia/pythia-evidence-r31`  
**Branch:** `codex/evidence-r31-statements`  
**Base Commit:** `395d288a0ecb23829b0232debb29865a00e6ef38` (`codex/evidence-r3-extraction` after R3.0.2)  
**Parent Candidate Under Review:** `148dd7a8a7bccf9e9555d7f1a48d97d814f4f9c8` (R3.1.2 CHANGES_REQUIRED)  
**Python Runtime:** `/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3` (Python 3.10.12)  
**Executor Verdict:** **IMPLEMENTED** (with honest **PARTIAL** on historical acquisition evidence)

---

## 1. Executive Summary

This increment (R3.1.3) resolves the confirmed residues of **F1, F2, and F3** from independent review `r312-148dd7a` ([`REVIEW_RU.md`](file:///Users/gerwer/Desktop/MemPalace/project/pythia/reviews/2026-10-19/r312-148dd7a/REVIEW_RU.md)), while maintaining all previously closed contracts (**F4** single-entity decoding, **F5** comparable precision instant comparison, **F6** destination directory and temp file collision protection) and preserving all test coverage across the entire test suite.

All code modifications, test runs, independent probe validations, and bundle publications were executed exclusively on server `amper` under zero-network socket enforcement. The accepted development baseline remains `395d288a0ecb23829b0232debb29865a00e6ef38`.

All previous artifacts and reports in `docs/research/2026-10-19/r3/increment1/` (`initial/`, `revision1/`, `revision2/`, `R3_INCREMENT1_REPORT.md`, `R3_INCREMENT1_REVISION1_REPORT.md`, `R3_INCREMENT1_REVISION2_REPORT.md`) are preserved completely intact. All newly generated revision 3 artifacts reside in **`docs/research/2026-10-19/r3/increment1/revision3/`**.

---

## 2. Narrative Erratum Explanation

In the previous delivery message for R3.1.2, a narrative copy-paste error occurred:
- **Erroneous message snippet:** The message cited an excerpt beginning with `"Recent indicators suggest..."`, span `[22153, 22448]`, and mistakenly quoted the bundle digest `8d17247...` as the raw HTML blob SHA-256. (At byte position 22,153 in the raw HTML, the page actually contains top navigation menu items for "Videos").
- **Actual committed data:** The committed raw HTML, published bundles, and markdown previews on disk were, and have always been, 100% correct and deterministic:
  - Raw HTML Blob SHA-256: `8252bf02066af1c379bdcad3b9b70cc8cdbf8e21183eb3f00c8df0a2dba9391f` (84,741 bytes)
  - Paragraph 0 (`vote_approval`): `"The Federal Open Market Committee approved the following statement for release by a 12 – 0 vote:"` at raw byte span `[73647:73752]`
  - Paragraph 1 (`policy_decision`): `"The Committee decided to raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent..."` at raw byte span `[73756:74060]`
  - Paragraph 2 (`economic_assessment`): `"Economic activity is expanding at a solid pace..."` at raw byte span `[74064:74410]`
- **Resolution:** No underlying data files were corrupted. Below is the direct, unedited excerpt from the freshly published `statement_preview_monetary20260916a.md` in `revision3`:

```markdown
# FOMC Statement Research Preview

- **Bundle Digest:** `376d7e6dcdcd710b7853ec2e5e3ebdd2b63853989eb4746d554010d500892bc4`
- **Extractor Version:** `pythia-statement-extractor-r3/0.1.3`
- **Provenance Class:** `research_fixture`
- **Acquisition Verification Status:** `PARTIAL`
- **Usable For Forecast:** `False`

## Document Header
- **Statement ID:** `stmt-bf54fb45de736e7d`
- **Issuer:** Federal Reserve Board
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

- *Locator:* `#article > div.col-xs-12.col-sm-8.col-md-8:not(.heading) > p:nth-of-type(1)`
- *Raw Byte Slice:* `[73647:73752]` (105 bytes, SHA: `ebc203933f0f...`)

### Paragraph 1 [`policy_decision`]
> The Committee decided to raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent, in support of the Federal Reserve's dual mandate. The Committee is continuing its policy of maintaining ample reserves in the banking system.

- *Locator:* `#article > div.col-xs-12.col-sm-8.col-md-8:not(.heading) > p:nth-of-type(2)`
- *Raw Byte Slice:* `[73756:74060]` (304 bytes, SHA: `ce16d2e4c5d1...`)
```

---

## 3. Honest Acquisition Status & Boundaries

- **Historical Acquisition Evidence:** Remains honestly marked as **`PARTIAL`**.
- **Transport Decoupling:** The extractor operates as an offline deterministic parser over saved raw bytes. Parser success is strictly decoupled from runtime acquisition claims and does not constitute evidence of live transport verification.
- **Usability Constraint:** `usable_for_forecast = False` across all manifests, bundles, and previews.
- **Zero Runtime Claims:** Synthetic fixtures honestly declare `acquisition_verification_status = "NOT_APPLICABLE_SYNTHETIC"`, and notes clearly specify offline synthetic testing without claiming live fetch or bounded research acquisition.

---

## 4. Detailed Resolutions for R3.1.3 Residues (F1, F2, F3)

### F1: Mandatory Issuer, Typed Parent Lineage, and Final URL Profile Matching
1. **Mandatory Issuer (No Invented Defaults):**
   - Extractor requires `issuer` to be present and a non-empty string belonging to `ALLOWED_ISSUERS` (`"Federal Reserve Board"`, `"Federal Open Market Committee"`).
   - If missing, empty, or null, extraction fails closed with typed rejection `status="invalid_manifest"`, `reason="Manifest must contain non-empty string 'issuer' (inventing default issuer from missing data is forbidden)"`.
2. **Typed Parent Lineage Validation:**
   - Real manifests must contain a non-empty dictionary with typed lineage fields (`feed_fact_id`, `feed_blob_sha256`, `feed_item_locator`, and either `receipt_id`/`revision_id` or `archive_name`/`parent_bundle_digest`).
   - Dummy references such as `{"label": "not a feed reference"}` are rejected with `status="invalid_manifest"`.
   - Explicit synthetic fixtures declaring `{"fixture_kind": "synthetic_fixture"}` are accepted without fabricating fake archive hashes.
3. **Dual URL Profile Matching (`source_url` & `final_url`):**
   - Both `source_url` and `final_url` are checked against the FOMC statement prefix `/newsevents/pressreleases/monetary`.
   - Manifests with non-statement paths (e.g. `final_url = "https://www.federalreserve.gov/aboutthefed.htm"`) are rejected with `status="unsupported_source_url"`.
4. **Honest Synthetic Metadata:**
   - Synthetic fixtures produce bundles with `acquisition_verification_status="NOT_APPLICABLE_SYNTHETIC"` and `notes="Synthetic research fixture for offline test and parser verification. Not acquired from live web or runtime collector; no live acquisition claims."`.

### F2: Statement Profile & Substantive Body Content Gating
1. **Task Force & Non-Statement Body Gating:**
   - Gated unsupported document bodies (e.g. task forces on bank supervision, leadership/governance announcements, enforcement actions, advisory council reports, testimonies, joint releases, and discount rate minutes) that might share valid statement skeletons.
   - Example `<p>The Federal Reserve Board announced the formation of a task force on bank supervision...</p>` returns typed rejection `status="unsupported_document_type"`.
2. **Fail Closed on Ambiguity:**
   - Ensures that only genuine policy decisions, rate announcements, and official economic assessments pass extraction.

### F3: Exact Closing Tag Boundaries & Full Text Containment
1. **Comment-Skipping Tag Matching (`_find_closing_tag`):**
   - Implemented `_find_closing_tag(raw_bytes, open_pos, tag_name)` which parses closing tags while skipping HTML comments (`<!-- ... -->`).
   - Prevents truncated spans when comments contain closing tags, e.g. `<p>The Committee decided <!-- </p> --> to maintain the range at 4 to 5 percent.</p>`.
   - Preserves the complete substantive statement text and ensures full text correspondence (`display_text == BeautifulSoup(str(p_dom)).get_text()`).
2. **Container Span Containment:**
   - Correctly matches the container `<div>` closing tag even if comments precede it (`<!-- </div> -->`).
   - Verifies that all paragraph byte spans are strictly contained within `[container.byte_start, container.byte_end]`.
3. **Real HTML Compatibility:**
   - Handled the Fed's unclosed `<p class="releaseTime">` followed directly by `<ul>` with a safe fallback, ensuring byte-exact parity on historical frozen proofs `[69275:73752]`.

---

## 5. Verification Matrix (F1, F2, F3)

| Control Case | Expected Result | Actual Result | Status |
|:---|:---|:---|:---:|
| `positive_real_parser_partial` | Extracted; machine/preview PARTIAL | Extracted; `acquisition_verification_status: PARTIAL` | **PASS** |
| `f1_missing_issuer_parent` | Reject missing real provenance; no invented issuer | `status="invalid_manifest"`, reason notes missing issuer | **PASS** |
| `f1_null_issuer` | Reject null issuer | `status="invalid_manifest"`, reason notes missing issuer | **PASS** |
| `f1_nontyped_nonempty_parent` | Reject missing typed feed linkage | `status="invalid_manifest"`, missing typed lineage fields | **PASS** |
| `f1_final_nonstatement_page` | Reject final identity incompatible with profile | `status="unsupported_source_url"`, path `/aboutthefed.htm` | **PASS** |
| `positive_explicit_synthetic` | Extracted; honest synthetic provenance | Extracted; `NOT_APPLICABLE_SYNTHETIC`, no fetch claim | **PASS** |
| `f2_taskforce_body_under_skeleton` | Reject unsupported document type | `status="unsupported_document_type"` (`task force`) | **PASS** |
| `inline_comment_closing_p` | Full text preserved, no truncation | Full text `"The Committee decided to maintain..."`, proof valid | **PASS** |
| `inline_comment_closing_div` | Container covers all paragraph spans | `container_covers_all_paragraph_spans: true` | **PASS** |

---

## 6. Execution Command Log & Reproducible Output

All commands run on server `amper` via SSH:

### 1. Demonstration Script (`run_demo.py`)
```bash
$ /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 docs/research/2026-10-19/r3/increment1/revision3/run_demo.py
```
**Output:**
```
======================================================================
Pythia R3.1.3: Offline FOMC Statement Extractor Demonstration (Revision 3)
======================================================================

[Step 1] Verifying 32 blobs across 4 live archives BEFORE demo...
Total blobs verified: 32; Healthy: True
All 32 original CAS blobs across all 4 live archives are 100% intact.

[Step 2] Processing Real Official FOMC Statements...

  --- Extracting monetary20260916a ---
  All 10 ProofSpans verified against raw HTML blob (100% valid).
  Structural locators verified: unambiguous (cardinality 1) for container and all 6 paragraphs.
  Bundle published: statement_bundle_376d7e6dcdcd710b7853ec2e5e3ebdd2b63853989eb4746d554010d500892bc4.json (digest: 376d7e6dcdcd710b7853ec2e5e3ebdd2b63853989eb4746d554010d500892bc4)
  Preview published: statement_preview_monetary20260916a.md

  --- Extracting monetary20260729a ---
  All 11 ProofSpans verified against raw HTML blob (100% valid).
  Structural locators verified: unambiguous (cardinality 1) for container and all 7 paragraphs.
  Bundle published: statement_bundle_225567ae364d195532ca354d021dd9470ade500861e34c861878efa1dafb0d8d.json (digest: 225567ae364d195532ca354d021dd9470ade500861e34c861878efa1dafb0d8d)
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

[Step 5] Demo Summary written to: docs/research/2026-10-19/r3/increment1/revision3/demo_summary.json

======================================================================
R3.1.3 REVISION 3 DEMO COMPLETED SUCCESSFULLY (100% PASS, ZERO NETWORK)
======================================================================
```
**Exit Code:** 0

### 2. Full Test Suite Execution
```bash
$ PYTHONPATH=/home/pythia/pythia-evidence-r31 /home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests -v
```
**Output Summary:**
```
============================= 247 passed in 14.07s =============================
```
**Exit Code:** 0

### 3. Independent Reviewer Probes Verification
- `bundle_replay.py`: `committed_bundle_identical: true`, `committed_preview_identical: true`, `network_attempts: []`.
- `check_corrected_frozen.py`: `all_proofs_valid: true`, `preview_has_partial: true`, `dom_paragraphs_equal: true`.
- `remaining_contract_probe.py`: 9 of 9 probes passed.
- `f3_f4_correspondence.py`: 17 of 17 synthetic cases passed (including comment `</p>` and comment `</div>`).
- `publication_controls.py`: 9 of 9 controls met (`unmet_requirements: []`).
- `reviewer_controls.py`: 18 of 18 controls met (`unmet_requirements: []`).

---

## 7. Published Artifact Digests (Revision 3)

- **Extractor Version:** `pythia-statement-extractor-r3/0.1.3`
- **Bundle Version:** `pythia-statement-bundle-r3/0.1.3`
- **Schema Version:** `pythia-statement-schema-r3/1.2`
- **September 16, 2026 Statement:**
  - Raw HTML Blob SHA-256: `8252bf02066af1c379bdcad3b9b70cc8cdbf8e21183eb3f00c8df0a2dba9391f` (84,741 bytes)
  - Bundle File: `statement_bundle_376d7e6dcdcd710b7853ec2e5e3ebdd2b63853989eb4746d554010d500892bc4.json`
  - Bundle Digest: `376d7e6dcdcd710b7853ec2e5e3ebdd2b63853989eb4746d554010d500892bc4`
  - Total ProofSpans: 10 (all 10 verified valid)
- **July 29, 2026 Statement:**
  - Raw HTML Blob SHA-256: `f3cfa831daa2e63b0c370bd0de3b58cccc30439ee3e8378bc0a6c2dbf650ef49` (83,318 bytes)
  - Bundle File: `statement_bundle_225567ae364d195532ca354d021dd9470ade500861e34c861878efa1dafb0d8d.json`
  - Bundle Digest: `225567ae364d195532ca354d021dd9470ade500861e34c861878efa1dafb0d8d`
  - Total ProofSpans: 11 (all 11 verified valid)

---

## 8. Summary of Status & Architectural Boundaries

- **R3.1.3 Scope:** Strictly confined to F1, F2, F3 boundary defect fixes.
- **Accepted Baseline:** `395d288a0ecb23829b0232debb29865a00e6ef38` remains intact.
- **R3 Status:** `IN_PROGRESS`. R3.1 is not unilaterally declared completed, and R3.2 is **NOT opened**.
- **Action Required:** Stand by for independent architectural review.
