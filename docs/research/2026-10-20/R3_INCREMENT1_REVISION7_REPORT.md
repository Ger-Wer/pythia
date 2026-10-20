# Pythia R3.1.7: Bounded Research Re-Acquisition and Transport Audit Report (Revision 7)

**Date:** 2026-10-20  
**Execution Environment:** `amper` (Ubuntu 24.04, Linux 6.8.0-45-generic x86_64)  
**Worktree:** `/home/pythia/pythia-evidence-r31`  
**Branch:** `codex/evidence-r31-statements`  
**Base Candidate:** `11e7cb74b58133fe9c404a971bd6fbab7cf7372b` (R3.1.6 review PASS)  
**Accepted Development Baseline:** `395d288a0ecb23829b0232debb29865a00e6ef38` (`codex/evidence-r3-extraction` after R3.0.2)  
**Python Runtime:** `/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python` (Python 3.10.12)  
**Status Verdict:** **IMPLEMENTED** (with honest **PARTIAL** on historical acquisition evidence)

---

## 1. Executive Summary

This increment (R3.1.7) executes a **bounded research capture of the exact two official Fed statement pages with a complete audit of capture conditions**, resolving the transport verification gaps identified in review `r316-11e7cb7` ([`REVIEW_RU.md`](file:///Users/gerwer/Desktop/MemPalace/project/pythia/reviews/2026-10-20/r316-11e7cb7/REVIEW_RU.md)).

### Architectural Invariants Honored
1. **Parser Checkpoint Untouched:** Offline parser checkpoint `11e7cb74b58133fe9c404a971bd6fbab7cf7372b` passed independent review (F1–F6 closed, 256 tests, 43 strict statement tests, nullable 24/24, finite 64/64, root 8/8). Zero lines of product code (`fed_statement_extract.py`, etc.) or core tests were modified.
2. **Scope Confined to Standalone Research Driver:** Implementation is strictly confined to a standalone driver under `docs/research/2026-10-20/r3/increment1/revision7/acquisition/`. No runtime collector, schema, CLI, or R2 framework changes were made.
3. **Hard Supervisor & Strict Transport:**
   - External parent process supervisor enforcing 20s hard deadline per attempt via `SIGKILL`.
   - Anonymous HTTPS with strict TLS verification (`trust_env=False`, no ambient credentials/netrc/cookies).
   - Strict cookie isolation: `Cookie`, `Authorization`, and `Proxy-Authorization` headers are never sent on any hop. Any incoming `Set-Cookie` is detected, redacted, and never stored or forwarded.
   - Strict redirect control: Max 2 redirects per attempt, HTTPS `federalreserve.gov` / `www.federalreserve.gov` port 443 only, userinfo forbidden, pre-checked before request.
   - Decoded body limit: Max 2 MiB, verified with discrete `eof_complete: true`.
   - Strict attempt budget: Max 2 attempts per URL, max 4 total attempts, stop upon success, max 100s total supervisor budget.
4. **Hermetic Test Suite (8/8 PASS):** Driver verified via offline hermetic mock suite with socket guard blocking external network. Tests verify hung worker termination, cookie isolation, redirect rejection, body limit enforcement, incomplete EOF detection, budget enforcement, and audit immutability.
5. **Single Live Capture Run:**
   - Both target URLs acquired in a single bounded execution on `amper`:
     - `monetary20260916a`: HTTP 200, 84,741 bytes, SHA-256 `83ed7421cdc9a17999bb44006015e24e1410f0464f59fc3ddeb955907b58e263`, `eof_complete: true`, duration 202.5 ms.
     - `monetary20260729a`: HTTP 200, 83,318 bytes, SHA-256 `09e038db50283fc9e6b615a39cbff696b18ab6adf1d623e12dc22931c88f5074`, `eof_complete: true`, duration 198.5 ms.
   - Budget used: 2 attempts, 2 hops, 0 redirects, 0 worker kills, total runtime 696.7 ms.
6. **Statement Extraction & Proof Verification:**
   - `monetary20260916a`: 6 paragraphs, 10/10 ProofSpans 100% valid, bundle digest `21053da4ab7d0748544eaea69bb531af89ad0fa4d8ee811c7591e93bcd860587`.
   - `monetary20260729a`: 7 paragraphs, 11/11 ProofSpans 100% valid, bundle digest `c6241bc7048cc9aafd1fa41e0a808defb7897dcdda8e47f35d0db2440179bef1`.
7. **Honest Status Separation:**
   - Fresh transport status: `VERIFIED_BOUNDED_RESEARCH`.
   - Historical acquisition status: honestly and strictly declared as `PARTIAL` (`usable_for_forecast = False`). Fresh capture proves that the pages are clean and acquirable under supervisor-bounded conditions, but does not retroactively rewrite historical transport facts from 2026-10-19.
8. **Substantive Equivalence with Revision 6:**
   - Exact size parity: 84,741 bytes and 83,318 bytes (delta: 0 bytes).
   - SHA delta caused strictly by dynamic Cloudflare edge Zaraz script nonce in `<head>` and Cloudflare token params rotating per HTTP response. Substantive `#article` statement HTML, paragraph text, byte offsets, and ProofSpans are 100% byte-identical.
9. **Baseline Preservation:** Accepted baseline remains `395d288a0ecb23829b0232debb29865a00e6ef38`. Entire R3 remains `IN_PROGRESS`; R3.1 is not unilaterally accepted; R3.2 is **NOT opened**.

---

## 2. Bounded Research Acquisition Driver Architecture

The standalone driver is located at:
`docs/research/2026-10-20/r3/increment1/revision7/acquisition/bounded_research_driver.py`

### Key Design Elements
- **Parent-Worker Process Supervisor:** Uses `multiprocessing.Process` with `os.pipe` IPC. The parent process monitors worker execution with a high-resolution monotonic timer against a hard 20.0s deadline. If exceeded, the parent issues `SIGKILL` (`process.kill()`), cleans up temporary files, and records an explicit `worker_killed_timeout` outcome with unproven fields left as `unknown`.
- **Anonymous Transport & Cookie Isolation:**
  - Creates a dedicated `urllib.request.OpenerDirector` with `trust_env=False`.
  - Installs a custom redirect handler that inspects target URLs, validates against the domain allowlist (`federalreserve.gov`, `www.federalreserve.gov`), verifies port 443 and HTTPS scheme, and forbids userinfo.
  - Custom HTTP request processor strips any `Cookie`, `Authorization`, or `Proxy-Authorization` headers on initial requests and redirects alike.
  - Custom response processor checks for `Set-Cookie` headers: records `[REDACTED_IGNORED]` in the audit trail without saving cookie values, and prevents cookie storage.
- **Strict Stream Decoding & Discrete EOF:**
  - Decodes HTTP stream in 64 KiB chunks while enforcing the 2,097,152 bytes (2 MiB) limit.
  - Verifies discrete EOF completion (`eof_complete: true`). Closing HTML tag or `Content-Length` alone is never treated as proof of EOF.
- **Comprehensive Audit Log:**
  - Records run ID, UTC timestamps, monotonic elapsed time, supervisor configuration, attempt numbers, hop counts, outgoing header names, response headers (redacted), and SHA-256 hashes.

---

## 3. Hermetic Driver Test Suite (`test_bounded_research_driver.py`)

A standalone offline hermetic test suite verifies driver behavior under scripted conditions with external networking forbidden by socket guard:

```bash
$ PYTHONPATH=/home/pythia/pythia-evidence-r31 /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python -m pytest docs/research/2026-10-20/r3/increment1/revision7/acquisition/test_bounded_research_driver.py -v
```

### Results (8/8 PASS in 3.47s)
| Test Name | Behavior Verified | Status |
| :--- | :--- | :--- |
| `test_supervisor_kills_hung_worker` | External parent kills stalled worker on short deadline (0.5s), records killed outcome | **PASSED** |
| `test_set_cookie_isolation_on_redirect` | Set-Cookie from response is NOT sent as Cookie on redirect hop | **PASSED** |
| `test_offsite_redirect_rejected_before_request` | Redirect to non-allowlisted host rejected prior to issuing request | **PASSED** |
| `test_third_redirect_rejected_before_request` | Redirect exceeding max 2 redirects rejected prior to request | **PASSED** |
| `test_decoded_body_exact_limit_and_overflow` | Exact 2 MiB limit succeeds; 2 MiB + 1 byte rejected with overflow error | **PASSED** |
| `test_interrupted_body_incomplete_eof` | Truncated/interrupted stream rejected with `incomplete_stream_eof` | **PASSED** |
| `test_attempt_budget_and_stop_on_success` | Stops on first success; retries failure within max 2 budget | **PASSED** |
| `test_mandatory_audit_fields_and_no_clobber` | Audit contains all required fields; existing fixtures protected | **PASSED** |

Log saved to: `docs/research/2026-10-20/r3/increment1/revision7/acquisition/driver_test_run.log`.

---

## 4. Live Capture Execution Evidence

The single live acquisition run was executed on `amper`:
- **Run ID:** `run-20261020-160532-3238216`
- **Execution UTC:** `2026-10-20T16:05:32.313371+00:00` to `2026-10-20T16:05:33.010059+00:00`
- **Total Duration:** 696.7 ms
- **Total Attempts Made:** 2 (budget: max 4)
- **Total Hops Executed:** 2
- **Killed Workers:** 0

### Attempt 1: monetary20260916a
- **Target URL:** `https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm`
- **Outcome:** `success`
- **HTTP Status:** 200 OK
- **Duration:** 202.5 ms
- **Decoded Bytes:** 84,741 bytes
- **Raw SHA-256:** `83ed7421cdc9a17999bb44006015e24e1410f0464f59fc3ddeb955907b58e263`
- **EOF Complete:** `true`
- **Outgoing Headers:** `Host`, `User-Agent`, `Accept`, `Accept-Encoding`, `Connection` (Zero Cookie / Auth headers)
- **Response Headers:** `CF-Cache-Status: HIT`, `set-cookie: [REDACTED_IGNORED]`, `Content-Encoding: gzip`
- **Parent RSS Reference:** `live-r2-revision3`, fact `fact-b7cdc057cc6ec28d`, locator `/rss/channel/item[3]`

### Attempt 2: monetary20260729a
- **Target URL:** `https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm`
- **Outcome:** `success`
- **HTTP Status:** 200 OK
- **Duration:** 198.5 ms
- **Decoded Bytes:** 83,318 bytes
- **Raw SHA-256:** `09e038db50283fc9e6b615a39cbff696b18ab6adf1d623e12dc22931c88f5074`
- **EOF Complete:** `true`
- **Outgoing Headers:** `Host`, `User-Agent`, `Accept`, `Accept-Encoding`, `Connection` (Zero Cookie / Auth headers)
- **Response Headers:** `CF-Cache-Status: REVALIDATED`, `set-cookie: [REDACTED_IGNORED]`, `Content-Encoding: gzip`
- **Parent RSS Reference:** `live-r2-revision3`, fact `fact-23bc4c0fc0243b9e`, locator `/rss/channel/item[7]`

---

## 5. Raw Byte & Substantive Content Comparison (R6 vs R7)

| Fixture Key | Revision 6 Size | Revision 7 Size | Delta | R6 Raw SHA-256 | R7 Raw SHA-256 | Substantive Parity |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `monetary20260916a` | 84,741 B | 84,741 B | 0 B | `8252bf02066af1c3...` | `83ed7421cdc9a179...` | **100% Identical** |
| `monetary20260729a` | 83,318 B | 83,318 B | 0 B | `f3cfa831daa2e63b...` | `09e038db50283fc9...` | **100% Identical** |

### Difference Cause Analysis
Byte-by-byte comparison reveals that the SHA difference is strictly confined to:
1. Cloudflare edge Zaraz script tag's dynamic `nonce` attribute in `<head>` (`nonce="..."` and `kP.zaraz._n="..."`), which rotates on every HTTP response.
2. Cloudflare challenge / edge token params script tag at the page footer (`window.__CF$cv$params={...}`).

All substantive FOMC statement content — the `#article` container, heading, release time, all paragraph texts, HTML markup, byte offsets, and ProofSpans — is **100% byte-identical**.

---

## 6. Extraction, Proof Verification & Previews

Statement extraction was performed directly on the fresh raw bytes using the existing parser:

### September 16, 2026 (`monetary20260916a`)
- **Statement ID:** `stmt-4ab04a95f6fd670c`
- **Title:** `Federal Reserve issues FOMC statement`
- **Published At:** `2026-09-16T14:00:00-04:00`
- **Paragraphs:** 6
- **ProofSpans:** 10/10 verified 100% byte-exact against fresh raw bytes.
- **Bundle File:** `statement_bundle_21053da4ab7d0748544eaea69bb531af89ad0fa4d8ee811c7591e93bcd860587.json`
- **Bundle Digest:** `21053da4ab7d0748544eaea69bb531af89ad0fa4d8ee811c7591e93bcd860587`
- **Preview File:** `statement_preview_monetary20260916a.md`
- **Companion Note:** `statement_preview_monetary20260916a_note.md`

### July 29, 2026 (`monetary20260729a`)
- **Statement ID:** `stmt-5027465ebefa9f0c`
- **Title:** `Federal Reserve issues FOMC statement`
- **Published At:** `2026-07-29T14:00:00-04:00`
- **Paragraphs:** 7
- **ProofSpans:** 11/11 verified 100% byte-exact against fresh raw bytes.
- **Bundle File:** `statement_bundle_c6241bc7048cc9aafd1fa41e0a808defb7897dcdda8e47f35d0db2440179bef1.json`
- **Bundle Digest:** `c6241bc7048cc9aafd1fa41e0a808defb7897dcdda8e47f35d0db2440179bef1`
- **Preview File:** `statement_preview_monetary20260729a.md`
- **Companion Note:** `statement_preview_monetary20260729a_note.md`

---

## 7. Offline Demonstration & Verification (`run_demo.py`)

A comprehensive offline demonstration script was constructed at:
`docs/research/2026-10-20/r3/increment1/revision7/run_demo.py`

```bash
$ PYTHONPATH=/home/pythia/pythia-evidence-r31 /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python docs/research/2026-10-20/r3/increment1/revision7/run_demo.py
```

### Execution Log Output
```
======================================================================
Pythia R3.1.7: Offline FOMC Statement Extractor Demonstration (Revision 7)
======================================================================

[Step 1] Verifying 32 blobs across 4 live archives BEFORE demo...
Total blobs verified: 32; Healthy: True
All 32 original CAS blobs across all 4 live archives are 100% intact.

[Step 1b] Verifying Bounded Research Acquisition Package & Audit...
  Acquisition package verified: fresh=VERIFIED_BOUNDED_RESEARCH, audit_sha=1a10f4d536b4c645...

[Step 2] Processing Real Official FOMC Statements...

  --- Extracting monetary20260916a ---
  All 10 ProofSpans verified against raw HTML blob (100% valid).
  Structural locators verified: unambiguous (cardinality 1) for container and all 6 paragraphs.
  Bundle published: statement_bundle_21053da4ab7d0748544eaea69bb531af89ad0fa4d8ee811c7591e93bcd860587.json (digest: 21053da4ab7d0748544eaea69bb531af89ad0fa4d8ee811c7591e93bcd860587)
  Preview published: statement_preview_monetary20260916a.md
  Companion note verified: statement_preview_monetary20260916a_note.md

  --- Extracting monetary20260729a ---
  All 11 ProofSpans verified against raw HTML blob (100% valid).
  Structural locators verified: unambiguous (cardinality 1) for container and all 7 paragraphs.
  Bundle published: statement_bundle_c6241bc7048cc9aafd1fa41e0a808defb7897dcdda8e47f35d0db2440179bef1.json (digest: c6241bc7048cc9aafd1fa41e0a808defb7897dcdda8e47f35d0db2440179bef1)
  Preview published: statement_preview_monetary20260729a.md
  Companion note verified: statement_preview_monetary20260729a_note.md

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

[Step 5] Demo Summary written to: /home/pythia/pythia-evidence-r31/docs/research/2026-10-20/r3/increment1/revision7/demo_summary.json

======================================================================
R3.1.7 REVISION 7 DEMO COMPLETED SUCCESSFULLY (100% PASS, ZERO NETWORK)
======================================================================
```

---

## 8. Full Evaluation Test Suite (`test_run.log`)

```bash
$ PYTHONPATH=/home/pythia/pythia-evidence-r31 /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python -m pytest python/evaluation/tests -v
```

### Summary
```
============================= 256 passed in 10.95s =============================
```
- Total tests: 256
- Strict statement tests: 43 (100% PASS)
- Accepted baseline evaluation tests: 213 (100% PASS)
- Zero regressions. Saved to `docs/research/2026-10-20/r3/increment1/revision7/test_run.log`.

---

## 9. Artifact Manifest (Revision 7)

All generated artifacts are immutable and isolated under:
`docs/research/2026-10-20/r3/increment1/revision7/`

```
revision7/
├── acquisition/
│   ├── bounded_research_driver.py       # Standalone supervisor & capture driver
│   ├── test_bounded_research_driver.py  # Hermetic offline test suite (8/8 PASS)
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
│   ├── statement_bundle_21053da4ab7d0748544eaea69bb531af89ad0fa4d8ee811c7591e93bcd860587.json
│   └── statement_bundle_c6241bc7048cc9aafd1fa41e0a808defb7897dcdda8e47f35d0db2440179bef1.json
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

- **Status Verdict:** **IMPLEMENTED** (Bounded Research Capture & Complete Transport Audit).
- **Historical Evidence Invariant:** Historical acquisition from 2026-10-19 remains strictly and honestly declared as **`PARTIAL`** (`usable_for_forecast = False`). Fresh capture proves the pages are retrievable under strict supervisor and zero-credential isolation, but does not retroactively rewrite historical transport records.
- **Development Baseline:** Accepted development baseline remains **`395d288a0ecb23829b0232debb29865a00e6ef38`**.
- **Lifecycle Status:** Entire R3 is `IN_PROGRESS`. R3.1 is not unilaterally accepted; R3.2 is **NOT opened**.
- **Next Step:** Awaiting independent architectural review.
