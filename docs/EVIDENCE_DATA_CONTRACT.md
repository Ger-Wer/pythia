# Pythia: Evidence Data Contract

**Status:** R1 Specification (Design & Contract Only — No Storage / ORM / Migrations).  
**Date:** 2026-10-17.  
**Scope:** Defines the data contract for diagnostic probe runs (R1) and future evidence records (R2+), distinguishing probe results, raw document captures, and extracted claims.

---

## 1. Architectural Principles

1. **Separation of Layers:**
   - **Probe Result (Layer 0, R1):** An ephemeral or diagnostic check of reachability, transport, and envelope structure. A single probe does **not** constitute a historical archive or a `first_seen` timeline.
   - **Evidence Document (Layer 1, R2):** An immutable, append-only captured document with raw blob storage, complete SHA-256 integrity hash, HTTP metadata, collector/extractor versions, and rigorous timestamp lineage.
   - **Claim & Event Dossier (Layer 2, R3):** Verbatim quote, character anchors, normalized quantitative claims, and linkage to Polymarket/Kalshi prediction markets.
2. **Date Lineage & Null Semantics:**
   - `published_at`, `updated_at`, `first_seen_at`, and `fetched_at` are distinct fields.
   - If an official publication date cannot be proven from document content or authoritative metadata, `published_at` **must be `null`**. It must **never** be replaced with HTTP `Last-Modified` or `fetched_at`.
   - `first_seen_at` requires persistent chronological tracking across repeated collections; an isolated one-off probe cannot fabricate a historical `first_seen_at`.
3. **Verbatim & Original First:**
   - The raw byte content is preserved verbatim.
   - Translated text is strictly separated from original text and explicitly attributed to a translation engine and version.
4. **Zero Edge Assumption:**
   - Neither probe validity nor document storage implies trading edge or forecast usability. Every record in R1 carries `usable_for_forecast: false`.

---

## 2. Probe Result Contract (R1 Diagnostic Layer)

A probe run tests whether an endpoint is reachable and conforms to expected transport and envelope schemas.

### 2.1 Schema Definition

| Field | Type | Nullable | Description |
|---|---|---|---|
| `source_id` | `string` | No | Unique identifier matching `source_registry.json` (e.g. `nyfed_effr`). |
| `initial_url` | `string` (URI) | No | Configured URL requested by the probe. |
| `final_url` | `string` (URI) | No | Destination URL after following redirects (max 3 allowed within domain allowlist). |
| `http_status` | `integer` | Yes | HTTP status code (e.g. `200`, `403`). `null` if network error occurred before response. |
| `fetch_started_at` | `string` (ISO 8601 UTC) | No | UTC timestamp when request was initiated. |
| `fetch_completed_at` | `string` (ISO 8601 UTC) | No | UTC timestamp when response stream closed or timed out. |
| `duration_ms` | `float` | No | Elapsed request duration in milliseconds. |
| `bytes_received` | `integer` | No | Actual bytes read from stream (capped at 2 MiB stream limit). |
| `content_type` | `string` | Yes | `Content-Type` header from response, or `null` if unavailable. |
| `body_sha256` | `string` (hex 64) | Yes | SHA-256 of complete payload. `null` if stream limit exceeded or network failed. |
| `status` | `string` (enum) | No | Probe status (see §2.2). |
| `validation_level` | `string` (enum) | No | Depth of check passed/attempted (see §2.3). |
| `reason` | `string` | No | Clear, human-readable justification for the status and level. |
| `item_count` | `integer` | Yes | Number of valid items/releases/records parsed (e.g. 5 refRates, 0 releases). `null` for unparsed HTML. |
| `published_at` | `string` (ISO 8601 UTC) | Yes | Verified published date if extractable with proof, otherwise `null`. |
| `checker_version` | `string` | No | Version of probe software (e.g. `pythia-probe-r1/1.0`). |
| `usable_for_forecast` | `boolean` | No | Always `false` in R1. |

### 2.2 Status Enumeration (`status`)

- `valid`: The response satisfied all transport, envelope, and expected content structure checks for this specific endpoint.
- `blocked`: Access denied by HTTP 401/403/429, WAF, Cloudflare challenge, or CAPTCHA.
- `invalid_content`: The server returned HTTP 200, but payload structure does not match the expected contract (e.g. error HTML instead of RSS, dictionary instead of releases array, missing `refRates`).
- `network_error`: Connection refused, DNS failure, timeout (> 15s), or redirect to an untrusted domain.
- `unverified`: Transport and envelope succeeded, but substantive data extraction was not performed or verified in this stage (e.g. Atlanta Fed GDPNow HTML, LMSYS Arena HTML).

