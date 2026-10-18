# Pythia: Evidence Data Contract

**Status:** R2 Specification (Diagnostic Probe Layer 0 & Immutable Evidence Archive Layer 1 Implemented).  
**Date:** 2026-10-18.  
**Scope:** Defines the data contract for diagnostic probe runs (Layer 0), single-fetch capture results (`CaptureResult`), immutable content-addressable document revisions and attempt receipts (Layer 1, R2), and future extracted claims (Layer 2, R3+).

---

## 1. Architectural Principles

1. **Separation of Layers:**
   - **Capture Result (Boundary DTO, R2):** A single typed transport attempt capturing raw content-decoded bytes, headers, timings, and EOF/error status. Shared between diagnostic probe and archive storage without double fetching.
   - **Probe Result (Layer 0, R1/R2):** An ephemeral diagnostic check of reachability, transport, envelope, and content schema under strict resource bounds. A probe run does **not** constitute a historical archive or a `first_seen` timeline.
   - **Evidence Document & Archive (Layer 1, R2):** An immutable, append-only captured document storage combining filesystem CAS (raw blobs addressed by SHA-256) and SQLite (`archive.db` in WAL mode with append-only triggers). Receipts track every attempt (including blocked, unverified, invalid, and timeout); revisions track content evolution along `(source_id, initial_url)` with parent lineage.
   - **Claim & Event Dossier (Layer 2, R3):** Verbatim quote, character anchors, normalized quantitative claims, and linkage to prediction markets.
2. **Date Lineage & Null Semantics:**
   - `published_at`, `updated_at`, `first_seen_at`, `recorded_at`, and `fetch_started_at`/`fetch_completed_at` are distinct fields with explicit provenance and precision.
   - If an official publication date cannot be proven from document content or authoritative metadata, `published_at` **must be `null`**. It must **never** be replaced with HTTP `Last-Modified` or `fetched_at`/`recorded_at`.
   - Date-only disclosures (e.g. `2026-10-16`) must record precision as `"day"`. The collector must **never** synthesize an artificial time of day (such as `00:00:00Z`) to fabricate second-level precision.
   - `first_seen_at` requires persistent chronological tracking across repeated collections; an isolated one-off probe cannot fabricate a historical `first_seen_at`.
   - `recorded_at` is assigned exclusively by the archive in UTC at SQLite transaction commit time; caller-supplied timestamps are never substituted for archive recording time.
3. **Verbatim & Original First:**
   - The raw byte content is preserved verbatim in filesystem CAS.
   - Raw body is defined as the exact **content-decoded HTTP body** returned by `requests.iter_content` (transparent decompression of gzip/deflate), prior to any charset decoding (UTF-8, etc.) or parsing.
   - Translated text is strictly separated from original text and explicitly attributed to a translation engine and version.
