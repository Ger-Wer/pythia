# Pythia R3.1.1: Offline Statement Extraction Boundary Fixes Report (Revision 1)

**Date:** 2026-10-19  
**Execution Environment:** `amper` (Ubuntu 24.04, Linux 6.8.0-45-generic x86_64)  
**Worktree:** `/home/pythia/pythia-evidence-r31`  
**Branch:** `codex/evidence-r31-statements`  
**Base Commit:** `395d288a0ecb23829b0232debb29865a00e6ef38` (`codex/evidence-r3-extraction` after R3.0.2)  
**Candidate Under Review:** `ba50dfb54eebc52d396b6e2588a07ba616e3a9e9` (R3.1 CHANGES_REQUIRED)  
**Python Runtime:** `/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3` (Python 3.10.12)  
**Executor Verdict:** **IMPLEMENTED** (with honest **PARTIAL** on historical acquisition evidence)

---

## 1. Executive Summary

This increment (R3.1.1) resolves all six confirmed boundary defects (**F1–F6**) identified in independent review `r31-ba50dfb` ([`REVIEW_RU.md`](file:///Users/gerwer/Desktop/MemPalace/project/pythia/reviews/2026-10-19/r31-ba50dfb/REVIEW_RU.md)) for the offline FOMC statement extractor ([`fed_statement_extract.py`](file:///home/pythia/pythia-evidence-r31/python/evaluation/fed_statement_extract.py)).

All fixes were implemented and verified exclusively on server `amper`. No live network calls were permitted during tests or extraction runs. The accepted development baseline remains `395d288a0ecb23829b0232debb29865a00e6ef38`.

All previous increment artifacts (`docs/research/2026-10-19/r3/increment1/`) remain untouched; all corrected manifests, deterministic bundles, previews, and acquisition documentation are isolated in **`docs/research/2026-10-19/r3/increment1/revision1/`**.

---

## 2. Lineage Correction (Narrative Erratum)

In the initial R3.1 report (`R3_INCREMENT1_REPORT.md`), a narrative error misidentified the parent RSS feed source as `live-r2-run1 seq3`. As mechanically verified by provenance audit, the true lineage of the canonical Fed RSS feed facts is:
- **Archive:** `live-r2-revision3`
- **Seq:** 2
- **Receipt ID:** `39bf5ee5-5918-489f-b9e1-dfcfcfbce96b`
- **Revision ID:** `26b40558-1014-47f2-9b10-d7b8007a07d0`
- **Feed Blob SHA-256:** `69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1` (9,687 bytes)
- **Source ID:** `fed_monetary_rss` (`https://www.federalreserve.gov/feeds/press_monetary.xml`)
- **Parent Event Dossier Bundle:** `demo_dossier_bundle.json` (digest `cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde`, `as_of_seq = 8`)
- **Facts Referenced:**
  - `fact-b7cdc057cc6ec28d` (`/rss/channel/item[3]`) $\rightarrow$ September 16, 2026 statement
  - `fact-23bc4c0fc0243b9e` (`/rss/channel/item[7]`) $\rightarrow$ July 29, 2026 statement

This corrected lineage is frozen in `docs/research/2026-10-19/r3/increment1/revision1/fixtures/manifest_*.json`. The old report file is preserved verbatim.

---

## 3. Acquisition Provenance Audit & Honest Verdict

### A. Recovered Script & Execution Record
The original acquisition script executed on `amper` at `2026-10-19T07:47:12Z` has been recovered verbatim from the process transcript and saved to:
`docs/research/2026-10-19/r3/increment1/revision1/acquisition/original_acquisition_script.py`.

Execution output:
```text
[Acquiring] https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm...
  Success: 84741 bytes, sha256=8252bf02066af1c379bdcad3b9b70cc8cdbf8e21183eb3f00c8df0a2dba9391f
  Saved to: raw_monetary20260916a.html and manifest_monetary20260916a.json
[Acquiring] https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm...
  Success: 83318 bytes, sha256=f3cfa831daa2e63b0c370bd0de3b58cccc30439ee3e8378bc0a6c2dbf650ef49
  Saved to: raw_monetary20260729a.html and manifest_monetary20260729a.json
Done! Summary written to /home/pythia/pythia-evidence-r31/docs/research/2026-10-19/r3/increment1/fixtures/fixtures_summary.json
```

### B. Proven vs Unproven Facts
- **Proven in Code:**
  - Exact 2 target URLs matching parent feed facts.
  - HTTPS-only scheme enforced (`parsed.scheme == "https"`).
  - Verified TLS (`verify=True`).
  - Anonymous session (`session.trust_env = False`, no ambient credentials/netrc/cookies).
  - Domain allowlist restricted to `{"federalreserve.gov", "www.federalreserve.gov"}`.
  - Hard byte limit <= 2 MiB (`MAX_BYTES = 2097152`).
  - Hard timeout 20s (`TIMEOUT = 20.0`).
  - Redirection cap <= 2 (`MAX_REDIRECTS = 2`).
- **Missing / Unproven in Initial Manifest Schema:**
  - The script streamed via `iter_content`, but the manifest schema did not store an explicit boolean `eof_complete=True`.
  - The script executed in a single-pass loop, but no discrete `attempt_count=1` field was captured in the manifest schema.

### C. Verdict
In strict compliance with R3.1.1 instructions, missing fields are not invented retroactively. The verdict on historical acquisition evidence is declared **PARTIAL**, while offline parser validation and synthetic controls are **100% PASS**.

---

## 4. Boundary Defect Resolutions (F1 - F6)

### F1: Mandatory Typed Manifest & Provenance Validation
- **Defect:** In `ba50dfb`, `extract_fed_statement` published `extracted` when given `{}` manifest, non-fed source/final URLs, or `http_status=403 / transport_outcome="http_error"`.
- **Fix:** Added mandatory typed validation:
  - Manifest must be a non-empty dictionary containing required fields (`source_url`, `final_url`, `blob_sha256`, `bytes_stored`, `transport_outcome`).
  - `source_url` and `final_url` must belong to official domains (`federalreserve.gov`, `www.federalreserve.gov`).
  - `transport_outcome` must be `"success"`, and `http_status` must be 200.
  - `blob_sha256` and `bytes_stored` must match raw bytes.
  - Rejections emit typed `RejectionRecord` with explicit statuses (`"invalid_manifest"`, `"unsupported_source_url"`, `"transport_failure"`).
- **Verification:**
  - `empty_manifest`: rejected with `status="invalid_manifest"`.
  - `nonofficial_source_and_final`: rejected with `status="unsupported_source_url"`.
  - `http403_with_valid_html`: rejected with `status="transport_failure"`.

### F2: Negative Body Remains Negative Even with Supported Skeleton
- **Defect:** In `ba50dfb`, the challenge check had `and "article" not in text_lower`, which was always bypassed because `#article` was present. A valid skeleton containing only `<p>Enable JavaScript to view this article.</p>` published as `extracted`.
- **Fix:**
  - Removed flawed `"article" not in text_lower` condition.
  - Added substantive body content gate: inspects all extracted body paragraphs for JS barriers (`"enable javascript to view this article"`, `"enable javascript and cookies"`, `"please turn javascript on"`).
  - Emits `status="blocked_or_challenge"` if the body consists of a barrier.
- **Verification:**
  - `js_only_template`: rejected with `status="blocked_or_challenge"`.
  - Genuine statements with valid body text continue to pass cleanly.

### F3: Unambiguous Structural Locators & Title Comment Isolation
- **Defect:**
  - Container selector was `#article div.col-xs-12.col-sm-8.col-md-8`, matching both `.heading` and body (2 matches).
  - Paragraph selector was `p:nth-of-type(k)`, matching heading paragraphs as well as body (2 matches), and producing `[3, 1, 0]` in nested DOM.
  - An HTML comment `<!-- <h3 class="title">A different title</h3> -->` before heading caused raw title proof to point to the comment instead of the DOM title.
- **Fix:**
  - Container locator updated to: `#article > div.col-xs-12.col-sm-8.col-md-8:not(.heading)` (cardinality = 1).
  - Implemented hierarchical DOM path builder `_build_element_selector(el, root_selector, root_elem)` generating direct-child paths: `... > p:nth-of-type(k)` or `... > div:nth-of-type(1) > p:nth-of-type(1)`. Verified cardinality == 1 for every paragraph.
  - Title regex search anchored inside `<div class="heading ...">` raw byte offset, completely skipping pre-heading comments.
  - Verified `BeautifulSoup(title_proof.raw_quote_text).get_text(strip=True) == doc.title`.
- **Verification:**
  - `unambiguous_structural_locators_monetary20260916a`: container matches = 1, paragraph matches = `[1, 1, 1, 1, 1, 1]`.
  - `unambiguous_structural_locators_monetary20260729a`: container matches = 1, paragraph matches = `[1, 1, 1, 1, 1, 1, 1]`.
  - `nested_paragraph_locators_resolve`: matches = `[1, 1, 1]`.
  - `title_proof_matches_parsed_title`: `parsed_title == raw_title_proof`, `byte_hash_proof_valid = True`.

### F4: Single-Entity HTML Decoding
- **Defect:** In `ba50dfb`, `_clean_display_text` called `html.unescape()` after `BeautifulSoup.get_text()`, causing double unescaping. `<p>Literal &amp;#49; percent; literal &amp;lt;5.</p>` was corrupted to `"Literal 1 percent; literal <5."`.
- **Fix:** Removed redundant `html.unescape()`. BeautifulSoup's `get_text()` handles entity decoding exactly once.
- **Verification:**
  - `display_text_single_entity_decode`: expected and actual = `"Literal &#49; percent; literal &lt;5."`.
  - `bom_unicode_crlf_inline`: `"Ставка — 4.00 & 5.00 percent."`.
  - `short_utf8`: `"Café — 4 percent."`.

### F5: Valid Timestamps & Date Mismatch Diagnostic
- **Defect:**
  - `29:99 p.m. EDT` produced impossible ISO timestamp `2026-09-16T29:99:00-04:00` rejected by `datetime.fromisoformat`.
  - Differences between page calendar date and parent RSS pubDate were not recorded as frozen diagnostics.
- **Fix:**
  - Added strict hour (1..12) and minute (0..59) range validation before constructing `published_at`. Out-of-range times leave `published_at = None` with diagnostic reason `invalid_time_digits`.
  - Verified timestamp with `datetime.fromisoformat()`.
  - Added `date_diagnostics` recording calendar mismatch between page `published_date` and parent feed date.
- **Verification:**
  - `invalid_hour_minute`: `published_at = None`, `bundle_published = True`, `bundle_status = "extracted"`.
  - `rss_page_mismatch`: `date_mismatch_detected = True`, page date `2026-09-16` and feed date `2026-09-17` preserved separately.

### F6: Output Directory & Preview Destination Protection
- **Defect:** `build_fed_statement_bundle` accepted output directory inside or equal to fixture dir or symlinks to it. CLI `--preview-path` pointing to a raw fixture symlink overwrote raw HTML with Markdown.
- **Fix:**
  - `build_fed_statement_bundle`: checks `out_dir == fixture_dir` and `out_dir.is_relative_to(fixture_dir)` (resolving symlinks), raising `StatementPublishError`. Also protects live archive directories.
  - `publish_statement_preview`: rejects destinations resolving to fixture/manifest inputs, inside fixture directory, or live archive directories. Existing preview files with differing content raise `StatementPublishError` (no clobber); identical files replay safely.
  - CLI catches errors and exits with non-zero exit code (1), leaving raw inputs untouched.
- **Verification:**
  - `output_reject_same_fixture_dir`: rejected with `StatementPublishError`.
  - `output_reject_inside_fixture_dir`: rejected with `StatementPublishError`.
  - `output_reject_symlink_to_fixture_dir`: rejected with `StatementPublishError`.
  - `cli_preview_cannot_overwrite_raw_via_symlink`: exit code 1, raw SHA before and after are identical (`203abbb3...`).

---

## 5. Test Suite & Verification Results

### A. Independent Reviewer Controls (`test_reviewer_controls.py`)
```json
{
  "reviewed_commit": "ba50dfb54eebc52d396b6e2588a07ba616e3a9e9",
  "network_attempts": [],
  "unmet_requirements": []
}
```
All 17 control assertions passed (0 unmet requirements).

### B. Independent Semantics Probes (`test_semantics_probe.py`)
All 12 semantic probes passed with expected outcomes (positive real, empty manifest, non-fed host, http403, positive synthetic, invalid hour/minute, unknown timezone, date only, js-only barrier, invalid page date, missing page date, rss mismatch).

### C. Full Pytest Suite on `amper`
```text
============================= 232 passed in 13.90s =============================
```
- Total tests: 232 (213 accepted regression tests + 19 statement extractor tests).
- 0 failures, 0 warnings, 0 network attempts.
- Log saved to `docs/research/2026-10-19/r3/increment1/revision1/test_run.log`.

### D. Historical Demo Replay (`demo_replay.py`)
- Facts extracted: 35
- Proofs valid: 35/35 (100%)
- Bundle digest: `cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde` (identical to committed baseline)
- Live archive signature: identical before and after.

### E. Live Archive Integrity Audit
- `live-r2-run1`: 8 receipts, 8 CAS blobs intact.
- `live-r2-revision1`: 8 receipts, 8 CAS blobs intact.
- `live-r2-revision2`: 8 receipts, 8 CAS blobs intact.
- `live-r2-revision3`: 8 receipts, 8 CAS blobs intact.
- Total: 32 blobs intact and unmodified. Container `pythia-v2` and `ee6460f` untouched.

---

## 6. Sample Extracted Statement Preview (Revision 1)

Verbatim preview for `monetary20260916a` generated by `fed_statement_extract.py`:

```markdown
# FOMC Statement Research Preview

- **Bundle Digest:** `c5d4fa8c368fbcc0d97fb383f51ba2901f2744cbe6e91ea812d6e7a276277da7`
- **Extractor Version:** `pythia-statement-extractor-r3/0.1.1`
- **Provenance Class:** `research_fixture`
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

### Paragraph 2 [`economic_assessment`]
> Economic activity is expanding at a solid pace. While uncertainty remains elevated owing, in part, to geopolitical developments, domestic spending has been resilient. Productivity growth is strong, and capital investment is robust. Job gains have kept pace with the workforce, and the unemployment rate has changed little.

- *Locator:* `#article > div.col-xs-12.col-sm-8.col-md-8:not(.heading) > p:nth-of-type(3)`
- *Raw Byte Slice:* `[74064:74410]` (346 bytes, SHA: `423741ab9405...`)

### Paragraph 3 [`inflation_mandate`]
> Inflation remains elevated. Today's policy action will support a timelier return to the Committee's 2 percent goal. The Committee will deliver price stability.

- *Locator:* `#article > div.col-xs-12.col-sm-8.col-md-8:not(.heading) > p:nth-of-type(4)`
- *Raw Byte Slice:* `[74414:74580]` (166 bytes, SHA: `4e4a1f4ea46a...`)

### Paragraph 4 [`media_inquiries`]
> For media inquiries, please email [email protected] or call 202-452-2955.

- *Locator:* `#article > div.col-xs-12.col-sm-8.col-md-8:not(.heading) > p:nth-of-type(5)`
- *Raw Byte Slice:* `[74584:74818]` (234 bytes, SHA: `4b5c9687ad25...`)

### Paragraph 5 [`implementation_note`]
> Implementation Note issued September 16, 2026

- *Locator:* `#article > div.col-xs-12.col-sm-8.col-md-8:not(.heading) > p:nth-of-type(6)`
- *Raw Byte Slice:* `[74822:74937]` (115 bytes, SHA: `0f488c723de4...`)

## Verifications
- Container Proof: bytes `[73482:74976]`
- Title Proof: bytes `[69165:69225]`
- Date Proof: bytes `[69100:69147]`
- All proof spans independently verified against raw bytes: **VALID**
```

---

## 7. Deliverables & File Layout

- **Code:**
  - `python/evaluation/fed_statement_extract.py` (v0.1.1, schema v1.1, bundle v0.1.1)
  - `python/evaluation/tests/test_fed_statement_extract.py` (19 comprehensive tests)
- **Revision 1 Research Artifacts:**
  - `docs/research/2026-10-19/R3_INCREMENT1_REVISION1_REPORT.md` (this report)
  - `docs/research/2026-10-19/r3/increment1/revision1/fixtures/` (fixtures with corrected lineage)
  - `docs/research/2026-10-19/r3/increment1/revision1/bundles/` (deterministic bundles)
  - `docs/research/2026-10-19/r3/increment1/revision1/previews/` (markdown previews)
  - `docs/research/2026-10-19/r3/increment1/revision1/acquisition/` (recovered acquisition script and audit)
  - `docs/research/2026-10-19/r3/increment1/revision1/run_demo.py` (reproducible demonstration script)
  - `docs/research/2026-10-19/r3/increment1/revision1/demo_summary.json` (machine-readable summary)
  - `docs/research/2026-10-19/r3/increment1/revision1/test_run.log` (full pytest log: 232 passed)
