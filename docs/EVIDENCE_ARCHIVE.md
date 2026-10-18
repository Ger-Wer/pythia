# Pythia Evidence Archive (R2.1)

**Status:** R2.1 Production Specification & Operational Guide  
**Module:** `python/evaluation/evidence_archive.py`  
**Storage Architecture:** Filesystem Content-Addressable Storage (CAS) + SQLite Database (`archive.db`)  
**Schema Version:** `pythia-archive-r2/2.0`  
**Collector Version:** `pythia-collector-r2.1/1.0`  
**Probe Version:** `pythia-probe-r2.1/1.0`

---

## 1. Directory Structure

The archive resides in an explicitly configured directory (`state_dir` or `--archive-dir`):

```
<state_dir>/
├── archive.db         # SQLite database in WAL mode (PRAGMA synchronous = FULL)
├── archive.db-shm     # SQLite shared-memory index
├── archive.db-wal     # SQLite write-ahead log
├── blobs/             # Content-addressable storage (CAS) of verbatim raw bodies
│   └── 7d/
│       └── eb/
│           └── 7debc1936f7b...bin
└── tmp/               # Scratch directory on same mount for atomic fsync + no-clobber publish
```

---

## 2. Raw Body & Content Decoding

- **Definition of Raw:** Verbatim content-decoded HTTP bytes returned by `requests.iter_content` (transparent decompression of `gzip` / `deflate`), prior to any charset decoding (e.g. UTF-8, GB18030) or document parsing.
- **Header Provenance:** Transport headers (`content-encoding`, `content-type`, `etag`, `last-modified`) are preserved verbatim in `headers_json`. Encoded `Content-Length` is never substituted for decoded byte length.
- **Exact Storage:** Blobs are written directly to binary files with no character normalization, no line-ending conversions, and no JSON re-formatting.

---

## 3. Database Schema & Null Semantics

### Receipts Table (`receipts`)

Append-only log of every completed fetch attempt:

```sql
CREATE TABLE receipts (
    receipt_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL UNIQUE,
    source_id TEXT NOT NULL,
    initial_url TEXT NOT NULL,
    final_url TEXT,
    http_status INTEGER,
    headers_json TEXT NOT NULL,
    fetch_started_at TEXT NOT NULL,
    fetch_completed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    bytes_received INTEGER,
    bytes_stored INTEGER,
    complete INTEGER NOT NULL,
    limit_exceeded INTEGER NOT NULL,
    error_msg TEXT,
    collector_version TEXT NOT NULL,
    status TEXT NOT NULL,
    validation_level TEXT NOT NULL,
    reason TEXT NOT NULL,
    checker_version TEXT NOT NULL,
    usable_for_forecast INTEGER NOT NULL DEFAULT 0,
    payload_fingerprint TEXT NOT NULL,
    blob_sha256 TEXT,
    revision_id TEXT,
    FOREIGN KEY (revision_id) REFERENCES revisions (revision_id)
);
```

- `final_url`: `NULL` if connection failed, DNS failed, or worker was killed before response. Never defaults to `initial_url`.
- `bytes_received` / `bytes_stored`: `NULL` if worker was terminated by timeout before transferring metrics.
- `limit_exceeded`: Strictly `0` or `1` boolean integer.
- `error_msg`: String error explanation or `NULL` if capture completed without error.
- `collector_version`: Explicit version string of capturing collector (e.g. `pythia-collector-r2.1/1.0`).
- `payload_fingerprint`: Canonical SHA-256 digest over all capture metadata, transport headers (key-sorted), body SHA-256, and diagnostics.
- `blob_sha256` / `revision_id`: `NULL` if capture was incomplete, timed out, or exceeded stream limit. Complete blocked (403) and unverified HTML receive a CAS blob and revision link.
- `usable_for_forecast`: Strictly `0` (`false`) on all records.

### Revisions Table (`revisions`)