4. **Anonymous & Bounded Transport:**
   - All diagnostic probes and evidence collectors operate with anonymous transport: ambient environment credentials (`.netrc`, ambient proxy variables) are disabled (`trust_env = False`), cookie persistence and transmission are blocked, and TLS certificate verification is enforced.
   - Every source request is bounded by an **overall deadline of 15.0 seconds** (covering worker startup, DNS resolution, TCP/TLS connect, redirects, stream read, and complete IPC transmission) enforced strictly via an isolated short-lived worker process under parent deadline control via `subprocess.Popen` and `communicate(input=..., timeout=remaining)`. Canonical source_id and exact initial URL are strictly verified **prior to Popen**.
   - **Mandatory Worker IPC Schema (R2.1):** The worker receives JSON on `stdin` and must emit a single JSON object on `stdout` containing all 9 mandatory keys: `status_code`, `headers`, `body_b64`, `bytes_received`, `bytes_stored`, `complete`, `limit_exceeded`, `final_url`, `error_msg`. Any missing key triggers an immediate protocol error (no silent defaulting). `complete` and `limit_exceeded` must be strictly boolean (Python `bool`), counters and status must be strict non-negative integers (`bool` is rejected as integer), headers must be an object with string keys and string values, and `body_b64` is validated via `validate=True` (trailing garbage rejected).
   - **Explicit EOF & Completeness Invariant:** A response is marked `complete=True` if and only if explicit stream EOF was observed, HTTP status is in [100, 599], final URL is non-empty, error message is null, stream limit was not exceeded, and counters match exactly: `bytes_received == bytes_stored == len(body)`. An empty body is only complete if explicitly accompanied by 0 byte counters and clean HTTP status.
   - Parent calculates `deadline = monotonic() + budget` before `Popen`. After `communicate` returns, the parent validates process exit code, JSON schema, body bounds, and re-checks `elapsed <= budget` before accepting any response as valid; late responses even if well-formed are rejected as `network_error` with `null` hash and counters.
   - The final parent deadline check, duration measurement, and `fetch_completed_at` assignment occur **after** worker JSON decoding, base64 extraction, and unified `validate_capture_result` candidate validation, immediately before returning the finished `CaptureResult`. If the deadline is exceeded during parent validation, the response is rejected as a negative capture without revision. Disk write/archival is not part of transport budget.
   - On timeout, the parent enforces termination via a sequence of `terminate()` -> grace (0.15s) -> `kill()` (SIGKILL) -> `wait()` (reap) sharing a single bounded cleanup allowance of **<= 0.50 seconds** over the operational budget. All pipe handles are closed. `duration_ms` is strictly measured by parent monotonic clock covering startup, IPC, execution, and cleanup.
   - Streaming memory buffer is capped at **2 MiB** (2,097,152 bytes). An oversized response is immediately truncated at 2 MiB, closing the stream and invalidating the integrity hash (`body_sha256 = null`).
   - If a worker process is forcibly killed by timeout or terminated unexpectedly before metric transmission, actual transferred byte counters cannot be fabricated; `bytes_received`, `bytes_stored`, `http_status`, and `final_url` are reported as `null` with a clear explanation in `reason`. Initial URL remains known, but is never substituted for `final_url`.
5. **Zero Edge Assumption:**
   - Neither probe validity nor document storage implies trading edge or forecast usability. Every record in R1, R1.3, and R2 carries `usable_for_forecast: false`.

---

## 2. Probe Result Contract (Layer 0 Diagnostic Layer)

A probe run tests whether an endpoint is reachable and conforms to expected transport and envelope schemas under strict bounds.

### 2.1 Schema Definition

| Field | Type | Nullable | Description |
|---|---|---|---|
| `source_id` | `string` | No | Unique identifier matching one of the 8 canonical sources in `source_registry.json`. |
| `platform` | `string` | No | Publishing organization or platform. |
| `family` | `string` (enum) | No | Domain classification: `"macro"` or `"ai"`. |
| `initial_url` | `string` (URI) | No | Configured canonical URL requested by the probe. |
| `final_url` | `string` (URI) | Yes | Destination URL after following redirects (max 3 allowed within domain allowlist). **Must be `null`** if network error occurred before response or worker was killed by timeout. |
| `http_status` | `integer` | Yes | HTTP status code (e.g. `200`, `403`). `null` if network error occurred before response or worker was killed by timeout. |
| `fetch_started_at` | `string` (ISO 8601 UTC) | No | UTC timestamp when request was initiated. |
| `fetch_completed_at` | `string` (ISO 8601 UTC) | No | UTC timestamp when response stream closed or timed out. |
| `duration_ms` | `float` | No | Elapsed request duration in milliseconds (measured by parent clock, bounded by overall 15.0s deadline + cleanup margin). |
| `bytes_received` | `integer` | Yes | Total bytes read from socket/stream up to truncation boundary, or `null` if worker process was forcibly terminated by timeout before counters could be reported. |
| `bytes_stored` | `integer` | Yes | Bytes retained in buffer (strictly capped at 2 MiB / 2,097,152 bytes), or `null` if worker process was forcibly terminated by timeout. |
| `content_type` | `string` | Yes | `Content-Type` header from response, or `null` if unavailable. |
| `body_sha256` | `string` (hex 64) | Yes | SHA-256 of complete payload. **Must be `null`** if stream was truncated, interrupted, timed out, blocked, or unverified. (Raw integrity hash for blocked/unverified snapshots is preserved in Layer 1 CAS). |
| `status` | `string` (enum) | No | Probe status (see §2.2). |
| `validation_level` | `string` (enum) | No | Depth of check passed/attempted (see §2.3). |
| `reason` | `string` | No | Clear, human-readable justification for the status and level. |
| `item_count` | `integer` | Yes | Number of verified items/releases/records parsed (e.g. 5 refRates, 0 releases). `null` for unparsed HTML. |
| `published_at` | `string` (ISO 8601 UTC) | Yes | Strictly `null` in R1/R2 probes. |
| `checker_version` | `string` | No | Version of probe software (e.g. `pythia-probe-r2.1/1.0`). |
| `usable_for_forecast` | `boolean` | No | Always `false` in R1/R2. |

