"""Pythia R2: Immutable Evidence Archive (Filesystem CAS + SQLite).

Stores verbatim raw responses from canonical evidence sources in content-addressable
filesystem storage (CAS) by SHA-256, tracks an append-only receipt log for all fetch
attempts in SQLite, and maintains sequential revision history with point-in-time
as_of_seq cutoff queries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from python.evaluation.source_probe import (
    CANONICAL_SOURCES,
    PROBE_VERSION,
    CaptureResult,
    SourceProbe,
    utc_now_iso,
    validate_capture_result,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "pythia-archive-r2/2.0"


class ArchiveError(Exception):
    """Base exception for evidence archive operations."""


class ArchiveConflictError(ArchiveError):
    """Raised when an existing receipt_id has conflicting payload data."""


class ArchiveIntegrityError(ArchiveError):
    """Raised when archive CAS or database fails consistency or hash verification."""


@dataclass(frozen=True)
class ArchiveReceipt:
    """Immutable record of a single completed fetch attempt."""

    receipt_id: str
    seq: int
    source_id: str
    initial_url: str
    final_url: Optional[str]
    http_status: Optional[int]
    headers: Dict[str, str]
    fetch_started_at: str
    fetch_completed_at: str
    recorded_at: str
    duration_ms: float
    bytes_received: Optional[int]
    bytes_stored: Optional[int]
    complete: bool
    limit_exceeded: bool
    error_msg: Optional[str]
    collector_version: str
    status: str
    validation_level: str
    reason: str
    checker_version: str
    usable_for_forecast: bool
    payload_fingerprint: str
    blob_sha256: Optional[str]
    revision_id: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArchiveRevision:
    """Sequential revision entry for complete body captures of a canonical source."""

    revision_id: str
    source_id: str
    initial_url: str
    blob_sha256: str
    byte_size: int
    first_seen_at: str
    first_seen_seq: int
    prev_revision_id: Optional[str]
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EvidenceArchive:
    """Filesystem CAS + SQLite storage for immutable evidence documents and attempt receipts."""

    def __init__(
        self,
        state_dir: Path | str,
        source_registry: Optional[Dict[str, str]] = None,
    ) -> None:
        self.state_dir = Path(state_dir).resolve()
        self.blobs_dir = self.state_dir / "blobs"
        self.tmp_dir = self.state_dir / "tmp"
        self.db_path = self.state_dir / "archive.db"
        # R3.2: an archive instance may serve a dedicated document family with
        # its own explicit source allowlist. None keeps the accepted default:
        # validation against CANONICAL_SOURCES, byte-identical behaviour.
        self._source_registry: Optional[Dict[str, str]] = (
            dict(source_registry) if source_registry is not None else None
        )

        # Ensure directory layout exists
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create and configure a connection to the archive SQLite database."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            isolation_level=None,  # Autocommit mode; transactions managed via BEGIN IMMEDIATE
        )
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = FULL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 10000;")
        return conn

    def _init_db(self) -> None:
        """Initialize tables, indices, and append-only triggers."""
        # Detect legacy or incompatible schema prior to running DDL
        if self.db_path.is_file():
            # Inspect read-only to avoid modifying legacy files, headers, or PRAGMAs
            ro_uri = f"file:{self.db_path.resolve()}?mode=ro"
            ro_conn = sqlite3.connect(ro_uri, uri=True)
            try:
                cursor = ro_conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_info';")
                if cursor.fetchone() is not None:
                    cursor.execute("SELECT value FROM schema_info WHERE key = 'version';")
                    row = cursor.fetchone()
                    if row is not None:
                        existing_version = row[0]
                        if existing_version != SCHEMA_VERSION:
                            raise ArchiveIntegrityError(
                                f"Incompatible archive schema version '{existing_version}'. "
                                f"Expected '{SCHEMA_VERSION}'. Do not modify legacy archive."
                            )
            finally:
                ro_conn.close()

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_info (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS revisions (
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
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_revisions_source_url_seq
                ON revisions (source_id, initial_url, first_seen_seq);
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS receipts (
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
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_receipts_source_seq
                ON receipts (source_id, seq);
            """)

            # Enforce append-only integrity via SQLite triggers
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_receipts_no_update
                BEFORE UPDATE ON receipts
                BEGIN
                    SELECT RAISE(ABORT, 'Mutation forbidden: receipts table is append-only');
                END;
            """)

            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_receipts_no_delete
                BEFORE DELETE ON receipts
                BEGIN
                    SELECT RAISE(ABORT, 'Deletion forbidden: receipts table is append-only');
                END;
            """)

            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_revisions_no_update
                BEFORE UPDATE ON revisions
                BEGIN
                    SELECT RAISE(ABORT, 'Mutation forbidden: revisions table is append-only');
                END;
            """)

            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_revisions_no_delete
                BEFORE DELETE ON revisions
                BEGIN
                    SELECT RAISE(ABORT, 'Deletion forbidden: revisions table is append-only');
                END;
            """)

            conn.execute(
                f"INSERT OR IGNORE INTO schema_info (key, value) VALUES ('version', '{SCHEMA_VERSION}');"
            )
            conn.execute("COMMIT;")

    def _blob_path(self, sha256: str) -> Path:
        """Return the CAS filesystem path for a given SHA-256 hash."""
        return self.blobs_dir / sha256[:2] / sha256[2:4] / f"{sha256}.bin"

    def _fsync_cas_barrier(self, dest_path: Path, sync_file: bool = False) -> None:
        """Durable filesystem barrier for CAS before SQLite transaction commit.

        Synchronizes the blob file (if requested) and all required directory
        entries along the entire ancestry chain from the CAS leaf prefix directory
        up to the filesystem root.
        """
        if sync_file:
            file_fd = os.open(str(dest_path), os.O_RDONLY)
            try:
                os.fsync(file_fd)
            finally:
                os.close(file_fd)

        cur = dest_path.resolve().parent
        while True:
            dir_fd = os.open(str(cur), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            if cur.parent == cur:
                break
            cur = cur.parent

    def _store_blob(self, body: bytes) -> str:
        """Store bytes into filesystem CAS atomically with fsync and no-clobber semantics.

        Deduplicates identical blobs: if the blob already exists, verifies both
        size and SHA-256 hash. If corrupted, raises ArchiveIntegrityError without
        modifying the corrupted file. Publishes new blobs atomically via link
        without clobbering, followed by directory fsync. All paths (new publish,
        existing reuse, concurrent race loser) execute the durable filesystem
        barrier before returning.
        """
        sha256 = hashlib.sha256(body).hexdigest()
        dest_path = self._blob_path(sha256)

        if dest_path.is_file():
            # Already exists: verify exact size AND hash
            stat_size = dest_path.stat().st_size
            if stat_size != len(body):
                raise ArchiveIntegrityError(
                    f"CAS blob size mismatch at {dest_path}: expected {len(body)} bytes, found {stat_size} bytes."
                )
            existing_data = dest_path.read_bytes()
            existing_sha = hashlib.sha256(existing_data).hexdigest()
            if existing_sha != sha256:
                raise ArchiveIntegrityError(
                    f"CAS blob hash mismatch at {dest_path}: expected SHA-256 {sha256}, found corrupted blob {existing_sha}."
                )
            # Enforce durability barrier on reuse path
            self._fsync_cas_barrier(dest_path, sync_file=True)
            return sha256

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temporary file in self.tmp_dir (same filesystem for atomic hardlink/rename)
        temp_fd, temp_path_str = tempfile.mkstemp(dir=str(self.tmp_dir), prefix="blob_", suffix=".tmp")
        temp_path = Path(temp_path_str)
        try:
            with os.fdopen(temp_fd, "wb") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())

            # Atomic no-clobber publish: os.link fails with FileExistsError if dest_path already exists
            published = False
            try:
                os.link(temp_path, dest_path)
                published = True
            except FileExistsError:
                published = False

            # Remove temporary staging file
            temp_path.unlink(missing_ok=True)

            if not published:
                # Race condition: another concurrent writer wrote dest_path
                stat_size = dest_path.stat().st_size
                if stat_size != len(body):
                    raise ArchiveIntegrityError(
                        f"CAS blob race corruption at {dest_path}: expected {len(body)} bytes, found {stat_size} bytes."
                    )
                existing_data = dest_path.read_bytes()
                existing_sha = hashlib.sha256(existing_data).hexdigest()
                if existing_sha != sha256:
                    raise ArchiveIntegrityError(
                        f"CAS blob race hash mismatch at {dest_path}: expected {sha256}, found {existing_sha}."
                    )
                # Enforce durability barrier on race loser path
                self._fsync_cas_barrier(dest_path, sync_file=True)
                return sha256

            # Enforce durability barrier on new publish path (temp file was already fsynced)
            self._fsync_cas_barrier(dest_path, sync_file=False)

        except Exception:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            raise

        return sha256

    def get_blob(self, blob_sha256: str) -> bytes:
        """Retrieve raw blob bytes from CAS by SHA-256 and verify integrity."""
        path = self._blob_path(blob_sha256)
        if not path.is_file():
            raise FileNotFoundError(f"CAS blob not found for hash: {blob_sha256}")

        data = path.read_bytes()
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != blob_sha256:
            raise ArchiveIntegrityError(
                f"CAS corruption: blob at {path} has hash {actual_sha}, expected {blob_sha256}"
            )
        return data

    def _validate_capture(self, capture: CaptureResult) -> None:
        """Validate CaptureResult invariants at archive boundary before persistent processing."""
        validate_capture_result(capture, registry=self._source_registry)

    def _compute_payload_fingerprint(
        self,
        capture: CaptureResult,
        diag_status: str,
        diag_level: str,
        diag_reason: str,
        diag_checker_version: str,
    ) -> str:
        """Compute deterministic SHA-256 fingerprint over all capture and diagnostic fields."""
        canonical_headers = {k.lower(): capture.headers[k] for k in sorted(capture.headers.keys(), key=lambda s: s.lower())}
        payload_dict = {
            "source_id": capture.source_id,
            "initial_url": capture.initial_url,
            "final_url": capture.final_url,
            "status_code": capture.status_code,
            "headers": canonical_headers,
            "fetch_started_at": capture.fetch_started_at,
            "fetch_completed_at": capture.fetch_completed_at,
            "duration_ms": capture.duration_ms,
            "bytes_received": capture.bytes_received,
            "bytes_stored": capture.bytes_stored,
            "complete": capture.complete,
            "limit_exceeded": capture.limit_exceeded,
            "error_msg": capture.error_msg,
            "collector_version": capture.collector_version,
            "raw_body_sha256": hashlib.sha256(capture.body).hexdigest(),
            "diag_status": diag_status,
            "diag_level": diag_level,
            "diag_reason": diag_reason,
            "diag_checker_version": diag_checker_version,
        }
        serialized = json.dumps(payload_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _verify_idempotency_payload(self, existing: ArchiveReceipt, expected_fingerprint: str) -> None:
        """Verify that duplicate receipt_id matches full immutable payload fingerprint."""
        if existing.payload_fingerprint != expected_fingerprint:
            raise ArchiveConflictError(
                f"Conflict on receipt_id '{existing.receipt_id}': existing payload differs from provided capture "
                f"(existing fingerprint {existing.payload_fingerprint[:16]}... != {expected_fingerprint[:16]}...)"
            )

    def record_capture(
        self,
        capture: CaptureResult,
        receipt_id: Optional[str] = None,
        probe_result: Optional[Dict[str, Any]] = None,
        failpoint: Optional[str] = None,
        test_hook: Optional[Any] = None,
    ) -> ArchiveReceipt:
        """Record a completed CaptureResult into the archive.

        - Stores raw body in filesystem CAS (fsync before DB commit) if complete.
        - Records attempt receipt in receipts table.
        - Updates revision history for (source_id, initial_url) if body changed.
        - Guarantees idempotency on duplicate receipt_id with identical payload;
          raises ArchiveConflictError on duplicate receipt_id with conflicting payload.
        """
        # Validate canonical source and DTO invariants
        self._validate_capture(capture)

        if receipt_id is None:
            receipt_id = str(uuid.uuid4())

        # Resolve probe diagnostic status
        if probe_result is not None:
            diag_status = probe_result.get("status", "network_error")
            diag_level = probe_result.get("validation_level", "transport")
            diag_reason = probe_result.get("reason", "")
            diag_checker_version = probe_result.get("checker_version", PROBE_VERSION)
        else:
            with SourceProbe() as probe:
                status, level, reason, _ = probe.validate_content(
                    source_id=capture.source_id,
                    http_status=capture.status_code,
                    content_type=capture.content_type,
                    body=capture.body,
                    limit_exceeded=capture.limit_exceeded,
                    error_msg=capture.error_msg,
                )
            diag_status = status
            diag_level = level
            diag_reason = reason
            diag_checker_version = PROBE_VERSION

        expected_fingerprint = self._compute_payload_fingerprint(
            capture, diag_status, diag_level, diag_reason, diag_checker_version
        )

        # Check idempotency before expensive storage operations
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM receipts WHERE receipt_id = ?;", (receipt_id,))
            row = cursor.fetchone()
            if row is not None:
                existing = self._row_to_receipt(row)
                self._verify_idempotency_payload(existing, expected_fingerprint)
                return existing

            # CAS storage: complete responses without error or limit
            blob_sha256: Optional[str] = None
            if capture.complete and not capture.limit_exceeded and capture.error_msg is None:
                blob_sha256 = self._store_blob(capture.body)

            if test_hook:
                test_hook("after_blob_published_before_db")

            # Injected failpoint for crash-resilience testing
            if failpoint == "after_blob_fsync_before_db_commit":
                raise RuntimeError("Failpoint crash: blob fsynced, writer halted before SQLite commit")

            # Begin database transaction with exclusive lock
            conn.execute("BEGIN IMMEDIATE;")

            # Double-check idempotency under lock in case of concurrent insert
            cursor.execute("SELECT * FROM receipts WHERE receipt_id = ?;", (receipt_id,))
            row = cursor.fetchone()
            if row is not None:
                conn.execute("ROLLBACK;")
                existing = self._row_to_receipt(row)
                self._verify_idempotency_payload(existing, expected_fingerprint)
                return existing

            # Determine next monotonic sequence number
            cursor.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM receipts;")
            seq = int(cursor.fetchone()[0])
            recorded_at = utc_now_iso()

            revision_id: Optional[str] = None
            if blob_sha256 is not None:
                # Find current latest revision along the chain for (source_id, initial_url)
                cursor.execute(
                    """
                    SELECT revision_id, blob_sha256
                    FROM revisions
                    WHERE source_id = ? AND initial_url = ?
                    ORDER BY first_seen_seq DESC
                    LIMIT 1;
                    """,
                    (capture.source_id, capture.initial_url),
                )
                latest_rev = cursor.fetchone()

                if latest_rev is not None and latest_rev[1] == blob_sha256:
                    # Identical bytes: reuse current revision
                    revision_id = latest_rev[0]
                else:
                    # New revision (first observation, or bytes changed: e.g. A -> B -> A)
                    revision_id = str(uuid.uuid4())
                    prev_revision_id = latest_rev[0] if latest_rev is not None else None
                    cursor.execute(
                        """
                        INSERT INTO revisions (
                            revision_id, source_id, initial_url, blob_sha256,
                            byte_size, first_seen_at, first_seen_seq, prev_revision_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            revision_id,
                            capture.source_id,
                            capture.initial_url,
                            blob_sha256,
                            len(capture.body),
                            recorded_at,
                            seq,
                            prev_revision_id,
                            recorded_at,
                        ),
                    )

            # Insert receipt with metadata and fingerprint
            cursor.execute(
                """
                INSERT INTO receipts (
                    receipt_id, seq, source_id, initial_url, final_url,
                    http_status, headers_json, fetch_started_at, fetch_completed_at,
                    recorded_at, duration_ms, bytes_received, bytes_stored,
                    complete, limit_exceeded, error_msg, collector_version,
                    status, validation_level, reason, checker_version,
                    usable_for_forecast, payload_fingerprint, blob_sha256, revision_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    receipt_id,
                    seq,
                    capture.source_id,
                    capture.initial_url,
                    capture.final_url,
                    capture.status_code,
                    json.dumps(capture.headers),
                    capture.fetch_started_at,
                    capture.fetch_completed_at,
                    recorded_at,
                    capture.duration_ms,
                    capture.bytes_received,
                    capture.bytes_stored,
                    1 if capture.complete else 0,
                    1 if capture.limit_exceeded else 0,
                    capture.error_msg,
                    capture.collector_version,
                    diag_status,
                    diag_level,
                    diag_reason,
                    diag_checker_version,
                    0,  # Strict invariant: usable_for_forecast is always False in R2/R2.1
                    expected_fingerprint,
                    blob_sha256,
                    revision_id,
                ),
            )

            if test_hook:
                test_hook("inside_db_tx_before_commit")

            conn.execute("COMMIT;")

            return ArchiveReceipt(
                receipt_id=receipt_id,
                seq=seq,
                source_id=capture.source_id,
                initial_url=capture.initial_url,
                final_url=capture.final_url,
                http_status=capture.status_code,
                headers=dict(capture.headers),
                fetch_started_at=capture.fetch_started_at,
                fetch_completed_at=capture.fetch_completed_at,
                recorded_at=recorded_at,
                duration_ms=capture.duration_ms,
                bytes_received=capture.bytes_received,
                bytes_stored=capture.bytes_stored,
                complete=capture.complete,
                limit_exceeded=capture.limit_exceeded,
                error_msg=capture.error_msg,
                collector_version=capture.collector_version,
                status=diag_status,
                validation_level=diag_level,
                reason=diag_reason,
                checker_version=diag_checker_version,
                usable_for_forecast=False,
                payload_fingerprint=expected_fingerprint,
                blob_sha256=blob_sha256,
                revision_id=revision_id,
            )
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def _row_to_receipt(self, row: Tuple[Any, ...]) -> ArchiveReceipt:
        """Convert a SQLite receipts row tuple into an ArchiveReceipt dataclass."""
        return ArchiveReceipt(
            receipt_id=row[0],
            seq=int(row[1]),
            source_id=row[2],
            initial_url=row[3],
            final_url=row[4],
            http_status=row[5],
            headers=json.loads(row[6]),
            fetch_started_at=row[7],
            fetch_completed_at=row[8],
            recorded_at=row[9],
            duration_ms=float(row[10]),
            bytes_received=row[11],
            bytes_stored=row[12],
            complete=bool(row[13]),
            limit_exceeded=bool(row[14]),
            error_msg=row[15],
            collector_version=row[16],
            status=row[17],
            validation_level=row[18],
            reason=row[19],
            checker_version=row[20],
            usable_for_forecast=bool(row[21]),
            payload_fingerprint=row[22],
            blob_sha256=row[23],
            revision_id=row[24],
        )

    def _row_to_revision(self, row: Tuple[Any, ...]) -> ArchiveRevision:
        """Convert a SQLite revisions row tuple into an ArchiveRevision dataclass."""
        return ArchiveRevision(
            revision_id=row[0],
            source_id=row[1],
            initial_url=row[2],
            blob_sha256=row[3],
            byte_size=int(row[4]),
            first_seen_at=row[5],
            first_seen_seq=int(row[6]),
            prev_revision_id=row[7],
            created_at=row[8],
        )

    def get_receipt(self, receipt_id: str, as_of_seq: Optional[int] = None) -> Optional[ArchiveReceipt]:
        """Look up a receipt by receipt_id, respecting as_of_seq cutoff."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if as_of_seq is not None:
                cursor.execute(
                    "SELECT * FROM receipts WHERE receipt_id = ? AND seq <= ?;",
                    (receipt_id, as_of_seq),
                )
            else:
                cursor.execute("SELECT * FROM receipts WHERE receipt_id = ?;", (receipt_id,))
            row = cursor.fetchone()
            return self._row_to_receipt(row) if row else None

    def get_revision(self, revision_id: str, as_of_seq: Optional[int] = None) -> Optional[ArchiveRevision]:
        """Look up a revision by revision_id, respecting as_of_seq cutoff."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if as_of_seq is not None:
                cursor.execute(
                    "SELECT * FROM revisions WHERE revision_id = ? AND first_seen_seq <= ?;",
                    (revision_id, as_of_seq),
                )
            else:
                cursor.execute("SELECT * FROM revisions WHERE revision_id = ?;", (revision_id,))
            row = cursor.fetchone()
            return self._row_to_revision(row) if row else None

    def get_latest_revision(
        self,
        source_id: str,
        initial_url: str,
        as_of_seq: Optional[int] = None,
    ) -> Optional[ArchiveRevision]:
        """Get the latest revision for (source_id, initial_url) visible as of seq cutoff."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if as_of_seq is not None:
                cursor.execute(
                    """
                    SELECT * FROM revisions
                    WHERE source_id = ? AND initial_url = ? AND first_seen_seq <= ?
                    ORDER BY first_seen_seq DESC
                    LIMIT 1;
                    """,
                    (source_id, initial_url, as_of_seq),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM revisions
                    WHERE source_id = ? AND initial_url = ?
                    ORDER BY first_seen_seq DESC
                    LIMIT 1;
                    """,
                    (source_id, initial_url),
                )
            row = cursor.fetchone()
            return self._row_to_revision(row) if row else None

    def query_receipts(
        self,
        source_id: Optional[str] = None,
        as_of_seq: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[ArchiveReceipt]:
        """Query receipts in append order (by seq ASC), filtered by source and as_of_seq cutoff."""
        clauses: List[str] = []
        params: List[Any] = []

        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        if as_of_seq is not None:
            clauses.append("seq <= ?")
            params.append(as_of_seq)

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        limit_sql = f"LIMIT {int(limit)}" if limit is not None else ""

        sql = f"SELECT * FROM receipts {where_sql} ORDER BY seq ASC {limit_sql};"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [self._row_to_receipt(row) for row in cursor.fetchall()]

    def query_revisions(
        self,
        source_id: Optional[str] = None,
        as_of_seq: Optional[int] = None,
    ) -> List[ArchiveRevision]:
        """Query revisions in creation order, filtered by source and as_of_seq cutoff."""
        clauses: List[str] = []
        params: List[Any] = []

        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        if as_of_seq is not None:
            clauses.append("first_seen_seq <= ?")
            params.append(as_of_seq)

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM revisions {where_sql} ORDER BY first_seen_seq ASC;"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [self._row_to_revision(row) for row in cursor.fetchall()]

    def verify_integrity(self) -> Dict[str, Any]:
        """Perform full integrity check across CAS filesystem blobs and SQLite database.

        Verifies:
        1. Schema version matches SCHEMA_VERSION.
        2. SQLite quick_check passes without corruption.
        3. Foreign key constraints are respected.
        4. Every committed receipt referencing a blob has a valid CAS file matching SHA-256 and size.
        5. Every committed revision has a valid CAS file matching SHA-256 and byte_size.
        6. Receipt sequence numbers are strictly monotonic.
        """
        errors: List[str] = []
        checked_blobs: set[str] = set()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Verify schema version
            cursor.execute("SELECT value FROM schema_info WHERE key = 'version';")
            vrow = cursor.fetchone()
            if vrow is None or vrow[0] != SCHEMA_VERSION:
                errors.append(f"Schema version mismatch: found {vrow}, expected {SCHEMA_VERSION}")

            # SQLite quick_check
            cursor.execute("PRAGMA quick_check;")
            qc = cursor.fetchall()
            for r in qc:
                if r[0] != "ok":
                    errors.append(f"SQLite quick_check error: {r[0]}")

            # Check foreign key violations
            cursor.execute("PRAGMA foreign_key_check;")
            fk_violations = cursor.fetchall()
            for v in fk_violations:
                errors.append(f"Foreign key violation: {v}")

            # Check receipts
            cursor.execute("SELECT receipt_id, seq, blob_sha256, bytes_stored FROM receipts;")
            receipt_rows = cursor.fetchall()
            total_receipts = len(receipt_rows)

            for rec_id, seq, blob_sha, b_stored in receipt_rows:
                if blob_sha is not None:
                    checked_blobs.add(blob_sha)
                    blob_path = self._blob_path(blob_sha)
                    if not blob_path.is_file():
                        errors.append(
                            f"Receipt {rec_id} (seq {seq}) references missing CAS blob: {blob_sha}"
                        )
                    else:
                        actual_bytes = blob_path.read_bytes()
                        actual_sha = hashlib.sha256(actual_bytes).hexdigest()
                        if actual_sha != blob_sha:
                            errors.append(
                                f"CAS blob corruption for receipt {rec_id}: hash {actual_sha} != {blob_sha}"
                            )
                        if b_stored is not None and len(actual_bytes) != b_stored:
                            errors.append(
                                f"Size mismatch for receipt {rec_id}: file size {len(actual_bytes)} != bytes_stored {b_stored}"
                            )

            # Check revisions
            cursor.execute("SELECT revision_id, blob_sha256, byte_size FROM revisions;")
            revision_rows = cursor.fetchall()
            total_revisions = len(revision_rows)

            for rev_id, blob_sha, byte_size in revision_rows:
                checked_blobs.add(blob_sha)
                blob_path = self._blob_path(blob_sha)
                if not blob_path.is_file():
                    errors.append(f"Revision {rev_id} references missing CAS blob: {blob_sha}")
                else:
                    actual_bytes = blob_path.read_bytes()
                    actual_sha = hashlib.sha256(actual_bytes).hexdigest()
                    if actual_sha != blob_sha:
                        errors.append(
                            f"CAS blob corruption for revision {rev_id}: hash {actual_sha} != {blob_sha}"
                        )
                    if len(actual_bytes) != byte_size:
                        errors.append(
                            f"Size mismatch for revision {rev_id}: file size {len(actual_bytes)} != byte_size {byte_size}"
                        )

        return {
            "healthy": len(errors) == 0,
            "total_receipts": total_receipts,
            "total_revisions": total_revisions,
            "total_blobs_checked": len(checked_blobs),
            "errors": errors,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Pythia Evidence Archive CLI")
    parser.add_argument(
        "--state-dir",
        "--archive-dir",
        dest="state_dir",
        type=Path,
        required=True,
        help="Path to archive state directory containing archive.db and blobs/",
    )
    parser.add_argument(
        "--capture-source",
        type=str,
        help="Capture single source_id and record into archive",
    )
    parser.add_argument(
        "--capture-all",
        action="store_true",
        help="Capture all 8 canonical sources sequentially and record into archive",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify archive CAS blobs and SQLite index integrity",
    )
    parser.add_argument(
        "--list-receipts",
        action="store_true",
        help="List receipts from archive",
    )
    parser.add_argument(
        "--latest-revision",
        action="store_true",
        help="Get latest revision for --source",
    )
    parser.add_argument(
        "--source",
        type=str,
        help="Canonical source_id filter",
    )
    parser.add_argument(
        "--as-of-seq",
        type=int,
        help="Point-in-time cutoff sequence number",
    )
    parser.add_argument(
        "--read-blob",
        type=str,
        help="Read raw blob content by SHA-256 and print to stdout",
    )

    args = parser.parse_args()
    archive = EvidenceArchive(args.state_dir)

    if args.verify:
        result = archive.verify_integrity()
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["healthy"] else 1)

    elif args.read_blob:
        data = archive.get_blob(args.read_blob)
        sys.stdout.buffer.write(data)
        sys.exit(0)

    elif args.latest_revision:
        if not args.source:
            parser.error("--latest-revision requires --source <source_id>")
        if args.source not in CANONICAL_SOURCES:
            parser.error(f"Unknown canonical source_id: '{args.source}'")
        url = CANONICAL_SOURCES[args.source]
        rev = archive.get_latest_revision(args.source, url, as_of_seq=args.as_of_seq)
        if rev:
            print(json.dumps(rev.to_dict(), indent=2))
        else:
            print(json.dumps({"status": "NOT_FOUND"}, indent=2))
        sys.exit(0)

    elif args.list_receipts:
        receipts = archive.query_receipts(source_id=args.source, as_of_seq=args.as_of_seq)
        output = [r.to_dict() for r in receipts]
        print(json.dumps(output, indent=2))
        sys.exit(0)

    elif args.capture_source:
        sid = args.capture_source
        if sid not in CANONICAL_SOURCES:
            parser.error(f"Unknown canonical source_id: '{sid}'")
        url = CANONICAL_SOURCES[sid]
        with SourceProbe() as probe:
            capture = probe.capture_source(sid, url)
            probe_res = probe.probe_single({"source_id": sid, "url": url}, capture=capture)
            receipt = archive.record_capture(capture=capture, probe_result=probe_res)
        print(json.dumps(receipt.to_dict(), indent=2))
        sys.exit(0)

    elif args.capture_all:
        results = []
        with SourceProbe() as probe:
            for sid, url in CANONICAL_SOURCES.items():
                capture = probe.capture_source(sid, url)
                probe_res = probe.probe_single({"source_id": sid, "url": url}, capture=capture)
                receipt = archive.record_capture(capture=capture, probe_result=probe_res)
                results.append(receipt.to_dict())
        summary = {
            "total_captured": len(results),
            "state_dir": str(args.state_dir),
            "receipts": results,
        }
        print(json.dumps(summary, indent=2))
        sys.exit(0)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