Sequential history of complete body snapshots keyed by `(source_id, initial_url)`:

```sql
CREATE TABLE revisions (
    revision_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    initial_url TEXT NOT NULL,
    blob_sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    first_seen_at TEXT NOT NULL,
    first_seen_seq INTEGER NOT NULL,
    prev_revision_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (prev_revision_id) REFERENCES revisions (revision_id)
);
```

- `first_seen_at`: ISO 8601 UTC assigned by the archive at transaction commit of the first receipt introducing this revision.
- `first_seen_seq`: Sequence number `seq` of that first receipt.
- `prev_revision_id`: Self-referencing foreign key linking to preceding revision along this source's history.

---

## 4. Lineage Rules, Full Fingerprint Idempotency & Conflict Detection

### A → B → A Evolution
1. **Fetch 1 (Body A):** Creates Receipt 1, Revision 1 (`prev_revision_id = NULL`), Blob A.
2. **Fetch 2 (Body A):** Creates Receipt 2 (`revision_id = Revision 1`), Reuses Blob A. (2 receipts, 1 revision, 1 blob).
3. **Fetch 3 (Body B):** Creates Receipt 3, Revision 2 (`prev_revision_id = Revision 1`), Blob B. (3 receipts, 2 revisions, 2 blobs).
4. **Fetch 4 (Body A):** Creates Receipt 4, Revision 3 (`prev_revision_id = Revision 2`), Reuses Blob A. (4 receipts, 3 revisions, 2 blobs).

### Canonical Payload Fingerprint & Full Idempotency
- Every `CaptureResult` produces an immutable canonical payload fingerprint:
  $$	ext{fingerprint} = 	ext{SHA-256}(	ext{JSON}(	ext{source\_id}, 	ext{urls}, 	ext{status}, 	ext{headers\_sorted}, 	ext{timestamps}, 	ext{duration}, 	ext{counters}, 	ext{complete}, 	ext{limit}, 	ext{error}, 	ext{collector\_version}, 	ext{raw\_body\_sha256}, 	ext{diagnostics}))$$
- Calling `record_capture(capture, receipt_id="...")` with an already committed `receipt_id`:
  - If the fingerprint matches exactly: returns the existing `ArchiveReceipt` without creating duplicates or modifying SQLite.
  - If **any** metadata or payload field differs (ETag change, timestamp change, collector version change, or negative error change like timeout vs. resolver failed): immediately raises `ArchiveConflictError` before transaction commit.
- Under concurrency, double-check fingerprint verification under `BEGIN IMMEDIATE` lock guarantees that racing writers on identical payloads succeed idempotently, while racing writers on conflicting payloads yield exactly 1 winner and conflicts for all losers.

---

## 5. CAS Durability, No-Clobber Publication & Corruption Defense

1. **Pre-Reuse Integrity Verification:**
   - Before reusing an existing CAS blob file at `blobs/ab/cd/abcd...bin`, the archive checks both `st_size` and computes the actual SHA-256 of the on-disk file.
   - If the file was corrupted (e.g. byte flipped with same size, or truncated file): raises `ArchiveIntegrityError` without committing any receipt/revision and **without touching, overwriting, or repairing** the corrupted file (preserving diagnostic evidence).
2. **Atomic No-Clobber Publication & CAS Durability Barrier (`_fsync_cas_barrier`):**
   - Raw body is written to a temporary staging file in `tmp/`, flushed, and fsynced with `os.fsync(fd)`.
   - The file is published to the target CAS destination using `os.link` (atomic no-clobber publish). If destination already exists due to a race, the appearing file is verified for size and SHA-256.
   - Durability barrier: on all three CAS paths (new publish, existing blob reuse, concurrent race loser), file bytes and all required directory entries (`blobs/aa/bb`, `blobs/aa`, `blobs`) are synchronized via `_fsync_cas_barrier` before returning from `_store_blob` and before beginning the SQLite database transaction.
   - If an error occurs during directory fsync, no receipt or revision is committed to SQLite.
   - *Fixture-Only Routing:* Production worker runtime (`source_probe_worker.py`) does not read ambient environment mappings. Test HTTP redirects and loopback adapters are isolated strictly to test fixtures via explicit `worker_cmd`.