### 2.2 Status Enumeration (`status`)

- `valid`: The response satisfied all transport, envelope, and expected content structure checks for this specific endpoint (exact series match, valid calendar date, finite numeric values, verified feed category identity and XML integrity, valid release paths).
- `blocked`: Access denied by HTTP 401/403/429, WAF, Cloudflare challenge, or CAPTCHA.
- `invalid_content`: The server returned HTTP 200, but payload structure does not match the expected contract (e.g. error HTML instead of RSS, dictionary instead of releases array, missing `refRates`, invalid series `NOT_EFFR`, non-string tag, empty model name, unrelated sports feed, wrong feed category such as Fed Banking Orders for monetary policy or BLS Employment Situation for CPI, malformed or truncated XML).
- `network_error`: Connection refused, DNS failure, overall deadline exceeded (> 15.0s), or redirect to an untrusted domain.
- `unverified`: Transport and envelope succeeded, but substantive data extraction was not performed or verified in this stage (e.g. Atlanta Fed GDPNow HTML, LMSYS Arena HTML).

### 2.3 Validation Level Enumeration (`validation_level`)

- `transport`: Only TCP/TLS connection, stream limit boundaries, and HTTP status code were evaluated. Stream overflow truncation and process deadline termination report at this level.
- `envelope`: Transport succeeded, and top-level format envelope was confirmed (e.g. valid JSON parsing, non-truncated XML ElementTree parse with supported `<rss>`/`<feed>` root, valid HTML `<html>` document). Truncated XML fails at this level.
- `content`: Substantive inner structure, required field types, and entity identifiers were confirmed (e.g. `refRates` contains numeric `percentRate` and exact `EFFR` type; GitHub releases contain repo references; RSS contains expected category items with official domain links). Feed category mismatch fails at this level.

### 2.4 Common Capture DTO (`CaptureResult`)

A single fetch attempt produces a typed `CaptureResult` instance:

```python
@dataclass(frozen=True)
class CaptureResult:
    source_id: str
    initial_url: str
    final_url: Optional[str]        # Nullable if error/killed before response
    body: bytes                     # Content-decoded raw HTTP body
    status_code: Optional[int]
    headers: Dict[str, str]         # Canonical lowercase response headers
    fetch_started_at: str           # ISO 8601 UTC
    fetch_completed_at: str         # ISO 8601 UTC
    duration_ms: float              # Parent measured wall-clock milliseconds
    bytes_received: Optional[int]
    bytes_stored: Optional[int]
    complete: bool                  # True strictly on clean EOF without error/overflow
    limit_exceeded: bool
    error_msg: Optional[str]
    collector_version: str
```

Both Layer 0 (`SourceProbe.probe_single`) and Layer 1 (`EvidenceArchive.record_capture`) accept this single `CaptureResult`, preventing duplicate network fetches.

---

## 3. Evidence Archive Contract (Layer 1 Storage Layer, R2)

The evidence archive is an immutable, append-only persistence layer combining filesystem content-addressable storage (CAS) and SQLite (`archive.db`).

### 3.1 Architecture Overview

- **State Directory (`state_dir`):** An explicitly configured isolated path (never production DB, never committed to git).
  - `blobs/ab/cd/abcd...bin`: Content-addressable storage of full body payloads keyed by SHA-256. Fsynced and atomically published via `os.link` before SQLite transaction commit. Deduplicated across revisions and sources.
  - `tmp/`: Same-filesystem scratch directory for atomic writes.
  - `archive.db`: SQLite database in WAL mode (`journal_mode = WAL`, `synchronous = FULL`, `foreign_keys = ON`, `busy_timeout = 10000`).