### 2.3 Validation Level Enumeration (`validation_level`)

- `transport`: Only TCP/TLS connection and HTTP status code were evaluated.
- `envelope`: Transport succeeded, and top-level format envelope was confirmed (e.g. valid JSON parsing, valid RSS `<rss>` root, valid HTML `<html>` document).
- `content`: Substantive inner structure, required field types, and entity identifiers were confirmed (e.g. `refRates` contains numeric `percentRate` and `EFFR` type; GitHub releases contain repo references; RSS contains expected feed items).

---

## 3. Future Evidence Record Contract (R2+ Storage Layer)

When document archival is implemented in R2, documents will be stored append-only without mutation.

### 3.1 Core Record Fields

```typescript
interface EvidenceRecord {
  // Identification
  evidence_id: string;               // UUIDv7 (time-ordered) or sha256-based canonical ID
  source_id: string;                 // References source_registry.json
  issuer: string;                    // Authoritative publishing entity (e.g. "Federal Reserve Bank of New York")
  initial_url: string;               // Origin URL requested
  final_url: string;                 // Canonical resolved URL
  document_type: "api_json" | "rss_feed" | "html_document" | "pdf_binary" | "plain_text";
  language: string;                  // ISO 639-1 code ('en', 'zh') or 'multilingual' / 'unknown'

  // Integrity & Raw Storage
  blob_ref: string;                  // Path or CAS address of raw verbatim payload (e.g. "blobs/2026/10/17/7deb...bin")
  content_sha256: string;            // Exact SHA-256 of the complete preserved blob
  byte_size: number;                 // Exact byte length
  http_metadata: {
    status_code: number;
    headers: Record<string, string>; // Canonical lowercase headers (etag, last-modified, content-type)
    protocol_version: string;        // HTTP/1.1 or HTTP/2
  };
  collector_version: string;         // Pythia collector build identifier
  extractor_version: string | null;  // Version of text extraction module, or null if raw only

  // Temporal Lineage (Explicit & Non-substitutable)
  published_at: string | null;       // ISO 8601 UTC. Official release timestamp with provenance
  published_at_precision: "second" | "minute" | "hour" | "day" | null;
  date_origin: "document_body" | "authoritative_api" | "http_header" | "unknown";
  updated_at: string | null;         // ISO 8601 UTC. Official document revision timestamp
  first_seen_at: string;             // ISO 8601 UTC. First persistent observation by Pythia collector
  fetched_at: string;                // ISO 8601 UTC. Exact timestamp when this specific fetch completed

  // Content Separation
  raw_content: string | null;        // Decoded UTF-8 text if textual, or null if binary
  extracted_markdown: string | null; // Extracted structured text (Trafilatura/parser)
  translation: {
    language: string;
    engine: string;                  // e.g. "local-qwen-mt-v1"
    translated_text: string;
    translated_at: string;
  } | null;

  // Revision & Syndication Lineage
  revision_index: number;            // 0 for original, increments on revised document at same canonical URL
  previous_evidence_id: string | null; // Pointer to preceding revision
  is_syndicated_reprint: boolean;    // True if content is a wire-service or secondary aggregator reprint
  origin_canonical_url: string | null; // Canonical source URL if syndicated

  // Forward linkage to Layer 2 (Claim / Event) — unpopulated in R1/R2
  claims: Array<{
    claim_id: string;
    verbatim_quote: string;          // Exact substring from extracted_markdown
    quote_offset_start: number;      // Exact character start
    quote_offset_end: number;        // Exact character end
    quantitative_fact: {
      metric: string;                // e.g. "effr_percent", "gdpnow_real_gdp_growth"
      value: number;
      unit: string;                  // e.g. "percent", "bps", "usd"
      period: string;                // e.g. "2026-10-16", "2026-Q3"
    } | null;
    target_event_slug: string | null; // Prediction market event binding
  }>;

  // Safety & Gate Flag
  usable_for_forecast: boolean;      // False by default; only true after R5 JEV gate passes
}
```

---

## 4. Corpus Quality & Integrity Checks (Future R4 Verification Rules)

Before any collected document can be included in a benchmark or training corpus, the following negative checks must be passed:

1. **Revisions vs. New Events:**
   - If `canonical_url` matches an existing record but `content_sha256` differs, create a new revision node (`revision_index + 1`, linking to `previous_evidence_id`).
   - Never overwrite the previous record.
2. **Duplicate Origin Suppression:**
   - Multiple syndication feeds publishing the same press release (e.g. PR Newswire, Yahoo Finance, Reuters wire) must be clustered by content hash and entity origin. They do not count as independent confirming evidence.
3. **Unknown Date Handling:**
   - If no explicit date is published in the document body or structured API, `published_at` remains `null`.
   - The system must reject rules that attempt to backfill `published_at` with `fetched_at` or current time.
4. **Number and Unit Consistency:**
   - Numerical figures extracted from claims must be checked against unit rules (e.g. Fed Funds rates are expressed in percentage points, rate hikes in basis points, GDP growth as annualized percentage).
   - Values exceeding plausible financial bounds (e.g. EFFR > 25.0% or < 0.0%) trigger automatic rejection of the extracted claim.
5. **Translation Isolation:**
   - Original non-English text (e.g. DeepSeek Chinese technical disclosures) must never be replaced by machine translation in the primary record.
   - Translation is stored as a derived annotation.
6. **Anti-Bot & CAPTCHA Rejection:**
   - Any response containing Cloudflare Turnstile, hCaptcha, Akamai Ghost challenge, or standard login screens must be classified as `blocked` or `invalid_content`. Under no circumstances may an error challenge page be stored as an authoritative document.
7. **Empty Client-Side JavaScript Shells:**
   - Responses returning HTTP 200 with minimal HTML (e.g. `<div id="root"></div>` without server-rendered data) must be classified as `unverified` or `invalid_content` unless an authorized rendering engine extracts the content.

---

## 5. Contract Examples

### 5.1 Positive Probe Result Example (`nyfed_effr`)

```json
{
  "source_id": "nyfed_effr",
  "initial_url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json",
  "final_url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json",
  "http_status": 200,
  "fetch_started_at": "2026-10-17T14:04:12.102345Z",
  "fetch_completed_at": "2026-10-17T14:04:12.655432Z",
  "duration_ms": 553.09,
  "bytes_received": 1433,
  "content_type": "application/json;charset=UTF-8",
  "body_sha256": "7debc1936f7b78d6d43ac93a4695fe13269f74e960a31726b80769de80fd1760",
  "status": "valid",
  "validation_level": "content",
  "reason": "Successfully parsed 5 EFFR reference rate records with valid dates and percentRate values.",
  "item_count": 5,
  "published_at": null,
  "checker_version": "pythia-probe-r1/1.0",
  "usable_for_forecast": false
}
```

### 5.2 Negative Probe Result Example (`bls_cpi_rss` — Blocked)

```json
{
  "source_id": "bls_cpi_rss",
  "initial_url": "https://www.bls.gov/feed/cpi.rss",
  "final_url": "https://www.bls.gov/feed/cpi.rss",
  "http_status": 403,
  "fetch_started_at": "2026-10-17T14:04:13.011222Z",
  "fetch_completed_at": "2026-10-17T14:04:13.307551Z",
  "duration_ms": 296.33,
  "bytes_received": 1152,
  "content_type": "text/html",
  "body_sha256": "3a8b419c8f...",
  "status": "blocked",
  "validation_level": "transport",
  "reason": "HTTP 403 Forbidden: Endpoint blocked by server-side automated client filtering / WAF.",
  "item_count": null,
  "published_at": null,
  "checker_version": "pythia-probe-r1/1.0",
  "usable_for_forecast": false
}
```

### 5.3 Negative Probe Result Example (`stream_limit_exceeded`)

```json
{
  "source_id": "oversized_mock_feed",
  "initial_url": "https://example.org/bloated_feed.xml",
  "final_url": "https://example.org/bloated_feed.xml",
  "http_status": 200,
  "fetch_started_at": "2026-10-17T14:04:14.000000Z",
  "fetch_completed_at": "2026-10-17T14:04:14.450000Z",
  "duration_ms": 450.0,
  "bytes_received": 2097153,
  "content_type": "application/xml",
  "body_sha256": null,
  "status": "invalid_content",
  "validation_level": "transport",
  "reason": "Payload exceeded maximum stream limit of 2097152 bytes (2 MiB); full body hash aborted.",
  "item_count": null,
  "published_at": null,
  "checker_version": "pythia-probe-r1/1.0",
  "usable_for_forecast": false
}
```