3. **SQLite WAL & Durability Guarantees:**
   - Database operates with `PRAGMA journal_mode = WAL;`, `PRAGMA synchronous = FULL;`, `PRAGMA foreign_keys = ON;`, and `busy_timeout = 10000;`.
   - **Crash Guarantee:** If a writer process crashes (SIGKILL) after CAS publish but before SQLite commit, an orphaned uncommitted blob remains in `blobs/`, but SQLite contains no receipt or revision. The archive remains 100% healthy.
   - If a crash occurs inside the SQLite transaction before `COMMIT`, SQLite WAL recovery automatically rolls back all uncommitted rows on subsequent open.
   - *Scope Note:* Software process crash tests (SIGKILL) prove state machine and transaction rollback guarantees; they do not simulate underlying physical drive controller failures or magnetic media decay.

---

## 6. Legacy Archive Preservation (`pythia-archive-r2/1.0`)

- The v1 archive directory `/home/pythia/pythia-evidence-r2-data/live-r2-run1` is permanently preserved.
- `EvidenceArchive` inspects existing databases via a **read-only SQLite connection** (`?mode=ro`) before executing any DDL or PRAGMAs.
- When an incompatible schema version (such as `pythia-archive-r2/1.0`) is detected, `ArchiveIntegrityError` is raised immediately. Not a single byte or filesystem timestamp (`mtime`) is altered.
- Legacy archives remain accessible via pinned commit `7854de2` or standard read-only SQLite tools.

---

## 7. Point-in-Time Cutoff API (`as_of_seq`)

All query methods accept an optional `as_of_seq: int` parameter:

- `get_receipt(receipt_id, as_of_seq=K)`: returns `None` if receipt was committed after sequence `K`.
- `get_revision(revision_id, as_of_seq=K)`: returns `None` if revision first appeared after sequence `K`.
- `get_latest_revision(source_id, url, as_of_seq=K)`: returns the revision active at sequence `K`. Subsequent revisions are invisible.
- `query_receipts(as_of_seq=K)`: filters receipts with `seq <= K`.

---

## 8. CLI Reference & Verification Commands

### CLI Syntax

```bash
# Verify archive integrity
python -m python.evaluation.evidence_archive --state-dir <path> --verify

# Capture single source into archive
python -m python.evaluation.evidence_archive --state-dir <path> --capture-source nyfed_effr

# Capture all 8 canonical sources sequentially
python -m python.evaluation.evidence_archive --state-dir <path> --capture-all

# List receipts (with optional cutoff)
python -m python.evaluation.evidence_archive --state-dir <path> --list-receipts [--as-of-seq N] [--source SOURCE]

# Get latest revision for source (with optional cutoff)
python -m python.evaluation.evidence_archive --state-dir <path> --latest-revision --source nyfed_effr [--as-of-seq N]

# Read raw blob from CAS by hash
python -m python.evaluation.evidence_archive --state-dir <path> --read-blob <sha256>
```

### Verification on amper

```bash
# 1. Run all tests (173 passed)
cd /home/pythia/pythia-evidence-r2
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests/ -v

# 2. Run live capture across 8 canonical endpoints into revision1 directory
mkdir -p /home/pythia/pythia-evidence-r2-data/live-r2-revision1
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -m python.evaluation.evidence_archive     --state-dir /home/pythia/pythia-evidence-r2-data/live-r2-revision1 --capture-all

# 3. Verify database and CAS integrity
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -m python.evaluation.evidence_archive     --state-dir /home/pythia/pythia-evidence-r2-data/live-r2-revision1 --verify
```
