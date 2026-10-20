# Companion Note: Research Preview — monetary20260916a

- **Fixture Key:** `monetary20260916a`
- **Source URL:** https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm
- **Associated Preview:** `statement_preview_monetary20260916a.md`
- **Bundle File:** `statement_bundle_21053da4ab7d0748544eaea69bb531af89ad0fa4d8ee811c7591e93bcd860587.json`
- **Bundle Digest:** `21053da4ab7d0748544eaea69bb531af89ad0fa4d8ee811c7591e93bcd860587`
- **Raw File:** `raw_monetary20260916a.html` (84,741 bytes, SHA-256: `83ed7421cdc9a17999bb44006015e24e1410f0464f59fc3ddeb955907b58e263`)

---

## 1. Transport & Verification Status Comparison

| Dimension | Fresh Transport (Revision 7 Bounded Capture) | Historical Acquisition (Revision 1–6 / Oct 19) |
| :--- | :--- | :--- |
| **Status Verdict** | `VERIFIED_BOUNDED_RESEARCH` | `PARTIAL` |
| **Usable For Forecast** | `False` | `False` |
| **Provenance Class** | `research_fixture` | `research_fixture` |
| **Supervisor Enforced** | Yes (external parent process supervisor, 20s hard deadline, SIGKILL) | Absent / Not recorded |
| **Credential / Cookie Isolation** | Yes (zero ambient credentials, `trust_env=False`, no `Cookie`/`Authorization` headers sent on any hop, `Set-Cookie` redacted/ignored) | Not audited / Unknown |
| **Attempt & Redirect Accounting** | Yes (1 attempt, 0 redirects, total hops: 1; budget max 2 attempts) | Not recorded |
| **Decoded EOF Completion** | Verified (`eof_complete: true`, exactly 84,741 bytes, no transport or decoding errors) | Not recorded |

### Scope & Architectural Note
- The fresh transport verification (`VERIFIED_BOUNDED_RESEARCH`) strictly applies to this standalone, supervisor-bounded research re-acquisition.
- **Historical acquisition status remains strictly and honestly declared as `PARTIAL`**. The fresh execution proves that official Fed statement pages are retrievable under strict zero-ambient-credential, cookie-isolated, supervisor-bounded conditions, but it does **not** retroactively rewrite or alter the historical capture record from 2026-10-19.
- This bundle remains classified as `research_fixture` with `usable_for_forecast = False` and is not integrated into production runtime ingestion or live trading decision workflows.

---

## 2. Byte & Substantive Content Comparison with Revision 6

- **Prior Raw Size (R6):** 84,741 bytes
- **Fresh Raw Size (R7):** 84,741 bytes
- **Size Delta:** 0 bytes
- **SHA-256 Comparison:**
  - R6 SHA-256: `8252bf02066af1c379bdcad3b9b70cc8cdbf8e21183eb3f00c8df0a2dba9391f`
  - R7 SHA-256: `83ed7421cdc9a17999bb44006015e24e1410f0464f59fc3ddeb955907b58e263`
- **Cause of SHA Delta:** Dynamic Cloudflare edge Zaraz script nonce (`nonce="..."`) in `<head>` and Cloudflare challenge token params at the page footer rotate per HTTP response.
- **Substantive Equivalence:** 100% byte-identical across the entire substantive statement body (`#article`), title, release time, all 6 paragraphs, and all 10 ProofSpans.

---

## 3. Parent Feed Lineage

- **Archive Name:** `live-r2-revision3`
- **Parent Fact ID:** `fact-b7cdc057cc6ec28d`
- **Parent Feed Item Locator:** `/rss/channel/item[3]`
- **Parent Feed Blob SHA-256:** `69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1`
- **Feed Published At:** `2026-09-16T18:00:00+00:00`
- **Parent Dossier Bundle Digest (as_of_seq=8):** `cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde`