- **Append-Only Invariant:** SQLite triggers forbid `UPDATE` and `DELETE` on `receipts` and `revisions`.
- **Strict Typed CaptureResult Contract:** Typed dataclass validated strictly at the archive boundary before computing payload fingerprint or persistent storage. URL/source/version/time/error have exact required types (no `str()` coercion); `headers` must be `dict[str, str]` without case-variant collisions; `duration_ms` must be finite non-negative number (`bool` is not numeric); `body` must be `bytes` within the 2 MiB stream limit; timestamps must be valid ISO 8601 UTC strings. Candidate validation applies `validate_capture_result` uniformly after worker IPC parsing, including incomplete captures. Case-variant header collisions (e.g., conflicting values for `ETag` and `etag`) are detected prior to lowercase conversion. Post-validation deadline check ensures that any deadline crossed during parent validation yields a valid negative capture with `complete=False` and non-empty error reason for recording negative receipts without revision or CAS blob creation. Malformed direct DTOs raise `ValueError`.
- **Fixture-Only Test Routing & Offline Default Worker:** Production runtime worker (`source_probe_worker.py`) does not read ambient loopback mappings or contain test redirect adapters. All test-routing and mock adapters reside strictly in test fixtures using explicit `worker_cmd`. Default worker is tested offline under a test-only `sitecustomize.py` DNS guard verifying resolver entry without network transit.
- **Canonical Payload Fingerprint & Strict Idempotency:** Every capture generates a deterministic SHA-256 fingerprint over all transport metadata, timings, durations, byte counters, raw body SHA-256, key-sorted headers, and diagnostic results. Re-recording an identical `receipt_id` with matching fingerprint returns the existing receipt. Re-recording a `receipt_id` with any differing metadata or payload (e.g. ETag change, fetch time, collector version, or error reason) raises `ArchiveConflictError`. Under concurrency, double-checked fingerprint verification inside `BEGIN IMMEDIATE` transaction guarantees exactly 1 winner and conflicts for all competing payloads.
- **CAS Durability Barrier & Base Ancestry Chain:** Stored blobs enforce a filesystem durability barrier (`_fsync_cas_barrier`) across all paths (new publish, existing blob reuse, concurrent race loser): file bytes and all directory entries along the entire ancestry chain from the leaf directory (`blobs/aa/bb`) up to the filesystem root (`/`) — including `blobs/aa`, `blobs`, `state_dir`, and all newly created parent directories — are fsynced before returning from `_store_blob` and before SQLite transaction commit. Existing CAS blobs are verified for size and actual SHA-256 before reuse; any corrupted file raises `ArchiveIntegrityError` without being overwritten.
- **Durability & WAL:** Database operates with `PRAGMA journal_mode = WAL;` and `PRAGMA synchronous = FULL;`. Uncommitted process crashes leave orphaned blobs while SQLite remains 100% consistent; crashes inside DB transaction are rolled back by WAL recovery.
- **Scope of Guarantees:** Software process crash tests (SIGKILL) prove state machine and transaction rollback guarantees; they do not simulate underlying physical drive controller failures or magnetic media decay.
- **Legacy Schema Preservation:** Legacy archive `live-r2-run1` (schema `pythia-archive-r2/1.0`) is discovered via read-only inspection and strictly preserved with zero modification. Active schema is `pythia-archive-r2/2.0`.
- **Point-in-Time Cutoff (`as_of_seq`):** Historical state queries filtered by monotonic sequence number `seq <= as_of_seq` render subsequent receipts and revisions invisible without knowledge leakage from the future.
- **Usability Invariant:** Every receipt and revision in R2 and R2.1 carries `usable_for_forecast: false`.

### 3.2 Attempt Receipt Schema (`receipts` table)

Every completed fetch attempt (including blocked, unverified, invalid, and timeout) is recorded in the append-only `receipts` table:

