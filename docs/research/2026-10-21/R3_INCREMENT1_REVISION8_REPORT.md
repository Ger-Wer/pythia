# Pythia R3.1.8: Research Driver Transport Integrity and Complete Audit Report (Revision 8)

**Date:** 2026-10-21  
**Execution Environment:** `amper` (Ubuntu 24.04, Linux 6.8.0-45-generic x86_64)  
**Worktree:** `/home/pythia/pythia-evidence-r31`  
**Branch:** `codex/evidence-r31-statements`  
**Base Candidate:** `e383a50b4c76924ccaff338bc4750e7138bce668` (R3.1.7 review CHANGES_REQUIRED)  
**Accepted Development Baseline:** `395d288a0ecb23829b0232debb29865a00e6ef38` (`codex/evidence-r3-extraction` after R3.0.2)  
**Python Runtime:** `/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python` (Python 3.10.12)  
**Executor Verdict:** **IMPLEMENTED** (with honest **PARTIAL** on historical acquisition evidence)

---

## 1. Executive Summary

This increment (R3.1.8) resolves the three confirmed transport driver violations (**T1–T3**) identified in architectural review `r317-e383a50` ([`REVIEW_RU.md`](file:///Users/gerwer/Desktop/MemPalace/project/pythia/reviews/2026-10-21/r317-e383a50/REVIEW_RU.md)):
1. **T1 (Complete Compressed Stream EOF):** Implemented streaming decompression verification for supported encodings (`gzip`, `deflate`). The driver now verifies that the decompressor reaches its clean end-of-stream marker (`decompressor.eof is True`) with complete trailers (8-byte CRC32/ISIZE for gzip, 4-byte Adler32 for deflate). Truncated compressed streams are strictly rejected; incomplete raw bytes are never published. Plain and gzip exact 2 MiB (2,097,152 bytes) succeed; 2,097,153 bytes are rejected. Unsupported encodings are cleanly rejected.
2. **T2 (Atomic Publication & Fail-Closed No-Clobber):** Made `no_clobber=True` the mandatory default across both the Python API and CLI. Implemented a comprehensive pre-flight conflict check verifying that *none* of the destination files (raw, manifest, summary, audit, package) already exist before any network attempt is initiated. Replaced unverified direct writes with atomic staging and link/replace (`atomic_publish_bytes`), eliminating truncation and overwrite windows.
3. **T3 (Truthful Audit & Header Capture):** Audit now truthfully separates configuration, observations, and unknown values. Killed or interrupted attempts record unknown fields (`hops_count`, `redirect_count`, `hops`, `final_url`) as `None`/`null` rather than `0` or `False`. `total_hops_executed` is recorded as `None` if any worker was killed (`known_hops_executed` tracked separately). `discrete_eof_verified` is set to `True` only when all targets succeed with proven discrete EOF. Outgoing request header names are captured directly from the prepared request (`Host`, `User-Agent`, `Accept`, `Accept-Encoding`, `Connection`), and absence of `Cookie` and `Authorization` headers is verified and recorded per hop.
4. **Factual Narrative Correction (Raw Comparison):** Clarified the comparison between raw snapshots. Core substantive statement paragraphs (paragraphs 0–3 for Sep 16, paragraphs 0–4 for Jul 29) are 100% byte-identical in raw HTML quotes and decoded display text. All display texts and paragraph offsets match 100%. Raw markup differences within `#article` are accurately attributed to Cloudflare email-protection tokens (`/cdn-cgi/l/email-protection#...`) in share-links and the ancillary media inquiries paragraph (`paragraph_index: 4`), in addition to head Zaraz nonces and footer token scripts.

**Preservation & Invariants:**
- **Product Code Untouched:** 0 lines of product code (`fed_statement_extract.py`, etc.) or core tests modified. Parser checkpoint `11e7cb74b58133fe9c404a971bd6fbab7cf7372b` remains PASS, F1–F6 closed.
- **Historical Artifacts Untouched:** Revisions 1 through 7 remain 100% immutable.
- **All 32 Original CAS Blobs Intact:** Verified healthy before and after demo execution across all 4 live archives (`live-r2-run1`, `live-r2-revision1`, `live-r2-revision2`, `live-r2-revision3`) via `mode=ro`.
- **Parent Dossier Digest Confirmed:** Digest `cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde` verified.
- **Accepted Development Baseline:** Remains **`395d288a0ecb23829b0232debb29865a00e6ef38`**. Entire R3 remains `IN_PROGRESS`; R3.1 is not accepted; R3.2 is **NOT opened**.
- **Historical Evidence:** Explicitly and honestly preserved as **`PARTIAL`** (`usable_for_forecast = False`).

---

## 2. Before / After Analysis for T1–T3

### T1: Content Decoding and Stream EOF
- **Before (R3.1.7):** `bounded_research_driver.py` used `resp.iter_content()`. Because urllib3's decompressor does not raise an error when a stream is missing its trailer, truncated gzip (`gzip.compress(body)[:-8]`) and truncated deflate (`zlib.compress(body)[:-4]`) both silently ended, yielding 57 bytes of decoded text, `eof_complete=True`, and `transport_outcome="success"`.
- **After (R3.1.8):** `decode_http_stream(resp, max_bytes)` reads undecoded wire chunks (`resp.raw.read(decode_content=False)`) and feeds them into explicit `zlib.decompressobj(16 + zlib.MAX_WBITS)` (for gzip) or `zlib.decompressobj()` (for deflate). At stream end, `decompressor.flush()` is called and `decompressor.eof is True` is strictly verified. If `not decompressor.eof`, a `TransportDecodeError` is raised, `eof_complete` remains `False`, `transport_outcome` becomes `"failure"`, and no raw file is written. Plain/gzip exact 2,097,152 bytes pass; 2,097,153 bytes raise `BodyLimitExceededError`. Unsupported encodings (e.g. `br`, `zstd`) raise `TransportError`.

### T2: Artifact Safety and No-Clobber
- **Before (R3.1.7):** `no_clobber` was `False` by default. Re-running the driver replaced all 5 output files. When `no_clobber=True` was passed, only `output_raw_path.exists()` was checked; if raw was absent but metadata files existed, `acquisition_audit.json` and manifests were silently overwritten. Publications used unverified direct file writes.
- **After (R3.1.8):** `no_clobber=True` is the default parameter across API and CLI. `check_preflight_conflicts(output_dir, targets, no_clobber=True)` checks all candidate output paths (raw files, manifests, summary, audit, package) before any network call or process launch; if any exists, it immediately fails closed with `FileExistsError`. All file publications use `atomic_publish_bytes` via atomic staging and `os.link` / `os.replace`, guaranteeing safe, atomic publication with zero truncation or overwrite window.

### T3: Truthful Audit Reporting & Actual Headers
- **Before (R3.1.7):** When a worker was killed by supervisor timeout, the audit recorded `hops_count=0`, `redirect_count=0`, and `discrete_eof_verified=True` in proven conditions, falsely claiming zero hops occurred and EOF was verified. Outgoing header names were copied from a manual 3-element dict (`User-Agent`, `Accept`, `Accept-Encoding`), ignoring `Host` and `Connection`. The report text also referenced `urllib` and `multiprocessing` instead of the actual `requests` and `subprocess` implementation.
- **After (R3.1.8):** When a worker is killed, unknown fields (`hops_count`, `redirect_count`, `hops`, `final_url`) are recorded as `None` (`null`). In execution summary, `total_hops_executed` is set to `None` if any worker was killed (`known_hops_executed` tracked separately). `discrete_eof_verified` is set to `True` only when all targets succeed with proven discrete EOF; on killed or failed attempts, it is `False`. The request now sets `Host: parsed.netloc`, and outgoing header names are read from `resp.request.headers`, recording all 5 actual names (`Accept`, `Accept-Encoding`, `Connection`, `Host`, `User-Agent`). Absence of `Cookie` and `Authorization` headers is verified and recorded per hop.

---

## 3. Hermetic Driver Test Suite (`test_bounded_research_driver.py`)

A comprehensive hermetic offline test suite was executed under strict socket guard (forbidding external DNS/connect):

```bash
$ PYTHONPATH=/home/pythia/pythia-evidence-r31 /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python -m pytest docs/research/2026-10-21/r3/increment1/revision8/acquisition/test_bounded_research_driver.py -v
```

### Results (13/13 PASS in 7.93s)
| Test Name | Focus | Status |
| :--- | :--- | :--- |
| `test_supervisor_kills_hung_worker` | Hard 0.3s deadline terminates stalled worker via SIGKILL, no orphans | **PASSED** |
| `test_set_cookie_isolation_on_redirect` | Set-Cookie redacted; Cookie header NOT sent on redirect hop | **PASSED** |
| `test_offsite_redirect_rejected_before_request` | Offsite redirect rejected before issuing request | **PASSED** |
| `test_third_redirect_rejected_before_request` | Third redirect rejected before request (max 2) | **PASSED** |
| `test_gzip_valid_and_missing_trailer` | Valid gzip succeeds; truncated 8-byte trailer rejected, raw not written | **PASSED** |
| `test_deflate_valid_and_missing_trailer` | Valid deflate succeeds; truncated 4-byte trailer rejected, raw not written | **PASSED** |
| `test_unsupported_encoding_rejected` | Unsupported encoding (e.g. `br`) rejected cleanly with TransportError | **PASSED** |
| `test_decoded_body_exact_limit_and_overflow` | Exact 2 MiB succeeds; 2 MiB + 1 byte rejected with overflow error | **PASSED** |
| `test_default_publication_fails_closed_on_existing` | Default run fails closed before requests if output exists; files unchanged | **PASSED** |
| `test_explicit_no_clobber_metadata_only` | Existing metadata without raw causes pre-flight rejection; sentinels intact | **PASSED** |
| `test_killed_audit_truthful_reporting` | Killed worker records unknown hops as None; discrete_eof_verified=False | **PASSED** |
| `test_outgoing_headers_actual_recording` | All 5 actual header names recorded in audit; zero auth/cookie sent | **PASSED** |
| `test_url_gate_invalids` | Invalid schemes, ports, userinfo, and offsite domains rejected | **PASSED** |

Log saved to: `docs/research/2026-10-21/r3/increment1/revision8/acquisition/driver_test_run.log`.

---

## 4. Live Capture Execution Evidence (Revision 8)

A single bounded live run was executed on `amper`:
- **Driver Version:** `pythia-bounded-research-driver-r318/1.0`
- **Run ID:** `run-20261021-073720-386978`
- **Execution UTC:** `2026-10-21T07:37:20.761585+00:00` to `2026-10-21T07:37:21.448538+00:00`
- **Total Duration:** 687.0 ms
- **Total Attempts Made:** 2 (budget: max 4)
- **Total Hops Executed:** 2
- **Killed Workers:** 0
- **Fresh Transport Verdict:** `VERIFIED_BOUNDED_RESEARCH`

### Attempt 1: September 16, 2026 (`monetary20260916a`)
- **Target URL:** `https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm`
- **HTTP Status:** 200 OK
- **Duration:** 178.9 ms
- **Decoded Bytes:** 84,741 bytes
- **Raw SHA-256:** `e3308825cc5285fe31df36d5f0abff5b96275758ceb038b3658ba6c6198cf314`
- **EOF Complete:** `true` (gzip decompressor trailer CRC32/ISIZE verified)
- **Outgoing Headers (Actual 5):** `Accept`, `Accept-Encoding`, `Connection`, `Host`, `User-Agent`
- **Credentials/Cookies Sent:** None (`cookie_headers_sent: false`, `credential_headers_sent: false`)
- **Response Headers:** `CF-Cache-Status: HIT`, `Content-Encoding: gzip`, `set-cookie: [REDACTED_IGNORED]`
- **Parent Feed Reference:** `live-r2-revision3`, fact `fact-b7cdc057cc6ec28d`, locator `/rss/channel/item[3]`

### Attempt 2: July 29, 2026 (`monetary20260729a`)
- **Target URL:** `https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm`
- **HTTP Status:** 200 OK
- **Duration:** 212.3 ms
- **Decoded Bytes:** 83,318 bytes
- **Raw SHA-256:** `4561cbc921a36cbc61fdd90118d7c703cf73866e73f5781d1c4bb31057770195`
- **EOF Complete:** `true` (gzip decompressor trailer CRC32/ISIZE verified)
- **Outgoing Headers (Actual 5):** `Accept`, `Accept-Encoding`, `Connection`, `Host`, `User-Agent`
- **Credentials/Cookies Sent:** None (`cookie_headers_sent: false`, `credential_headers_sent: false`)
- **Response Headers:** `CF-Cache-Status: REVALIDATED`, `Content-Encoding: gzip`, `set-cookie: [REDACTED_IGNORED]`
- **Parent Feed Reference:** `live-r2-revision3`, fact `fact-23bc4c0fc0243b9e`, locator `/rss/channel/item[7]`

---

## 5. Raw Byte & Substantive Content Comparison (R7 vs R8)

| Fixture Key | R7 Raw Size | R8 Raw Size | Delta | R7 Raw SHA-256 | R8 Raw SHA-256 | Core Statement Raw Parity | Display Text Parity |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `monetary20260916a` | 84,741 B | 84,741 B | 0 B | `83ed7421cdc9a179...` | `e3308825cc5285fe...` | **100% Identical** (paras 0–3) | **100% Identical** (all 6) |
| `monetary20260729a` | 83,318 B | 83,318 B | 0 B | `09e038db50283fc9...` | `4561cbc921a36cbc...` | **100% Identical** (paras 0–4) | **100% Identical** (all 7) |

### Accurate Difference Cause Breakdown
Contiguous range analysis confirms that differences between R7 and R8 are strictly localized to dynamic Cloudflare edge tokens:
1. `<head>` Zaraz Script: Nonce rotates per response (`nonce="f41030ac..."` vs `nonce="8688291e..."` around byte 2822).
2. Page Footer Script: Cloudflare challenge token params rotate (`window.__CF$cv$params={r:'...',t:'...'}` around bytes 82540 / 83970).
3. Inside `#article`:
   - Share link: `/cdn-cgi/l/email-protection#...` token rotates around bytes 71270 / 72770.
   - Ancillary media inquiries paragraph (`paragraph_index: 4`): `/cdn-cgi/l/email-protection#...` and `data-cfemail="..."` rotate around bytes 73240 / 74650.
4. Core Substantive Statements:
   - Paragraphs 0 to 3 for Sep 16 (vote approval, policy decision, economic assessment, inflation mandate) are **100% byte-identical** in raw HTML quotes and decoded text.
   - Paragraphs 0 to 4 for Jul 29 are **100% byte-identical** in raw HTML quotes and decoded text.
   - All paragraph structural offsets and decoded display texts across all 6 and 7 paragraphs match 100%.

---

## 6. Extraction, Proof Verification & Published Bundles

Statement extraction was performed directly on fresh Revision 8 raw bytes using the unchanged parser (`11e7cb74`):

### September 16, 2026 (`monetary20260916a`)
- **Statement ID:** `stmt-624a85f06555c44a`
- **Title:** `Federal Reserve issues FOMC statement`
- **Published At:** `2026-09-16T14:00:00-04:00`
- **Paragraphs:** 6
- **ProofSpans:** 10/10 verified 100% byte-exact against fresh raw bytes.
- **Bundle File:** `statement_bundle_b91d219e930feca2589db16fa6377e844082b0a255148e3ae851632888a8c829.json`
- **Bundle Digest:** `b91d219e930feca2589db16fa6377e844082b0a255148e3ae851632888a8c829`
- **Preview File:** `statement_preview_monetary20260916a.md`
- **Companion Note:** `statement_preview_monetary20260916a_note.md`

### July 29, 2026 (`monetary20260729a`)
- **Statement ID:** `stmt-62a1319a62cf52eb`
- **Title:** `Federal Reserve issues FOMC statement`
- **Published At:** `2026-07-29T14:00:00-04:00`
- **Paragraphs:** 7
- **ProofSpans:** 11/11 verified 100% byte-exact against fresh raw bytes.
- **Bundle File:** `statement_bundle_e38f5c0738e03c03809eb6b9961c623bbf8148b91d002e17da908b58623c90c6.json`
- **Bundle Digest:** `e38f5c0738e03c03809eb6b9961c623bbf8148b91d002e17da908b58623c90c6`
- **Preview File:** `statement_preview_monetary20260729a.md`
- **Companion Note:** `statement_preview_monetary20260729a_note.md`

---

## 7. Offline Demonstration & Verification (`run_demo.py`)

The demonstration script `docs/research/2026-10-21/r3/increment1/revision8/run_demo.py` was executed under strict zero-network guard:

```bash
$ PYTHONPATH=/home/pythia/pythia-evidence-r31 /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python docs/research/2026-10-21/r3/increment1/revision8/run_demo.py
```

### Execution Log Output
```
======================================================================
Pythia R3.1.8: Offline FOMC Statement Extractor Demonstration (Revision 8)
======================================================================

[Step 1] Verifying 32 blobs across 4 live archives BEFORE demo (mode=ro)...
Total blobs verified: 32; Healthy: True
All 32 original CAS blobs across all 4 live archives are 100% intact.

[Step 1b] Verifying Bounded Research Acquisition Package & Audit (Revision 8)...
  Acquisition package verified: fresh=VERIFIED_BOUNDED_RESEARCH, audit_sha=6c9a386f806f0399...

[Step 2] Processing Real Official FOMC Statements...

  --- Extracting monetary20260916a ---
  All 10 ProofSpans verified against raw HTML blob (100% valid).
  Structural locators verified: unambiguous (cardinality 1) for container and all 6 paragraphs.
  Bundle published: statement_bundle_b91d219e930feca2589db16fa6377e844082b0a255148e3ae851632888a8c829.json (digest: b91d219e930feca2589db16fa6377e844082b0a255148e3ae851632888a8c829)
  Preview published: statement_preview_monetary20260916a.md
  Companion note verified: statement_preview_monetary20260916a_note.md

  --- Extracting monetary20260729a ---
  All 11 ProofSpans verified against raw HTML blob (100% valid).
  Structural locators verified: unambiguous (cardinality 1) for container and all 7 paragraphs.
  Bundle published: statement_bundle_e38f5c0738e03c03809eb6b9961c623bbf8148b91d002e17da908b58623c90c6.json (digest: e38f5c0738e03c03809eb6b9961c623bbf8148b91d002e17da908b58623c90c6)
  Preview published: statement_preview_monetary20260729a.md
  Companion note verified: statement_preview_monetary20260729a_note.md

[Step 3] Running Residual Boundary Controls (F1 - F6)...
  Testing F1: Typed Manifest Validation & Lineage Checks...
  Testing F2: Statement Profile & Body Gating...
  Testing F3: Comment Closing Tag Boundaries...
  Testing F4: Single Entity HTML Decoding...
  Testing F5: Temporal & Date Controls...
  Testing F6: Destination Directory Safety & Symlink Protection...

[Step 4] Verifying 32 blobs across 4 live archives AFTER demo (mode=ro)...
Total blobs verified: 32; Healthy: True
All 32 original CAS blobs across all 4 live archives are 100% intact and unchanged.

[Step 5] Demo Summary written to: /home/pythia/pythia-evidence-r31/docs/research/2026-10-21/r3/increment1/revision8/demo_summary.json

======================================================================
R3.1.8 REVISION 8 DEMO COMPLETED SUCCESSFULLY (100% PASS, ZERO NETWORK)
======================================================================
```

---

## 8. Test Suites Summary

1. **Hermetic Driver Suite (`test_bounded_research_driver.py`):** 13 passed in 7.93s (`driver_test_run.log`).
2. **Strict Statement Suite (`test_fed_statement_extract.py`):** 43 passed in 1.07s.
3. **Full Evaluation Suite (`pytest python/evaluation/tests -v`):** 256 passed in 11.12s (`test_run.log`).
4. **All 32 CAS Blobs:** Verified intact before and after demo execution.

---

## 9. Artifact Manifest (Revision 8)

All generated artifacts are immutable and isolated under:
`docs/research/2026-10-21/r3/increment1/revision8/`

```
revision8/
├── acquisition/
│   ├── bounded_research_driver.py       # Standalone supervisor & capture driver (v1.0 / R3.1.8)
│   ├── test_bounded_research_driver.py  # Hermetic offline test suite (13/13 PASS)
│   ├── driver_test_run.log              # Pytest execution log for driver tests
│   ├── acquisition_audit.json           # Comprehensive attempt & condition audit trail
│   └── acquisition_package.json         # Package metadata linking URLs, SHAs, digests
├── fixtures/
│   ├── raw_monetary20260916a.html       # 84,741 bytes raw HTML blob
│   ├── raw_monetary20260729a.html       # 83,318 bytes raw HTML blob
│   ├── manifest_monetary20260916a.json  # Parser manifest with verified transport metadata
│   ├── manifest_monetary20260729a.json  # Parser manifest with verified transport metadata
│   └── fixtures_summary.json            # Consolidated fixture summary
├── bundles/
│   ├── statement_bundle_b91d219e930feca2589db16fa6377e844082b0a255148e3ae851632888a8c829.json
│   └── statement_bundle_e38f5c0738e03c03809eb6b9961c623bbf8148b91d002e17da908b58623c90c6.json
├── previews/
│   ├── statement_preview_monetary20260916a.md      # Human-readable Markdown preview
│   ├── statement_preview_monetary20260916a_note.md # Companion note (fresh vs historical)
│   ├── statement_preview_monetary20260729a.md      # Human-readable Markdown preview
│   └── statement_preview_monetary20260729a_note.md # Companion note (fresh vs historical)
├── demo_summary.json                    # Full demo execution summary & control results
├── run_demo.py                          # Offline demonstration script
└── test_run.log                         # Full 256-test suite log
```

---

## 10. Architectural Boundaries & Verdict

- **Status Verdict:** **IMPLEMENTED** (T1–T3 Transport Driver Defects Resolved & Bounded Research Capture Completed).
- **Historical Evidence Invariant:** Historical acquisition from 2026-10-19 remains strictly and honestly declared as **`PARTIAL`** (`usable_for_forecast = False`). Fresh capture proves the pages are retrievable under strict supervisor and zero-credential isolation with proven compressed EOF, but does not retroactively rewrite historical transport records.
- **Development Baseline:** Accepted development baseline remains **`395d288a0ecb23829b0232debb29865a00e6ef38`**.
- **Lifecycle Status:** Entire R3 is `IN_PROGRESS`. R3.1 is not unilaterally accepted; R3.2 is **NOT opened**.
- **Next Step:** Awaiting independent architectural review.
