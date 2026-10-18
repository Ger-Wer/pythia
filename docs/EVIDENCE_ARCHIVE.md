# Pythia Evidence Archive (R2)

**Status:** R2 Production Specification & Operational Guide  
**Module:** `python/evaluation/evidence_archive.py`  
**Storage Architecture:** Filesystem Content-Addressable Storage (CAS) + SQLite Database (`archive.db`)

---

## 1. Directory Structure

The archive resides in an explicitly configured directory (`state_dir`):

```
<state_dir>/
├── archive.db         # SQLite database in WAL mode with append-only triggers
├── archive.db-shm     # SQLite shared-memory index
├── archive.db-wal     # SQLite write-ahead log
├── blobs/             # Content-addressable storage (CAS) of verbatim raw bodies
│   └── 7d/
│       └── eb/
│           └── 7debc1936f7b...bin
└── tmp/               # Scratch directory on same mount for atomic fsync + rename
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
    status TEXT NOT NULL,
    validation_level TEXT NOT NULL,
    reason TEXT NOT NULL,
    checker_version TEXT NOT NULL,
    usable_for_forecast INTEGER NOT NULL DEFAULT 0,
    blob_sha256 TEXT,
    revision_id TEXT,
    FOREIGN KEY (revision_id) REFERENCES revisions (revision_id)
);
```

- `final_url`: `NULL` if connection failed, DNS failed, or worker was killed before response. Never defaults to `initial_url`.
- `bytes_received` / `bytes_stored`: `NULL` if worker was terminated by timeout before transferring metrics.
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

## 4. Lineage Rules & Idempotency

### A → B → A Evolution
1. **Fetch 1 (Body A):** Creates Receipt 1, Revision 1 (`prev_revision_id = NULL`), Blob A.
2. **Fetch 2 (Body A):** Creates Receipt 2 (`revision_id = Revision 1`), Reuses Blob A. (2 receipts, 1 revision, 1 blob).
3. **Fetch 3 (Body B):** Creates Receipt 3, Revision 2 (`prev_revision_id = Revision 1`), Blob B. (3 receipts, 2 revisions, 2 blobs).
4. **Fetch 4 (Body A):** Creates Receipt 4, Revision 3 (`prev_revision_id = Revision 2`), Reuses Blob A. (4 receipts, 3 revisions, 2 blobs).

### Idempotency & Conflict Resolution
- Calling `record_capture(capture, receipt_id="...")` with an already committed `receipt_id`:
  - If the payload (`source_id`, `initial_url`, `final_url`, `http_status`, `complete`, content hash) matches: returns the existing `ArchiveReceipt` without creating duplicates.
  - If the payload differs: raises `ArchiveConflictError`.

---

## 5. Failure Recovery & Storage Invariants

1. **Blob-before-DB Commit:**
   - Raw body is written to `tmp/blob_*.tmp`, fsynced via `os.fsync(fd)`, and atomically renamed to `blobs/ab/cd/abcd...bin` before the SQLite transaction begins.
   - If a crash occurs between fsync and commit, the database rolls back cleanly; no committed receipt or revision can ever reference a missing blob.
2. **Append-Only Triggers:**
   - SQLite triggers `trg_receipts_no_update`, `trg_receipts_no_delete`, `trg_revisions_no_update`, and `trg_revisions_no_delete` reject any `UPDATE` or `DELETE` with `ABORT`.
3. **WAL Mode & Concurrency:**
   - Database operates with `PRAGMA journal_mode = WAL;`, `PRAGMA synchronous = NORMAL;`, `PRAGMA foreign_keys = ON;`, and `busy_timeout = 10000;`. Concurrent writes serialize via `BEGIN IMMEDIATE`.

---

## 6. Point-in-Time Cutoff API (`as_of_seq`)

All query methods accept an optional `as_of_seq: int` parameter:

- `get_receipt(receipt_id, as_of_seq=K)`: returns `None` if receipt was committed after sequence `K`.
- `get_revision(revision_id, as_of_seq=K)`: returns `None` if revision first appeared after sequence `K`.
- `get_latest_revision(source_id, url, as_of_seq=K)`: returns the revision active at sequence `K`. Subsequent revisions are invisible.
- `query_receipts(as_of_seq=K)`: filters receipts with `seq <= K`.

---

## 7. CLI Reference & Verification Commands

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
# 1. Run all tests (165 passed)
cd /home/pythia/pythia-evidence-r2
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/pytest python/evaluation/tests/ -v

# 2. Run live capture across 8 canonical endpoints
mkdir -p /home/pythia/pythia-evidence-r2-data/live-r2-run1
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -m python.evaluation.evidence_archive \
    --state-dir /home/pythia/pythia-evidence-r2-data/live-r2-run1 --capture-all

# 3. Verify database and CAS integrity
/home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3 -m python.evaluation.evidence_archive \
    --state-dir /home/pythia/pythia-evidence-r2-data/live-r2-run1 --verify
```