| Column | Type | Constraints | Description |
|---|---|---|---|
| `receipt_id` | `TEXT` | `PRIMARY KEY` | Unique attempt identifier (UUIDv7 or UUIDv4). |
| `seq` | `INTEGER` | `NOT NULL UNIQUE` | Monotonic sequential attempt number assigned by SQLite. |
| `source_id` | `TEXT` | `NOT NULL` | References one of the 8 canonical sources. |
| `initial_url` | `TEXT` | `NOT NULL` | Requested canonical URL. |
| `final_url` | `TEXT` | Nullable | Actually reached URL, or `NULL` if failed before response. |
| `http_status` | `INTEGER` | Nullable | HTTP status code (e.g. 200, 403), or `NULL`. |
| `headers_json` | `TEXT` | `NOT NULL` | JSON-encoded canonical response headers. |
| `fetch_started_at` | `TEXT` | `NOT NULL` | UTC ISO 8601 when fetch began. |
| `fetch_completed_at`| `TEXT` | `NOT NULL` | UTC ISO 8601 when fetch finished. |
| `recorded_at` | `TEXT` | `NOT NULL` | UTC ISO 8601 assigned by archive at commit time. |
| `duration_ms` | `REAL` | `NOT NULL` | Parent measured execution time in milliseconds. |
| `bytes_received` | `INTEGER` | Nullable | Total stream bytes transferred, or `NULL`. |
| `bytes_stored` | `INTEGER` | Nullable | Bytes retained in buffer (max 2 MiB), or `NULL`. |
| `complete` | `INTEGER` | `NOT NULL` | 1 if clean EOF without error/overflow; 0 otherwise. |
| `limit_exceeded` | `INTEGER` | `NOT NULL` | 1 if response stream exceeded 2 MiB limit; 0 otherwise. |
| `error_msg` | `TEXT` | Nullable | Error message string or `NULL` if successful. |
| `collector_version` | `TEXT` | `NOT NULL` | Version string of collector (e.g. `pythia-collector-r2.1/1.0`). |
| `status` | `TEXT` | `NOT NULL` | Diagnostic status (`valid`, `blocked`, `invalid_content`, `network_error`, `unverified`). |
| `validation_level` | `TEXT` | `NOT NULL` | Diagnostic depth (`transport`, `envelope`, `content`). |
| `reason` | `TEXT` | `NOT NULL` | Human-readable explanation. |
| `checker_version` | `TEXT` | `NOT NULL` | Collector/checker build version. |
| `usable_for_forecast`| `INTEGER` | `NOT NULL DEFAULT 0` | Strictly 0 in R2/R2.1. |
| `payload_fingerprint`| `TEXT` | `NOT NULL` | Canonical SHA-256 fingerprint of full capture and diagnostic payload. |
| `blob_sha256` | `TEXT` | Nullable | SHA-256 foreign key into CAS blob. `NULL` for incomplete/timeout. |
| `revision_id` | `TEXT` | Nullable | Foreign key into `revisions`. `NULL` for incomplete/timeout. |

### 3.3 Document Revision Schema (`revisions` table)

Tracks the sequential history of complete bodies keyed by `(source_id, initial_url)`:

| Column | Type | Constraints | Description |
|---|---|---|---|
| `revision_id` | `TEXT` | `PRIMARY KEY` | Unique revision identifier. |
| `source_id` | `TEXT` | `NOT NULL` | References canonical source. |
| `initial_url` | `TEXT` | `NOT NULL` | Canonical origin URL. |
| `blob_sha256` | `TEXT` | `NOT NULL` | SHA-256 of complete body stored in CAS. |
| `byte_size` | `INTEGER` | `NOT NULL` | Exact byte length of body. |
| `first_seen_at` | `TEXT` | `NOT NULL` | UTC ISO 8601 when this revision was first committed. |
| `first_seen_seq` | `INTEGER` | `NOT NULL` | Sequence number of the first receipt introducing this revision. |
| `prev_revision_id` | `TEXT` | Nullable | Foreign key to preceding revision along this chain (`NULL` for initial). |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO 8601 creation timestamp. |

**Revision Lineage Rules:**
1. A new fetch returning identical bytes adds a receipt pointing to the existing `revision_id`.
2. A new fetch returning modified bytes creates a new `revision_id` pointing to `prev_revision_id`.
3. An $A \to B \to A$ evolution produces 3 revisions and 2 CAS blobs.
4. Incomplete, timed-out, or truncated attempts do not alter revision history.


---

## 4. Corpus Quality & Integrity Checks (Future R4 Verification Rules)

1. **Revisions vs. New Events:**
   - If `canonical_url` matches an existing record but `content_sha256` differs, create a new revision node (`revision_index + 1`, linking to `previous_evidence_id`). Never overwrite the previous record.
