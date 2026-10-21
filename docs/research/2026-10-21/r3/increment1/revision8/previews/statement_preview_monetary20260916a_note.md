# Companion Note: Research Preview — monetary20260916a (Revision 8)

- **Fixture Key:** `monetary20260916a`
- **Source URL:** https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm
- **Associated Preview:** `statement_preview_monetary20260916a.md`
- **Bundle File:** `statement_bundle_b91d219e930feca2589db16fa6377e844082b0a255148e3ae851632888a8c829.json`
- **Bundle Digest:** `b91d219e930feca2589db16fa6377e844082b0a255148e3ae851632888a8c829`
- **Raw File:** `raw_monetary20260916a.html` (84,741 bytes, SHA-256: `e3308825cc5285fe31df36d5f0abff5b96275758ceb038b3658ba6c6198cf314`)

---

## 1. Transport & Verification Status Comparison

| Dimension | Fresh Transport (Revision 8) | Submitted Revision 7 | Historical Acquisition (Oct 19) |
| :--- | :--- | :--- | :--- |
| **Status Verdict** | `VERIFIED_BOUNDED_RESEARCH` | `CHANGES_REQUIRED` (Review 21.10) | `PARTIAL` |
| **Compressed Stream EOF** | Proven (zlib/gzip decompressor EOF verified) | Iter_content chunk end only (T1 defect) | Not recorded |
| **Artifact Publication** | Atomic staging + pre-flight fail-closed check | Overwriting default, partial no-clobber (T2) | Direct unverified write |
| **Audit Reporting** | Truthful unknowns (None), actual 5 headers | Recorded kill hops as 0, 3 headers (T3) | Incomplete audit |
| **Process Supervisor** | Enforced (20s hard deadline, SIGKILL) | Enforced (20s hard deadline, SIGKILL) | Absent / Not recorded |
| **Cookie Isolation** | Enforced (Zero Cookie/Auth headers sent) | Enforced (Zero Cookie/Auth headers sent) | Unknown / Not audited |
| **Usable For Forecast** | `False` | `False` | `False` |
| **Provenance Class** | `research_fixture` | `research_fixture` | `research_fixture` |

### Architectural Status Invariants
- **Fresh Transport:** Verified under `pythia-bounded-research-driver-r318/1.0` with proven compressed stream EOF, atomic publication, truthful audit, and strict cookie isolation.
- **Historical Acquisition:** Honestly and strictly preserved as **`PARTIAL`**. The fresh execution proves that official Fed statement pages are acquirable under supervisor-bounded conditions, but does **not** retroactively rewrite historical transport records from 2026-10-19.
- **Revision 7 Submission:** Acknowledged as claimed VERIFIED, review CHANGES_REQUIRED due to T1–T3. Historical artifacts remain unmodified.
- **Production Integration:** Classified as `research_fixture` with `usable_for_forecast = False`; never integrated into live trading decision engines.

---

## 2. Byte & Substantive Content Comparison (R7 vs R8)

- **Prior Raw Size (R7):** 84,741 bytes
- **Fresh Raw Size (R8):** 84,741 bytes
- **Size Delta:** 0 bytes (exact size parity preserved across R6, R7, and R8)
- **SHA-256 Comparison:**
  - R7 SHA-256: `83ed7421cdc9a17999bb44006015e24e1410f0464f59fc3ddeb955907b58e263`
  - R8 SHA-256: `e3308825cc5285fe31df36d5f0abff5b96275758ceb038b3658ba6c6198cf314`
- **Substantive Equivalence:**
  - Core statement paragraphs (paragraphs 0 to 3: vote approval, policy decision, economic assessment, inflation mandate): **100% byte-identical** in both raw HTML quotes and decoded display text.
  - All 6 paragraph display texts and structural offsets match 100%.
  - Raw markup differences within the page:
    1. Cloudflare edge Zaraz script nonce (`nonce="..."`) in `<head>` (~bytes 2820–3100).
    2. Cloudflare email protection token in share-link (~byte 72770).
    3. Cloudflare email protection token and `data-cfemail` attribute in ancillary media inquiries paragraph inside `#article` (~byte 74650).
    4. Cloudflare challenge token params in page footer (~byte 83970).

---

## 3. Parent Feed Lineage

- **Archive Name:** `live-r2-revision3`
- **Parent Fact ID:** `fact-b7cdc057cc6ec28d`
- **Parent Feed Item Locator:** `/rss/channel/item[3]`
- **Parent Feed Blob SHA-256:** `69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1`
- **Feed Published At:** `2026-09-16T18:00:00+00:00`
- **Parent Dossier Bundle Digest (as_of_seq=8):** `cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde`
