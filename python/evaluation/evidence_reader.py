"""Pythia R3.0: Read-Only Archive Reader.

Strict, point-in-time, read-only reader for existing Pythia Evidence Archives.
Enforces:
1. Pure SQLite mode=ro connection (no DDL, no mkdir, no mutable PRAGMAs, no immutable=1).
2. Schema v2 validation and SQLite quick_check.
3. Strict as_of_seq cutoff enforcement.
4. Independent CAS blob presence, size, and SHA-256 verification.
5. Consistent linkage across receipts, revisions, and CAS blobs.
6. Honest negative capture handling without failing on missing blobs.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ReaderError(Exception):
    """Base exception for evidence reader operations."""


class ArchiveNotFoundError(ReaderError):
    """Raised when the archive directory, database, or blobs directory is missing."""


class CutoffViolationError(ReaderError):
    """Raised when a receipt or revision violates the as_of_seq cutoff."""


class ReaderIntegrityError(ReaderError):
    """Raised when archive metadata, linkage, or CAS blobs fail consistency checks."""


@dataclass(frozen=True)
class ReadOnlyReceipt:
    """Immutable representation of a receipt read from the archive database."""

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
class ReadOnlyRevision:
    """Immutable representation of a revision read from the archive database."""

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


@dataclass(frozen=True)
class VerifiedCapture:
    """A receipt verified against its revision linkage and CAS blob storage."""

    receipt: ReadOnlyReceipt
    revision: Optional[ReadOnlyRevision]
    blob_bytes: Optional[bytes]

    @property
    def is_negative(self) -> bool:
        return self.receipt.blob_sha256 is None and self.receipt.revision_id is None


class ReadOnlyArchiveReader:
    """Strict read-only accessor for existing Pythia Evidence Archives."""

    SCHEMA_VERSION = "pythia-archive-r2/2.0"

    def __init__(self, state_dir: Path | str) -> None:
        self.state_dir = Path(state_dir)

        # Strict non-destructive check: MUST NOT create files or directories
        if not self.state_dir.is_dir():
            raise ArchiveNotFoundError(f"Archive directory not found: {self.state_dir}")

        self.db_path = self.state_dir / "archive.db"
        if not self.db_path.is_file():
            raise ArchiveNotFoundError(f"Archive database not found: {self.db_path}")

        self.blobs_dir = self.state_dir / "blobs"
        if not self.blobs_dir.is_dir():
            raise ArchiveNotFoundError(f"Archive blobs directory not found: {self.blobs_dir}")

        # Verify schema and database integrity on existing DB
        self._verify_archive_header()

    def _get_ro_connection(self) -> sqlite3.Connection:
        """Create a strictly read-only SQLite connection.
        
        Uses mode=ro URI without immutable=1 to correctly observe WAL checkpoints.
        """
        ro_uri = f"file:{self.db_path.resolve()}?mode=ro"
        conn = sqlite3.connect(ro_uri, uri=True, timeout=10.0)
        conn.execute("PRAGMA busy_timeout = 10000;")
        return conn

    def _verify_archive_header(self) -> None:
        """Verify schema version and basic database health."""
        with self._get_ro_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_info';")
            if cursor.fetchone() is None:
                raise ReaderIntegrityError("schema_info table missing from archive database")

            cursor.execute("SELECT value FROM schema_info WHERE key = 'version';")
            row = cursor.fetchone()
            if row is None or row[0] != self.SCHEMA_VERSION:
                version_found = row[0] if row else "None"
                raise ReaderIntegrityError(
                    f"Incompatible archive schema version '{version_found}'. "
                    f"Expected '{self.SCHEMA_VERSION}'."
                )

            cursor.execute("PRAGMA quick_check;")
            qc = cursor.fetchall()
            for r in qc:
                if r[0] != "ok":
                    raise ReaderIntegrityError(f"SQLite quick_check failed: {r[0]}")

    def _row_to_receipt(self, row: Tuple[Any, ...]) -> ReadOnlyReceipt:
        return ReadOnlyReceipt(
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

    def _row_to_revision(self, row: Tuple[Any, ...]) -> ReadOnlyRevision:
        return ReadOnlyRevision(
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

    def _blob_path(self, sha256: str) -> Path:
        """Resolve the CAS file path for a given SHA-256."""
        return self.blobs_dir / sha256[:2] / sha256[2:4] / f"{sha256}.bin"

    def read_receipts(self, receipt_ids: List[str], as_of_seq: int) -> List[VerifiedCapture]:
        """Read and verify explicit receipts subject to an as_of_seq cutoff.
        
        Args:
            receipt_ids: Non-empty list of receipt IDs to read.
            as_of_seq: Monotonic sequence cutoff (integer >= 0).
            
        Returns:
            List of VerifiedCapture objects, deterministically ordered by (seq ASC, receipt_id ASC).
            
        Raises:
            ValueError: If inputs are invalid.
            ArchiveNotFoundError: If any requested receipt_id does not exist.
            CutoffViolationError: If any receipt or revision exceeds as_of_seq.
            ReaderIntegrityError: If metadata linkage or CAS blobs are missing or corrupt.
        """
        # Strict input validation
        if type(as_of_seq) is not int or isinstance(as_of_seq, bool) or as_of_seq < 0:
            raise ValueError(f"as_of_seq must be a non-negative integer, got {as_of_seq!r}")

        if not isinstance(receipt_ids, (list, tuple)) or len(receipt_ids) == 0:
            raise ValueError("receipt_ids must be a non-empty list of receipt IDs")

        for rid in receipt_ids:
            if not isinstance(rid, str) or not rid.strip():
                raise ValueError(f"Invalid receipt ID: {rid!r}")

        # Deduplicate receipt IDs deterministically
        unique_rids = sorted(set(receipt_ids))

        with self._get_ro_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN;")
            try:
                # Query all requested receipts in single read transaction
                placeholders = ",".join("?" for _ in unique_rids)
                cursor.execute(
                    f"SELECT * FROM receipts WHERE receipt_id IN ({placeholders});",
                    unique_rids,
                )
                rows = cursor.fetchall()
                receipt_map: Dict[str, ReadOnlyReceipt] = {}
                for row in rows:
                    rec = self._row_to_receipt(row)
                    receipt_map[rec.receipt_id] = rec

                # Verify all requested receipts exist
                for rid in unique_rids:
                    if rid not in receipt_map:
                        raise ArchiveNotFoundError(f"Requested receipt_id '{rid}' not found in archive")

                # Verify cutoff and linkage
                verified_captures: List[VerifiedCapture] = []
                for rid in sorted(unique_rids, key=lambda r: (receipt_map[r].seq, receipt_map[r].receipt_id)):
                    rec = receipt_map[rid]
                    if rec.seq > as_of_seq:
                        raise CutoffViolationError(
                            f"Receipt '{rid}' (seq {rec.seq}) exceeds cutoff as_of_seq={as_of_seq}"
                        )

                    # Negative receipt check
                    if rec.blob_sha256 is None and rec.revision_id is None:
                        # Legitimate negative receipt: no CAS blob, no revision
                        verified_captures.append(
                            VerifiedCapture(receipt=rec, revision=None, blob_bytes=None)
                        )
                        continue

                    # Contradictory linkage check
                    if rec.blob_sha256 is None or rec.revision_id is None:
                        raise ReaderIntegrityError(
                            f"Inconsistent linkage in receipt '{rid}': blob_sha256={rec.blob_sha256!r}, "
                            f"revision_id={rec.revision_id!r}"
                        )

                    # Positive capture: fetch revision
                    cursor.execute(
                        "SELECT * FROM revisions WHERE revision_id = ?;",
                        (rec.revision_id,),
                    )
                    rev_row = cursor.fetchone()
                    if rev_row is None:
                        raise ReaderIntegrityError(
                            f"Receipt '{rid}' references missing revision '{rec.revision_id}'"
                        )
                    rev = self._row_to_revision(rev_row)

                    # Cross-verify revision consistency
                    if rev.source_id != rec.source_id:
                        raise ReaderIntegrityError(
                            f"Linkage mismatch in receipt '{rid}': source_id {rec.source_id} != {rev.source_id}"
                        )
                    if rev.initial_url != rec.initial_url:
                        raise ReaderIntegrityError(
                            f"Linkage mismatch in receipt '{rid}': initial_url {rec.initial_url} != {rev.initial_url}"
                        )
                    if rev.blob_sha256 != rec.blob_sha256:
                        raise ReaderIntegrityError(
                            f"Linkage mismatch in receipt '{rid}': blob_sha256 {rec.blob_sha256} != {rev.blob_sha256}"
                        )
                    if rev.first_seen_seq > as_of_seq:
                        raise CutoffViolationError(
                            f"Revision '{rev.revision_id}' for receipt '{rid}' first seen at seq "
                            f"{rev.first_seen_seq} > as_of_seq {as_of_seq}"
                        )

                    # Open and verify CAS blob
                    blob_path = self._blob_path(rec.blob_sha256)
                    if not blob_path.is_file():
                        raise ReaderIntegrityError(
                            f"Receipt '{rid}' references missing CAS blob: {blob_path}"
                        )

                    blob_bytes = blob_path.read_bytes()
                    actual_sha = hashlib.sha256(blob_bytes).hexdigest()
                    if actual_sha != rec.blob_sha256:
                        raise ReaderIntegrityError(
                            f"CAS blob hash corruption for receipt '{rid}': actual {actual_sha} != {rec.blob_sha256}"
                        )
                    if len(blob_bytes) != rev.byte_size:
                        raise ReaderIntegrityError(
                            f"CAS blob size mismatch for revision '{rev.revision_id}': "
                            f"actual {len(blob_bytes)} != byte_size {rev.byte_size}"
                        )
                    if rec.bytes_stored is not None and len(blob_bytes) != rec.bytes_stored:
                        raise ReaderIntegrityError(
                            f"CAS blob size mismatch for receipt '{rid}': "
                            f"actual {len(blob_bytes)} != bytes_stored {rec.bytes_stored}"
                        )

                    verified_captures.append(
                        VerifiedCapture(receipt=rec, revision=rev, blob_bytes=blob_bytes)
                    )

                return verified_captures
            finally:
                conn.execute("ROLLBACK;")