2. **Duplicate Origin Suppression:**
   - Multiple syndication feeds publishing the same press release (e.g. PR Newswire, Yahoo Finance, Reuters wire) must be clustered by content hash and entity origin. They do not count as independent confirming evidence.
3. **Unknown Date Handling:**
   - If no explicit date is published in the document body or structured API, `published_at` remains `null`. Date-only publications retain precision `"day"` without time extrapolation.
4. **Number and Unit Consistency:**
   - Numerical figures extracted from claims must be checked against unit rules. Values exceeding financial bounds trigger automatic rejection.
5. **Translation Isolation:**
   - Original non-English text must never be replaced by machine translation in the primary record. Translation is stored as a derived annotation.
6. **Anti-Bot & CAPTCHA Rejection:**
   - Any response containing Cloudflare Turnstile, hCaptcha, Akamai Ghost challenge, or standard login screens must be classified as `blocked` or `invalid_content`. Under no circumstances may an error challenge page be stored as an authoritative document.
7. **Empty Client-Side JavaScript Shells:**
   - Responses returning HTTP 200 with minimal HTML (e.g. `<div id="root"></div>` without server-rendered data) must be classified as `unverified` or `invalid_content`.

---

## 5. Contract Examples

### 5.1 Positive Probe Result Example (`nyfed_effr`)

```json
{
  "source_id": "nyfed_effr",
  "platform": "Federal Reserve Bank of New York",
  "family": "macro",
  "initial_url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json",
  "final_url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json",
  "http_status": 200,
  "fetch_started_at": "2026-10-17T15:12:29.683086Z",
  "fetch_completed_at": "2026-10-17T15:12:30.105391Z",
  "duration_ms": 422.28,
  "bytes_received": 1433,
  "bytes_stored": 1433,
  "content_type": "application/json;charset=utf-8",
  "body_sha256": "7debc1936f7b78d6d43ac93a4695fe13269f74e960a31726b80769de80fd1760",
  "status": "valid",
  "validation_level": "content",
  "reason": "Successfully validated 5 EFFR reference rate records.",
  "item_count": 5,
  "published_at": null,
  "checker_version": "pythia-probe-r1.3/1.0",
  "usable_for_forecast": false
}
```

### 5.2 Negative Probe Result Example (`bls_cpi_rss` — Blocked)

```json
{
  "source_id": "bls_cpi_rss",
  "platform": "Bureau of Labor Statistics (U.S. Department of Labor)",
  "family": "macro",
  "initial_url": "https://www.bls.gov/feed/cpi.rss",
  "final_url": "https://www.bls.gov/feed/cpi.rss",
  "http_status": 403,
  "fetch_started_at": "2026-10-17T15:12:30.346272Z",
  "fetch_completed_at": "2026-10-17T15:12:30.772384Z",
  "duration_ms": 426.07,
  "bytes_received": 1323,
  "bytes_stored": 1323,
  "content_type": "text/html",
  "body_sha256": null,
  "status": "blocked",
  "validation_level": "transport",
  "reason": "HTTP 403 client access restricted by remote host / automated protection.",
  "item_count": null,
  "published_at": null,
  "checker_version": "pythia-probe-r1.3/1.0",
  "usable_for_forecast": false
}
```

### 5.3 Negative Probe Result Example (`stream_limit_exceeded`)

```json
{
  "source_id": "nyfed_effr",
  "platform": "Federal Reserve Bank of New York",
  "family": "macro",
  "initial_url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json",
  "final_url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json",
  "http_status": 200,
  "fetch_started_at": "2026-10-17T15:12:31.000000Z",
  "fetch_completed_at": "2026-10-17T15:12:31.450000Z",
  "duration_ms": 450.0,
  "bytes_received": 2105344,
  "bytes_stored": 2097152,
  "content_type": "application/json",
  "body_sha256": null,
  "status": "invalid_content",
  "validation_level": "transport",
  "reason": "Payload exceeded maximum stream limit of 2097152 bytes (2 MiB); full body hash not computed.",
  "item_count": null,
  "published_at": null,
  "checker_version": "pythia-probe-r1.3/1.0",
  "usable_for_forecast": false
}
```
