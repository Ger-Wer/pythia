# Companion Note: Research Preview — monetary20260729a (Revision 9)

- **Fixture Key:** `monetary20260729a`
- **Source URL:** https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm
- **Associated Preview:** `statement_preview_monetary20260729a.md`
- **Bundle File:** `statement_bundle_207279edad56df99967461e4bcc31e929a0a2f0847109f40ffdfaf8f2f19838a.json`
- **Bundle Digest:** `207279edad56df99967461e4bcc31e929a0a2f0847109f40ffdfaf8f2f19838a`
- **Raw File:** `raw_monetary20260729a.html` (83,318 bytes, SHA-256: `3048b8b787b2df598d496eb0e661136b2fef9f035f62eca50d74e8cdd99ba0b5`)

---

## 1. Transport & Verification Status Comparison

| Dimension | Fresh Transport (Revision 9) | Submitted Revision 8 | Historical Acquisition (Oct 19) |
| :--- | :--- | :--- | :--- |
| **Status Verdict** | `VERIFIED_BOUNDED_RESEARCH` | `CHANGES_REQUIRED` (Review 22.10, N1–N2) | `PARTIAL` |
| **Compressed Stream EOF** | Proven incl. post-EOF byte discipline: multi-member gzip decodes in full, trailing bytes reject (N1 closed) | Decoder EOF verified; bytes after member EOF silently dropped (N1) | Not recorded |
| **Artifact Publication** | Atomic staging + pre-flight fail-closed check | Atomic staging + pre-flight fail-closed check | Direct unverified write |
| **Audit Reporting** | Truthful unknowns (None); proven conditions require actual observations (N2 closed) | Truthful unknowns (None), actual 5 headers; cookie isolation could be vacuously true (N2) | Incomplete audit |
| **Process Supervisor** | Enforced (20s hard deadline, SIGKILL) | Enforced (20s hard deadline, SIGKILL) | Absent / Not recorded |
| **Cookie Isolation** | Enforced (Zero Cookie/Auth headers sent, observed on 2 hops) | Enforced (Zero Cookie/Auth headers sent) | Unknown / Not audited |
| **Usable For Forecast** | `False` | `False` | `False` |
| **Provenance Class** | `research_fixture` | `research_fixture` | `research_fixture` |

### Architectural Status Invariants
- **Fresh Transport:** Verified under `pythia-bounded-research-driver-r319/1.0` (run `run-20261022-135130-2858417`) with proven compressed stream EOF including full accounting of wire bytes after decoder EOF, atomic publication, truthful audit, and strict cookie isolation.
- **Historical Acquisition:** Honestly and strictly preserved as **`PARTIAL`**. The fresh execution proves that official Fed statement pages are acquirable under supervisor-bounded conditions, but does **not** retroactively rewrite historical transport records from 2026-10-19.
- **Revision 8 Submission:** Acknowledged as sound in its submitted capture; review CHANGES_REQUIRED due to N1–N2 in the driver mechanism. Historical artifacts remain unmodified.
- **Production Integration:** Classified as `research_fixture` with `usable_for_forecast = False`; never integrated into live trading decision engines.

---

## 2. Byte & Substantive Content Comparison (R8 vs R9)

- **Prior Raw Size (R8):** 83,318 bytes
- **Fresh Raw Size (R9):** 83,318 bytes
- **Size Delta:** 0 bytes (exact size parity preserved across R6, R7, R8, and R9)
- **SHA-256 Comparison:**
  - R8 SHA-256: `4561cbc921a36cbc61fdd90118d7c703cf73866e73f5781d1c4bb31057770195`
  - R9 SHA-256: `3048b8b787b2df598d496eb0e661136b2fef9f035f62eca50d74e8cdd99ba0b5`
- **Substantive Equivalence:**
  - Core statement paragraphs (paragraphs 0 to 4): **100% byte-identical** in both raw HTML quotes and decoded display text (quote SHA-256 equality verified against the Revision 8 bundle).
  - All 7 paragraph display texts and structural offsets match; the only raw difference inside `#article` remains the ancillary media paragraph's rotating Cloudflare email-protection tokens, plus Zaraz nonce in `<head>` and challenge tokens in the footer.

---

## 3. Parent Feed Lineage

- **Archive Name:** `live-r2-revision3`
- **Parent Fact ID:** `fact-23bc4c0fc0243b9e`
- **Parent Feed Item Locator:** `/rss/channel/item[7]`
- **Parent Feed Blob SHA-256:** `69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1`
- **Feed Published At:** `2026-07-29T18:00:00+00:00`
- **Parent Dossier Bundle Digest (as_of_seq=8):** `cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde`
