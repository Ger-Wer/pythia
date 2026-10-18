"""Pythia R2 Tests: Immutable Evidence Archive (Filesystem CAS + SQLite).

Verifies all R2 criteria:
1. Exact bytes/hash round-trip for UTF-8 with Chinese text and binary; loopback gzip fixture.
2. Identical body fetches -> 2 receipts, 1 revision, 1 blob; A -> B -> A -> 3 revisions, 2 blobs.
3. Idempotency & conflict rejection for duplicate receipt_id. Concurrent write safety.
4. Failpoint crash resilience (fsync before DB commit) and rollback on error.
5. Append-only triggers enforce immutability (UPDATE and DELETE forbidden).
6. Blocked (403)/unverified HTML stored as diagnostic snapshots; partial/timeout get no revision.
7. as_of_seq point-in-time cutoff isolation.
8. Single HTTP GET per capture; offline queries never touch network.
9. Strict worker schema validation and counter consistency.
"""

from __future__ import annotations

import base64
import concurrent.futures
import gzip
import hashlib
import http.server
import json
import os
import socketserver
import sqlite3
import sys
import signal
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from python.evaluation.evidence_archive import (
    ArchiveConflictError,
    ArchiveIntegrityError,
    ArchiveReceipt,
    ArchiveRevision,
    EvidenceArchive,
)
from python.evaluation.source_probe import (
    CANONICAL_SOURCES,
    COLLECTOR_VERSION,
    PROBE_VERSION,
    CaptureResult,
    SourceProbe,
    utc_now_iso,
)


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """Fixture providing an isolated archive state directory."""
    sdir = tmp_path / "evidence_archive_state"
    sdir.mkdir(parents=True, exist_ok=True)
    return sdir


@pytest.fixture
def archive(state_dir: Path) -> EvidenceArchive:
    """Fixture providing an initialized EvidenceArchive."""
    return EvidenceArchive(state_dir)


def make_capture(
    source_id: str = "nyfed_effr",
    body: bytes = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-16","percentRate":3.88}]}',
    status_code: Optional[int] = 200,
    headers: Optional[Dict[str, str]] = None,
    complete: bool = True,
    limit_exceeded: bool = False,
    error_msg: Optional[str] = None,
    duration_ms: float = 12.5,
    final_url: Optional[str] = None,
) -> CaptureResult:
    """Helper to construct a CaptureResult with canonical defaults."""
    initial_url = CANONICAL_SOURCES[source_id]
    if final_url is None and status_code is not None:
        final_url = initial_url
    if headers is None:
        headers = {"content-type": "application/json"}
    now = utc_now_iso()
    return CaptureResult(
        source_id=source_id,
        initial_url=initial_url,
        final_url=final_url,
        body=body,
        status_code=status_code,
        headers=headers,
        fetch_started_at=now,
        fetch_completed_at=now,
        duration_ms=duration_ms,
        bytes_received=len(body) if status_code is not None else 0,
        bytes_stored=len(body) if status_code is not None else 0,
        complete=complete,
        limit_exceeded=limit_exceeded,
        error_msg=error_msg,
        collector_version=COLLECTOR_VERSION,
    )


# -------------------------------------------------------------------------
# 1. Exact bytes/hash round-trip and Loopback Gzip Content-Decoded Contract
# -------------------------------------------------------------------------

def test_exact_bytes_round_trip_utf8_chinese_and_binary(archive: EvidenceArchive):
    """Raw byte bodies (UTF-8 Chinese text and binary) round-trip exactly with no transcoding."""
    chinese_text = "深度求索 DeepSeek 2026年最新模型发布，参数量 671B 🚀 🔥 \u4e2d\u6587\u6d4b\u8bd5"
    chinese_bytes = chinese_text.encode("utf-8")
    binary_bytes = bytes([0x00, 0xFF, 0xFE, 0x01, 0x80, 0x7F, 0x1B, 0x00, 0x42])

    cap_zh = make_capture(source_id="hf_deepseek_models", body=chinese_bytes)
    rec_zh = archive.record_capture(cap_zh)
    assert rec_zh.blob_sha256 is not None
    retrieved_zh = archive.get_blob(rec_zh.blob_sha256)
    assert retrieved_zh == chinese_bytes
    assert hashlib.sha256(retrieved_zh).hexdigest() == hashlib.sha256(chinese_bytes).hexdigest()

    cap_bin = make_capture(source_id="github_deepseek_v3_releases", body=binary_bytes)
    rec_bin = archive.record_capture(cap_bin)
    assert rec_bin.blob_sha256 is not None
    retrieved_bin = archive.get_blob(rec_bin.blob_sha256)
    assert retrieved_bin == binary_bytes
    assert hashlib.sha256(retrieved_bin).hexdigest() == hashlib.sha256(binary_bytes).hexdigest()


def test_loopback_gzip_content_decoded_raw_contract(archive: EvidenceArchive):
    """Loopback server serving gzip-compressed HTTP response confirms that requests

    content-decodes the payload and the archive stores the decoded raw body.
    """
    original_text = json.dumps({"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-16", "percentRate": 3.88}]})
    original_bytes = original_text.encode("utf-8")
    compressed_bytes = gzip.compress(original_bytes)
    assert compressed_bytes != original_bytes

    class GzipHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(compressed_bytes)))
            self.end_headers()
            self.wfile.write(compressed_bytes)

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), GzipHandler)
    server_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        url = f"http://127.0.0.1:{server_port}/rates.json"

        # Worker script that fetches from loopback server using requests
        worker_code = f"""import sys, json, base64, requests

raw = sys.stdin.read()
params = json.loads(raw)
sess = requests.Session()
sess.trust_env = False
resp = sess.get("{url}")

# iter_content returns content-decoded bytes (decompressed from gzip)
chunks = list(resp.iter_content(chunk_size=8192))
body = b"".join(chunks)

out = {{
    "status_code": resp.status_code,
    "headers": {{str(k).lower(): str(v) for k, v in resp.headers.items()}},
    "content_type": resp.headers.get("Content-Type"),
    "body_b64": base64.b64encode(body).decode("ascii"),
    "bytes_received": len(body),
    "bytes_stored": len(body),
    "complete": True,
    "limit_exceeded": False,
    "final_url": "{CANONICAL_SOURCES['nyfed_effr']}",
    "error_msg": None,
}}
print(json.dumps(out))
"""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(worker_code)
            worker_script = f.name

        try:
            probe = SourceProbe(worker_cmd=[sys.executable, worker_script])
            capture = probe.capture_source("nyfed_effr", CANONICAL_SOURCES["nyfed_effr"])

            # Verify that capture body is the decompressed raw body
            assert capture.body == original_bytes
            assert capture.bytes_stored == len(original_bytes)
            assert capture.headers.get("content-encoding") == "gzip"
            assert capture.complete is True

            # Record into archive
            receipt = archive.record_capture(capture)
            assert receipt.blob_sha256 == hashlib.sha256(original_bytes).hexdigest()
            retrieved = archive.get_blob(receipt.blob_sha256)
            assert retrieved == original_bytes
        finally:
            os.unlink(worker_script)
    finally:
        server.shutdown()
        server.server_close()


# -------------------------------------------------------------------------
# 2. Identical Body Fetch and A -> B -> A Revision Lineage
# -------------------------------------------------------------------------

def test_identical_body_and_aba_revision_lineage(archive: EvidenceArchive):
    """Sequence test:

    1st fetch (A): 1 receipt, 1 revision (prev=None), 1 blob.
    2nd fetch (A): 2 receipts, 1 revision (reused), 1 blob.
    3rd fetch (B): 3 receipts, 2 revisions (prev=rev1), 2 blobs.
    4th fetch (A): 4 receipts, 3 revisions (prev=rev2), 2 blobs (reused blob A).
    """
    body_a = json.dumps({"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-16", "percentRate": 3.88}]}).encode()
    body_b = json.dumps({"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-17", "percentRate": 3.89}]}).encode()

    sha_a = hashlib.sha256(body_a).hexdigest()
    sha_b = hashlib.sha256(body_b).hexdigest()

    # Attempt 1: Body A
    cap1 = make_capture(source_id="nyfed_effr", body=body_a)
    rec1 = archive.record_capture(cap1, receipt_id="rec-001")
    assert rec1.seq == 1
    assert rec1.blob_sha256 == sha_a
    assert rec1.revision_id is not None

    rev1 = archive.get_revision(rec1.revision_id)
    assert rev1 is not None
    assert rev1.first_seen_seq == 1
    assert rev1.blob_sha256 == sha_a
    assert rev1.prev_revision_id is None

    # Attempt 2: Body A (identical)
    cap2 = make_capture(source_id="nyfed_effr", body=body_a)
    rec2 = archive.record_capture(cap2, receipt_id="rec-002")
    assert rec2.seq == 2
    assert rec2.blob_sha256 == sha_a
    # Revision is reused!
    assert rec2.revision_id == rec1.revision_id

    # Verify counts: 2 receipts, 1 revision
    all_recs = archive.query_receipts()
    all_revs = archive.query_revisions()
    assert len(all_recs) == 2
    assert len(all_revs) == 1

    # Attempt 3: Body B (changed)
    cap3 = make_capture(source_id="nyfed_effr", body=body_b)
    rec3 = archive.record_capture(cap3, receipt_id="rec-003")
    assert rec3.seq == 3
    assert rec3.blob_sha256 == sha_b
    assert rec3.revision_id != rec1.revision_id

    rev2 = archive.get_revision(rec3.revision_id)
    assert rev2 is not None
    assert rev2.first_seen_seq == 3
    assert rev2.blob_sha256 == sha_b
    assert rev2.prev_revision_id == rev1.revision_id

    # Attempt 4: Body A (reverted to A: A -> B -> A)
    cap4 = make_capture(source_id="nyfed_effr", body=body_a)
    rec4 = archive.record_capture(cap4, receipt_id="rec-004")
    assert rec4.seq == 4
    assert rec4.blob_sha256 == sha_a
    # New revision with lineage pointing to rev2, but blob is reused!
    assert rec4.revision_id != rev2.revision_id
    assert rec4.revision_id != rev1.revision_id

    rev3 = archive.get_revision(rec4.revision_id)
    assert rev3 is not None
    assert rev3.first_seen_seq == 4
    assert rev3.blob_sha256 == sha_a
    assert rev3.prev_revision_id == rev2.revision_id

    # Total counts: 4 receipts, 3 revisions, 2 blobs
    assert len(archive.query_receipts()) == 4
    assert len(archive.query_revisions()) == 3

    integrity = archive.verify_integrity()
    assert integrity["healthy"] is True
    assert integrity["total_receipts"] == 4
    assert integrity["total_revisions"] == 3
    assert integrity["total_blobs_checked"] == 2


def test_different_sources_do_not_merge_identities(archive: EvidenceArchive):
    """Different source_id / URL returning identical bytes maintain distinct revisions."""
    same_bytes = b"[]"
    cap1 = make_capture(source_id="github_deepseek_v3_releases", body=same_bytes)
    rec1 = archive.record_capture(cap1)

    cap2 = make_capture(source_id="hf_deepseek_models", body=same_bytes)
    rec2 = archive.record_capture(cap2)

    assert rec1.blob_sha256 == rec2.blob_sha256
    assert rec1.revision_id != rec2.revision_id

    rev1 = archive.get_revision(rec1.revision_id)
    rev2 = archive.get_revision(rec2.revision_id)
    assert rev1.source_id == "github_deepseek_v3_releases"
    assert rev2.source_id == "hf_deepseek_models"
    assert rev1.revision_id != rev2.revision_id


# -------------------------------------------------------------------------
# 3. Idempotency, Conflict Rejection, and Concurrency Safety
# -------------------------------------------------------------------------

def test_idempotency_and_conflict_rejection(archive: EvidenceArchive):
    """Re-recording with same receipt_id and identical payload is idempotent.

    Re-using receipt_id with conflicting payload raises ArchiveConflictError.
    """
    cap = make_capture(source_id="nyfed_effr", body=b'{"refRates":[]}')
    rec1 = archive.record_capture(cap, receipt_id="receipt-idem-001")
    assert rec1.receipt_id == "receipt-idem-001"
    assert rec1.seq == 1

    # Exact duplicate submission
    rec2 = archive.record_capture(cap, receipt_id="receipt-idem-001")
    assert rec2 == rec1
    assert len(archive.query_receipts()) == 1

    # Conflicting submission with same receipt_id but different body
    conflicting_cap = make_capture(
        source_id="nyfed_effr",
        body=b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-16","percentRate":3.88}]}',
    )
    with pytest.raises(ArchiveConflictError) as exc_info:
        archive.record_capture(conflicting_cap, receipt_id="receipt-idem-001")
    assert "Conflict on receipt_id 'receipt-idem-001'" in str(exc_info.value)

    # Conflicting submission with same receipt_id but different http_status
    conflicting_status_cap = make_capture(
        source_id="nyfed_effr",
        body=b'{"refRates":[]}',
        status_code=500,
    )
    with pytest.raises(ArchiveConflictError) as exc_info2:
        archive.record_capture(conflicting_status_cap, receipt_id="receipt-idem-001")
    assert "Conflict on receipt_id 'receipt-idem-001'" in str(exc_info2.value)


def test_concurrent_writers_safety(archive: EvidenceArchive):
    """Concurrent writers recording identical and distinct receipts do not corrupt CAS or SQLite."""
    shared_body = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-16","percentRate":3.88}]}'

    # Pre-instantiate immutable CaptureResults per receipt_id for identical replay testing
    # (Per B2.3: Avoid generating new timestamps in each thread which creates conflicting fingerprints)
    distinct_caps = {
        f"concurrent-worker-{k}": make_capture(source_id="nyfed_effr", body=shared_body)
        for k in range(4)
    }

    def worker_task(idx: int) -> ArchiveReceipt:
        # Some workers use same receipt_id, others use distinct
        rid = f"concurrent-worker-{idx % 4}"
        cap = distinct_caps[rid]
        return archive.record_capture(cap, receipt_id=rid)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker_task, i) for i in range(20)]
        results = [f.result() for f in futures]

    assert len(results) == 20
    # 4 distinct receipt_ids were used (0, 1, 2, 3)
    receipts = archive.query_receipts()
    assert len(receipts) == 4
    # All 4 receipts should share the same revision and blob
    assert len(archive.query_revisions()) == 1

    integrity = archive.verify_integrity()
    assert integrity["healthy"] is True


def test_concurrent_competing_payloads_same_id(archive: EvidenceArchive):
    """Competing writers attempting to record distinct payloads with the same receipt_id:
    exactly one succeeds, and all others fail with ArchiveConflictError."""
    target_rid = "racing-same-id"
    bodies = [f'{{"writer":{i}}}'.encode("utf-8") for i in range(10)]

    successes = []
    conflicts = []

    def racer(idx: int):
        cap = make_capture(source_id="nyfed_effr", body=bodies[idx])
        try:
            rec = archive.record_capture(cap, receipt_id=target_rid)
            successes.append(rec)
        except ArchiveConflictError as err:
            conflicts.append(err)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(racer, i) for i in range(10)]
        for f in futures:
            f.result()

    assert len(successes) == 1
    assert len(conflicts) == 9
    receipts = archive.query_receipts()
    assert len(receipts) == 1
    assert receipts[0].receipt_id == target_rid
    integrity = archive.verify_integrity()
    assert integrity["healthy"] is True


# -------------------------------------------------------------------------
# 4. Failpoint Crash Resilience and Rollback
# -------------------------------------------------------------------------

def test_failpoint_crash_recovery(archive: EvidenceArchive):
    """When a writer crashes after blob fsync but before SQLite commit:

    1. The uncommitted receipt/revision is not in SQLite.
    2. Previously committed data is 100% intact.
    3. Reopened archive is healthy with no orphaned committed references.
    """
    body1 = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-15","percentRate":3.85}]}'
    cap1 = make_capture(source_id="nyfed_effr", body=body1)
    rec1 = archive.record_capture(cap1, receipt_id="stable-001")
    assert rec1.seq == 1

    # Writer triggers crash failpoint
    body2 = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-16","percentRate":3.88}]}'
    cap2 = make_capture(source_id="nyfed_effr", body=body2)
    with pytest.raises(RuntimeError, match="Failpoint crash"):
        archive.record_capture(cap2, receipt_id="crashed-002", failpoint="after_blob_fsync_before_db_commit")

    # Reopen archive on same state_dir
    archive_reopened = EvidenceArchive(archive.state_dir)
    integrity = archive_reopened.verify_integrity()
    assert integrity["healthy"] is True
    assert integrity["total_receipts"] == 1
    assert integrity["total_revisions"] == 1

    # stable-001 is completely intact
    stable = archive_reopened.get_receipt("stable-001")
    assert stable is not None
    assert stable.blob_sha256 == hashlib.sha256(body1).hexdigest()
    assert archive_reopened.get_blob(stable.blob_sha256) == body1

    # crashed-002 was never committed to SQLite
    assert archive_reopened.get_receipt("crashed-002") is None


def test_append_only_triggers_prevent_mutation(archive: EvidenceArchive):
    """SQLite triggers forbid UPDATE and DELETE on receipts and revisions."""
    cap = make_capture(source_id="nyfed_effr", body=b'{"refRates":[]}')
    rec = archive.record_capture(cap)

    with archive._get_connection() as conn:
        with pytest.raises(sqlite3.DatabaseError, match="Mutation forbidden"):
            conn.execute("UPDATE receipts SET status = 'tampered' WHERE receipt_id = ?;", (rec.receipt_id,))

        with pytest.raises(sqlite3.DatabaseError, match="Deletion forbidden"):
            conn.execute("DELETE FROM receipts WHERE receipt_id = ?;", (rec.receipt_id,))

        with pytest.raises(sqlite3.DatabaseError, match="Mutation forbidden"):
            conn.execute("UPDATE revisions SET byte_size = 999 WHERE revision_id = ?;", (rec.revision_id,))

        with pytest.raises(sqlite3.DatabaseError, match="Deletion forbidden"):
            conn.execute("DELETE FROM revisions WHERE revision_id = ?;", (rec.revision_id,))


# -------------------------------------------------------------------------
# 5. Diagnostic Snapshots (403 / HTML) vs Incomplete/Timeout Captures
# -------------------------------------------------------------------------

def test_diagnostic_snapshots_for_403_and_unverified(archive: EvidenceArchive):
    """Complete 403 Forbidden and unverified HTML are preserved with raw CAS hash (usable=false).

    Incomplete stream and network timeouts do NOT receive a CAS blob or revision.
    """
    # 1. Complete HTTP 403 (blocked)
    blocked_body = b"<html><head><title>403 Forbidden</title></head><body>Access Denied</body></html>"
    cap_403 = make_capture(
        source_id="bls_cpi_rss",
        body=blocked_body,
        status_code=403,
        headers={"content-type": "text/html"},
        complete=True,
    )
    rec_403 = archive.record_capture(cap_403)
    assert rec_403.status == "blocked"
    assert rec_403.usable_for_forecast is False
    assert rec_403.blob_sha256 == hashlib.sha256(blocked_body).hexdigest()
    assert rec_403.revision_id is not None
    assert archive.get_blob(rec_403.blob_sha256) == blocked_body

    # 2. Complete unverified HTML (GDPNow)
    html_body = b"<!DOCTYPE html><html><body><h1>GDPNow</h1><p>Model estimate: 3.2%</p></body></html>"
    cap_html = make_capture(
        source_id="atlanta_gdpnow",
        body=html_body,
        status_code=200,
        headers={"content-type": "text/html"},
        complete=True,
    )
    rec_html = archive.record_capture(cap_html)
    assert rec_html.status == "unverified"
    assert rec_html.usable_for_forecast is False
    assert rec_html.blob_sha256 == hashlib.sha256(html_body).hexdigest()
    assert rec_html.revision_id is not None
    assert archive.get_blob(rec_html.blob_sha256) == html_body

    # 3. Incomplete capture (partial stream truncated by 2 MiB limit)
    partial_body = b"x" * (2 * 1024 * 1024)
    cap_partial = make_capture(
        source_id="nyfed_effr",
        body=partial_body,
        status_code=200,
        complete=False,
        limit_exceeded=True,
        error_msg=None,
    )
    rec_partial = archive.record_capture(cap_partial)
    assert rec_partial.complete is False
    assert rec_partial.blob_sha256 is None
    assert rec_partial.revision_id is None

    # 4. Incomplete capture (network error / deadline timeout)
    cap_timeout = make_capture(
        source_id="nyfed_effr",
        body=b"",
        status_code=None,
        complete=False,
        error_msg="Overall source deadline exceeded (15.0s)",
    )
    rec_timeout = archive.record_capture(cap_timeout)
    assert rec_timeout.complete is False
    assert rec_timeout.blob_sha256 is None
    assert rec_timeout.revision_id is None
    assert rec_timeout.status == "network_error"


# -------------------------------------------------------------------------
# 6. Point-in-time as_of_seq Cutoff Isolation
# -------------------------------------------------------------------------

def test_as_of_seq_cutoff_isolation(archive: EvidenceArchive):
    """as_of_seq cutoff query hides later receipts and revisions, preserving reproducible history."""
    body1 = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-15","percentRate":3.85}]}'
    body2 = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-16","percentRate":3.88}]}'
    body3 = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-17","percentRate":3.90}]}'

    # Seq 1: Revision 1
    rec1 = archive.record_capture(make_capture("nyfed_effr", body=body1), receipt_id="r1")
    # Seq 2: Incomplete attempt (no revision)
    rec2 = archive.record_capture(
        make_capture("nyfed_effr", body=b"", status_code=None, complete=False, error_msg="timeout"),
        receipt_id="r2",
    )
    # Seq 3: Revision 2
    rec3 = archive.record_capture(make_capture("nyfed_effr", body=body2), receipt_id="r3")
    # Seq 4: Revision 3
    rec4 = archive.record_capture(make_capture("nyfed_effr", body=body3), receipt_id="r4")

    assert rec1.seq == 1
    assert rec2.seq == 2
    assert rec3.seq == 3
    assert rec4.seq == 4

    # Cutoff at seq 1: only receipt 1 and revision 1 exist
    recs_as_of_1 = archive.query_receipts(as_of_seq=1)
    assert len(recs_as_of_1) == 1
    assert recs_as_of_1[0].receipt_id == "r1"
    rev_as_of_1 = archive.get_latest_revision("nyfed_effr", CANONICAL_SOURCES["nyfed_effr"], as_of_seq=1)
    assert rev_as_of_1.revision_id == rec1.revision_id

    # Receipt 3 and 4 are invisible as of seq 1
    assert archive.get_receipt("r3", as_of_seq=1) is None
    assert archive.get_receipt("r4", as_of_seq=1) is None
    assert archive.get_revision(rec3.revision_id, as_of_seq=1) is None

    # Cutoff at seq 2: receipts 1 and 2 visible, latest revision is still revision 1
    recs_as_of_2 = archive.query_receipts(as_of_seq=2)
    assert len(recs_as_of_2) == 2
    rev_as_of_2 = archive.get_latest_revision("nyfed_effr", CANONICAL_SOURCES["nyfed_effr"], as_of_seq=2)
    assert rev_as_of_2.revision_id == rec1.revision_id

    # Cutoff at seq 3: latest revision is revision 2
    rev_as_of_3 = archive.get_latest_revision("nyfed_effr", CANONICAL_SOURCES["nyfed_effr"], as_of_seq=3)
    assert rev_as_of_3.revision_id == rec3.revision_id

    # Cutoff at seq 4: latest revision is revision 3
    rev_as_of_4 = archive.get_latest_revision("nyfed_effr", CANONICAL_SOURCES["nyfed_effr"], as_of_seq=4)
    assert rev_as_of_4.revision_id == rec4.revision_id

    # Historical stability: adding seq 5 does NOT change past cutoff queries
    archive.record_capture(make_capture("nyfed_effr", body=b'{"refRates":[]}'), receipt_id="r5")
    recs_as_of_3_after = archive.query_receipts(as_of_seq=3)
    assert len(recs_as_of_3_after) == 3
    assert archive.get_latest_revision("nyfed_effr", CANONICAL_SOURCES["nyfed_effr"], as_of_seq=3).revision_id == rec3.revision_id


# -------------------------------------------------------------------------
# 7. Single HTTP GET per Capture & No Offline Network Calls
# -------------------------------------------------------------------------

def test_single_http_get_per_capture_and_no_offline_network(archive: EvidenceArchive):
    """Fixture verifies exactly 1 HTTP GET occurs per capture.

    Probe diagnostic and archive persistence share the single CaptureResult.
    Offline reads / replays do not touch network.
    """
    request_counter = {"count": 0}
    test_body = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-16","percentRate":3.88}]}'

    class CountingHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            request_counter["count"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(test_body)

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), CountingHandler)
    server_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        url = f"http://127.0.0.1:{server_port}/rates.json"
        worker_code = f"""import sys, json, base64, requests

raw = sys.stdin.read()
sess = requests.Session()
sess.trust_env = False
resp = sess.get("{url}")

out = {{
    "status_code": resp.status_code,
    "headers": {{str(k).lower(): str(v) for k, v in resp.headers.items()}},
    "content_type": resp.headers.get("Content-Type"),
    "body_b64": base64.b64encode(resp.content).decode("ascii"),
    "bytes_received": len(resp.content),
    "bytes_stored": len(resp.content),
    "complete": True,
    "limit_exceeded": False,
    "final_url": "{CANONICAL_SOURCES['nyfed_effr']}",
    "error_msg": None,
}}
print(json.dumps(out))
"""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(worker_code)
            worker_script = f.name

        try:
            probe = SourceProbe(worker_cmd=[sys.executable, worker_script])

            # Single fetch
            capture = probe.capture_source("nyfed_effr", CANONICAL_SOURCES["nyfed_effr"])
            assert request_counter["count"] == 1

            # Layer 0 probe evaluation uses capture (0 network calls)
            probe_res = probe.probe_single({"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}, capture=capture)
            assert request_counter["count"] == 1
            assert probe_res["status"] == "valid"

            # Layer 1 archive persistence uses same capture (0 network calls)
            receipt = archive.record_capture(capture=capture, probe_result=probe_res)
            assert request_counter["count"] == 1
            assert receipt.blob_sha256 == hashlib.sha256(test_body).hexdigest()

            # Offline reads and queries (0 network calls)
            blob = archive.get_blob(receipt.blob_sha256)
            assert blob == test_body
            rec_read = archive.get_receipt(receipt.receipt_id)
            assert rec_read.receipt_id == receipt.receipt_id
            assert request_counter["count"] == 1
        finally:
            os.unlink(worker_script)
    finally:
        server.shutdown()
        server.server_close()


# -------------------------------------------------------------------------
# 8. Strict Worker Schema Validation
# -------------------------------------------------------------------------

def test_strict_worker_schema_validation(tmp_path: Path):
    """Supervisor enforces strict typing: non-bool complete or counter mismatch fails schema."""
    # Worker returning integer 1 for complete (instead of boolean True)
    bad_worker = tmp_path / "bad_worker.py"
    bad_worker.write_text("""import sys, json, base64
raw = sys.stdin.read()
body = b'{"refRates":[]}'
out = {
    "status_code": 200,
    "headers": {"content-type": "application/json"},
    "body_b64": base64.b64encode(body).decode("ascii"),
    "bytes_received": len(body),
    "bytes_stored": len(body),
    "complete": 1,  # NOT a boolean!
    "limit_exceeded": False,
    "final_url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json",
    "error_msg": None,
}
print(json.dumps(out))
""")

    probe = SourceProbe(worker_cmd=[sys.executable, str(bad_worker)])
    capture = probe.capture_source("nyfed_effr", CANONICAL_SOURCES["nyfed_effr"])
    assert capture.complete is False
    assert "Worker 'complete' must be boolean" in capture.error_msg

    # Worker returning counter mismatch
    mismatch_worker = tmp_path / "mismatch_worker.py"
    mismatch_worker.write_text("""import sys, json, base64
raw = sys.stdin.read()
body = b'{"refRates":[]}'
out = {
    "status_code": 200,
    "headers": {"content-type": "application/json"},
    "body_b64": base64.b64encode(body).decode("ascii"),
    "bytes_received": 100,
    "bytes_stored": 50,  # len(body) is 15 != 50!
    "complete": True,
    "limit_exceeded": False,
    "final_url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json",
    "error_msg": None,
}
print(json.dumps(out))
""")

    probe2 = SourceProbe(worker_cmd=[sys.executable, str(mismatch_worker)])
    capture2 = probe2.capture_source("nyfed_effr", CANONICAL_SOURCES["nyfed_effr"])
    assert capture2.complete is False
    assert "Worker counter inconsistency" in capture2.error_msg


# -------------------------------------------------------------------------
# 9. Real Separate Process SIGKILL Tests & Injected Error Rollback
# -------------------------------------------------------------------------

def test_separate_process_sigkill_boundary_a_blob_published_before_db(tmp_path: Path):
    """Separate process writer killed with SIGKILL after CAS blob is published but before DB begin:
    orphaned blob is created on disk, no DB receipt or revision is committed, baseline data intact."""
    sdir = tmp_path / "sigkill_boundary_a"
    archive = EvidenceArchive(sdir)

    # 1. Establish baseline record
    cap0 = make_capture(source_id="nyfed_effr", body=b'{"baseline":1}')
    rec0 = archive.record_capture(cap0, receipt_id="baseline-001")
    assert rec0.seq == 1
    assert len(archive.query_receipts()) == 1

    marker_file = tmp_path / "boundary_a_marker.txt"
    crashed_body = b'{"crashed_at":"boundary_a","data":"xyz"}'
    crashed_cap = make_capture(source_id="nyfed_effr", body=crashed_body)
    crashed_sha = hashlib.sha256(crashed_body).hexdigest()

    # Subprocess script that runs record_capture with test_hook
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    subproc_code = f"""import sys, time, os, json
sys.path.insert(0, r"{repo_root}")
from python.evaluation.evidence_archive import EvidenceArchive
from python.evaluation.source_probe import CaptureResult

archive = EvidenceArchive(r"{sdir}")
cap = CaptureResult(
    source_id="{crashed_cap.source_id}",
    initial_url="{crashed_cap.initial_url}",
    final_url="{crashed_cap.final_url}",
    body={repr(crashed_body)},
    status_code=200,
    headers={{"content-type": "application/json"}},
    fetch_started_at="{crashed_cap.fetch_started_at}",
    fetch_completed_at="{crashed_cap.fetch_completed_at}",
    duration_ms=10.0,
    bytes_received={len(crashed_body)},
    bytes_stored={len(crashed_body)},
    complete=True,
    limit_exceeded=False,
    error_msg=None,
    collector_version="{crashed_cap.collector_version}",
)

def hook(point):
    if point == "after_blob_published_before_db":
        with open(r"{marker_file}", "w") as f:
            f.write(f"reached:{os.getpid()}")
        while True:
            time.sleep(0.01)

archive.record_capture(cap, receipt_id="crashed-a-001", test_hook=hook)
"""
    script_path = tmp_path / "writer_a.py"
    script_path.write_text(subproc_code)

    proc = subprocess.Popen([sys.executable, str(script_path)])
    try:
        # Wait for marker file
        t0 = time.monotonic()
        while not marker_file.is_file():
            if time.monotonic() - t0 > 5.0:
                raise TimeoutError("Subprocess failed to reach hook boundary A within 5 seconds")
            time.sleep(0.05)

        # Confirm process is alive and send SIGKILL
        assert proc.poll() is None
        proc.send_signal(signal.SIGKILL)
        ret = proc.wait()
        assert ret != 0

        # Reopen archive and verify state
        reopened = EvidenceArchive(sdir)
        receipts = reopened.query_receipts()
        assert len(receipts) == 1
        assert receipts[0].receipt_id == "baseline-001"
        assert reopened.get_receipt("crashed-a-001") is None

        # The CAS blob was published before crash, so it exists as an orphan
        assert reopened._blob_path(crashed_sha).is_file() is True
        assert reopened.get_blob(crashed_sha) == crashed_body

        # Verify integrity: orphan blobs are permitted, archive is 100% healthy
        integrity = reopened.verify_integrity()
        assert integrity["healthy"] is True
        assert integrity["total_receipts"] == 1
        assert integrity["total_revisions"] == 1
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_separate_process_sigkill_boundary_b_inside_db_tx_before_commit(tmp_path: Path):
    """Separate process writer killed with SIGKILL inside SQLite transaction before commit:
    SQLite WAL recovery rolls back transaction, no uncommitted rows exist, baseline data intact."""
    sdir = tmp_path / "sigkill_boundary_b"
    archive = EvidenceArchive(sdir)

    # 1. Establish baseline record
    cap0 = make_capture(source_id="nyfed_effr", body=b'{"baseline":2}')
    rec0 = archive.record_capture(cap0, receipt_id="baseline-002")
    assert rec0.seq == 1
    assert len(archive.query_receipts()) == 1

    marker_file = tmp_path / "boundary_b_marker.txt"
    crashed_body = b'{"crashed_at":"boundary_b","data":"abc"}'
    crashed_cap = make_capture(source_id="nyfed_effr", body=crashed_body)
    crashed_sha = hashlib.sha256(crashed_body).hexdigest()

    # Subprocess script that runs record_capture with test_hook inside DB tx
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    subproc_code = f"""import sys, time, os, json
sys.path.insert(0, r"{repo_root}")
from python.evaluation.evidence_archive import EvidenceArchive
from python.evaluation.source_probe import CaptureResult

archive = EvidenceArchive(r"{sdir}")
cap = CaptureResult(
    source_id="{crashed_cap.source_id}",
    initial_url="{crashed_cap.initial_url}",
    final_url="{crashed_cap.final_url}",
    body={repr(crashed_body)},
    status_code=200,
    headers={{"content-type": "application/json"}},
    fetch_started_at="{crashed_cap.fetch_started_at}",
    fetch_completed_at="{crashed_cap.fetch_completed_at}",
    duration_ms=10.0,
    bytes_received={len(crashed_body)},
    bytes_stored={len(crashed_body)},
    complete=True,
    limit_exceeded=False,
    error_msg=None,
    collector_version="{crashed_cap.collector_version}",
)

def hook(point):
    if point == "inside_db_tx_before_commit":
        with open(r"{marker_file}", "w") as f:
            f.write(f"reached:{os.getpid()}")
        while True:
            time.sleep(0.01)

archive.record_capture(cap, receipt_id="crashed-b-001", test_hook=hook)
"""
    script_path = tmp_path / "writer_b.py"
    script_path.write_text(subproc_code)

    proc = subprocess.Popen([sys.executable, str(script_path)])
    try:
        # Wait for marker file
        t0 = time.monotonic()
        while not marker_file.is_file():
            if time.monotonic() - t0 > 5.0:
                raise TimeoutError("Subprocess failed to reach hook boundary B within 5 seconds")
            time.sleep(0.05)

        # Confirm process is alive and send SIGKILL
        assert proc.poll() is None
        proc.send_signal(signal.SIGKILL)
        ret = proc.wait()
        assert ret != 0

        # Reopen archive and verify state
        reopened = EvidenceArchive(sdir)
        receipts = reopened.query_receipts()
        assert len(receipts) == 1
        assert receipts[0].receipt_id == "baseline-002"
        assert reopened.get_receipt("crashed-b-001") is None

        # Verify integrity: SQLite WAL automatically recovered cleanly
        integrity = reopened.verify_integrity()
        assert integrity["healthy"] is True
        assert integrity["total_receipts"] == 1
        assert integrity["total_revisions"] == 1
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_injected_error_transaction_rollback(archive: EvidenceArchive):
    """Exception raised inside DB transaction rolls back SQLite transaction without partial state."""
    cap0 = make_capture(source_id="nyfed_effr", body=b'{"baseline":3}')
    archive.record_capture(cap0, receipt_id="rollback-base-001")

    failing_cap = make_capture(source_id="nyfed_effr", body=b'{"failing":1}')

    def failing_hook(point):
        if point == "inside_db_tx_before_commit":
            raise RuntimeError("Injected database failure before commit")

    with pytest.raises(RuntimeError, match="Injected database failure"):
        archive.record_capture(failing_cap, receipt_id="failing-tx-001", test_hook=failing_hook)

    # SQLite transaction was rolled back cleanly
    assert archive.get_receipt("failing-tx-001") is None
    assert len(archive.query_receipts()) == 1
    integrity = archive.verify_integrity()
    assert integrity["healthy"] is True


# -------------------------------------------------------------------------
# -------------------------------------------------------------------------
# 10. Canonical Worker Gzip and Single GET via Fixture Worker
# -------------------------------------------------------------------------

def test_canonical_worker_loopback_gzip_and_single_get(archive: EvidenceArchive, tmp_path: Path):
    """Canonical worker and _fetch_bounded_core correctly decode gzip content and make exactly 1 GET via explicit fixture-worker."""
    raw_payload = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-18","percentRate":4.95}]}'
    gzipped = gzip.compress(raw_payload)
    get_counter = [0]

    class GzipLoopbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            get_counter[0] += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(gzipped)))
            self.end_headers()
            self.wfile.write(gzipped)

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), GzipLoopbackHandler)
    server_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        fixture_worker_script = tmp_path / "gzip_fixture_worker.py"
        fixture_worker_script.write_text(
            "import sys, urllib.parse, requests\n"
            f"sys.path.insert(0, r'{repo_root}')\n"
            "from python.evaluation import source_probe_worker\n"
            "from python.evaluation.source_probe import create_anonymous_session\n\n"
            "class LoopbackRouteAdapter(requests.adapters.HTTPAdapter):\n"
            "    def __init__(self, target_base: str) -> None:\n"
            "        super().__init__()\n"
            "        self.target_base = target_base.rstrip('/')\n\n"
            "    def send(self, request: requests.PreparedRequest, **kwargs):\n"
            "        p = urllib.parse.urlparse(request.url)\n"
            "        original_host = p.netloc\n"
            "        request.url = self.target_base + p.path + (f'?{p.query}' if p.query else '')\n"
            "        request.headers['Host'] = original_host\n"
            "        return super().send(request, **kwargs)\n\n"
            "sess = create_anonymous_session()\n"
            f"sess.mount('https://markets.newyorkfed.org', LoopbackRouteAdapter('http://127.0.0.1:{server_port}'))\n"
            "source_probe_worker.main(session=sess)\n"
        )

        with SourceProbe(worker_cmd=[sys.executable, str(fixture_worker_script)]) as probe:
            capture = probe.capture_source("nyfed_effr", CANONICAL_SOURCES["nyfed_effr"])

        # Verify single HTTP GET occurred
        assert get_counter[0] == 1, f"Expected exactly 1 GET request, got {get_counter[0]}"

        # Verify content-decoded payload and complete metadata
        assert capture.complete is True
        assert capture.body == raw_payload
        assert capture.bytes_stored == len(raw_payload)
        assert capture.headers.get("content-encoding") == "gzip"
        assert capture.status_code == 200

        # Record into archive and verify CAS blob matches decompressed bytes
        rec = archive.record_capture(capture, receipt_id="gzip-canon-001")
        assert rec.blob_sha256 == hashlib.sha256(raw_payload).hexdigest()
        assert archive.get_blob(rec.blob_sha256) == raw_payload
    finally:
        server.shutdown()
        server.server_close()


def test_default_worker_ignores_ambient_loopback_env(tmp_path: Path):
    """Default worker does not read PYTHIA_TEST_LOOPBACK_MAP; ambient env does not redirect default worker."""
    hits = [0]

    class MockHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits[0] += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), MockHandler)
    server_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    old_env = os.environ.get("PYTHIA_TEST_LOOPBACK_MAP")
    os.environ["PYTHIA_TEST_LOOPBACK_MAP"] = json.dumps({
        "https://markets.newyorkfed.org": f"http://127.0.0.1:{server_port}"
    })
    try:
        # Default SourceProbe with default worker
        # Use short timeout so test stays bounded and offline
        with SourceProbe(timeout=0.10) as probe:
            capture = probe.capture_source("nyfed_effr", CANONICAL_SOURCES["nyfed_effr"])

        # The ambient loopback server must NEVER be contacted by the default worker
        assert hits[0] == 0, f"Default worker unexpectedly routed request to ambient loopback server ({hits[0]} hits)"
        assert capture.body != b"{}"
    finally:
        if old_env is None:
            os.environ.pop("PYTHIA_TEST_LOOPBACK_MAP", None)
        else:
            os.environ["PYTHIA_TEST_LOOPBACK_MAP"] = old_env
        server.shutdown()
        server.server_close()


# 11. Reviewer Defect Controls Suite
# -------------------------------------------------------------------------

def test_reviewer_controls_complete_suite(tmp_path: Path):
    """Adapt all reviewer defect controls: verifies that all 9 previously reproducing defects are fixed."""
    folder = tmp_path / "reviewer_suite"
    folder.mkdir()

    # Baseline capture
    cap = make_capture(
        source_id="nyfed_effr",
        body=b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-16","percentRate":3.88}]}',
        headers={"content-type": "application/json", "etag": "v1"},
    )

    # 1. Identical payload positive replay
    arch_id = EvidenceArchive(folder / "idempotency")
    orig = arch_id.record_capture(cap, receipt_id="stable-id")
    replay = arch_id.record_capture(cap, receipt_id="stable-id")
    assert replay == orig
    assert len(arch_id.query_receipts()) == 1

    # 2. Metadata changes under same receipt_id must raise ArchiveConflictError
    with pytest.raises(ArchiveConflictError):
        arch_id.record_capture(replace(cap, headers={**cap.headers, "etag": "v2"}), receipt_id="stable-id")

    with pytest.raises(ArchiveConflictError):
        arch_id.record_capture(replace(cap, fetch_completed_at="2026-10-18T05:00:02.000000Z"), receipt_id="stable-id")

    with pytest.raises(ArchiveConflictError):
        arch_id.record_capture(replace(cap, collector_version="review-capture/2"), receipt_id="stable-id")

    # 3. Negative capture error change under same receipt_id
    negative = replace(
        cap,
        body=b"",
        status_code=None,
        final_url=None,
        headers={},
        bytes_received=None,
        bytes_stored=None,
        complete=False,
        error_msg="timeout",
    )
    arch_id.record_capture(negative, receipt_id="negative-id")
    with pytest.raises(ArchiveConflictError):
        arch_id.record_capture(replace(negative, error_msg="resolver failed"), receipt_id="negative-id")

    # 4. CAS same-size and wrong-size corruption prevention
    cas_dir = EvidenceArchive(folder / "cas-integrity")
    first = cas_dir.record_capture(cap, receipt_id="before-corruption")
    blob_path = cas_dir._blob_path(first.blob_sha256)
    original_bytes = blob_path.read_bytes()

    # Mutate to same size, wrong hash
    wrong = bytes([cap.body[0] ^ 1]) + cap.body[1:]
    blob_path.write_bytes(wrong)

    # Attempt to record identical cap: must be rejected with ArchiveIntegrityError without clobbering
    with pytest.raises(ArchiveIntegrityError):
        cas_dir.record_capture(cap, receipt_id="after-corruption")

    # File must NOT have been overwritten
    assert blob_path.read_bytes() == wrong
    # No receipt committed
    assert cas_dir.get_receipt("after-corruption") is None
    # verify_integrity reports corruption
    integ = cas_dir.verify_integrity()
    assert integ["healthy"] is False

    # Restore and test wrong size
    blob_path.write_bytes(original_bytes[:5])
    with pytest.raises(ArchiveIntegrityError):
        cas_dir.record_capture(cap, receipt_id="after-truncation")

    # 5. Malformed worker cases
    sid = "github_deepseek_v3_releases"
    full_body = b"[]"
    full_dict = {
        "status_code": 200,
        "headers": {"content-type": "application/json"},
        "body_b64": base64.b64encode(full_body).decode("ascii"),
        "bytes_received": 2,
        "bytes_stored": 2,
        "complete": True,
        "limit_exceeded": False,
        "final_url": CANONICAL_SOURCES[sid],
        "error_msg": None,
    }

    malformed_cases = [
        ("missing_explicit_eof", {k: v for k, v in full_dict.items() if k != "complete"}),
        ("invalid_base64_ignored_suffix", dict(full_dict, body_b64="W10=!!")),
        ("contradictory_complete_counters", dict(full_dict, bytes_received=3)),
        ("missing_required_body_metadata", {"complete": True}),
    ]

    for name, payload in malformed_cases:
        worker_file = folder / f"{name}.py"
        worker_file.write_text("import json,sys\nsys.stdin.read()\nprint(" + repr(json.dumps(payload)) + ")\n")
        with SourceProbe(worker_cmd=[sys.executable, str(worker_file)]) as probe:
            captured = probe.capture_source(sid, CANONICAL_SOURCES[sid])
            diag = probe.probe_single({"source_id": sid, "url": CANONICAL_SOURCES[sid]}, capture=captured)

        assert captured.complete is False, f"Case {name} must have complete=False"
        target_arch = EvidenceArchive(folder / name)
        receipt = target_arch.record_capture(captured, receipt_id=name, probe_result=diag)
        assert receipt.blob_sha256 is None, f"Case {name} must not store CAS blob"
        assert receipt.revision_id is None, f"Case {name} must not create revision"


def test_direct_invalid_dto_rejected_at_archive_boundary(archive: EvidenceArchive):
    """Direct invalid DTOs passed to record_capture are rejected at the boundary."""
    cap = make_capture(source_id="nyfed_effr", body=b'{"ok":1}')

    # Non-bool complete
    with pytest.raises(ValueError, match="must be bool"):
        archive.record_capture(replace(cap, complete=1))  # type: ignore

    # Non-bool limit_exceeded
    with pytest.raises(ValueError, match="must be bool"):
        archive.record_capture(replace(cap, limit_exceeded="no"))  # type: ignore

    # Negative counter
    with pytest.raises(ValueError, match="must be non-negative integer"):
        archive.record_capture(replace(cap, bytes_received=-1))

    # Complete=True with error_msg
    with pytest.raises(ValueError, match="Contradictory CaptureResult"):
        archive.record_capture(replace(cap, complete=True, error_msg="oops"))

    # Complete=True with limit_exceeded=True
    with pytest.raises(ValueError, match="Contradictory CaptureResult"):
        archive.record_capture(replace(cap, complete=True, limit_exceeded=True))

    # Complete=True with counter mismatch
    with pytest.raises(ValueError, match="counter mismatch"):
        archive.record_capture(replace(cap, complete=True, bytes_received=10, bytes_stored=10, body=b"123"))

    # Non-str headers
    with pytest.raises(ValueError, match="header values must be str"):
        archive.record_capture(replace(cap, headers={"etag": 1}))

    with pytest.raises(ValueError, match="header keys must be str"):
        archive.record_capture(replace(cap, headers={1: "val"}))

    # Conflicting case-variant headers
    with pytest.raises(ValueError, match="Conflicting case-variant headers"):
        archive.record_capture(replace(cap, headers={"ETag": "v1", "etag": "v2"}))

    # Non-str final_url
    with pytest.raises(ValueError, match="final_url.*must be string"):
        archive.record_capture(replace(cap, final_url=123))

    # Empty final_url
    with pytest.raises(ValueError, match="final_url.*cannot be empty"):
        archive.record_capture(replace(cap, final_url=""))

    # Non-number or invalid duration_ms
    with pytest.raises(ValueError, match="duration_ms"):
        archive.record_capture(replace(cap, duration_ms=True))

    with pytest.raises(ValueError, match="duration_ms"):
        archive.record_capture(replace(cap, duration_ms=float("nan")))

    with pytest.raises(ValueError, match="duration_ms"):
        archive.record_capture(replace(cap, duration_ms=-5.0))

    # Non-bytes body
    with pytest.raises(ValueError, match="body.*must be bytes"):
        archive.record_capture(replace(cap, body="string_not_bytes"))

    # Body exceeding limit
    with pytest.raises(ValueError, match="body.*exceeds stream limit"):
        archive.record_capture(replace(cap, body=b"x" * (2 * 1024 * 1024 + 1)))

    # Invalid timestamp formats
    with pytest.raises(ValueError, match="fetch_started_at"):
        archive.record_capture(replace(cap, fetch_started_at="2026-10-18"))

    with pytest.raises(ValueError, match="fetch_completed_at"):
        archive.record_capture(replace(cap, fetch_completed_at=12345))

    # Invalid collector version
    with pytest.raises(ValueError, match="collector_version"):
        archive.record_capture(replace(cap, collector_version=""))

    # Non-int or bool status_code
    with pytest.raises(ValueError, match="status_code"):
        archive.record_capture(replace(cap, status_code=True))

    # Non-int or bool bytes counters
    with pytest.raises(ValueError, match="bytes_received"):
        archive.record_capture(replace(cap, bytes_received=True))

    # Header order invariance and positive replay
    rec1 = archive.record_capture(replace(cap, headers={"b": "2", "a": "1"}), receipt_id="order-test")
    rec2 = archive.record_capture(replace(cap, headers={"a": "1", "b": "2"}), receipt_id="order-test")
    assert rec1 == rec2
    assert rec1.payload_fingerprint == rec2.payload_fingerprint

    # Foreign source_id/url in probe_single
    with pytest.raises(ValueError, match="Capture mismatch"):
        with SourceProbe() as probe:
            probe.probe_single({"source_id": "bls_cpi_rss", "url": CANONICAL_SOURCES["bls_cpi_rss"]}, capture=cap)


def test_legacy_schema_version_detection(tmp_path: Path):
    """Archive detects legacy schema (v1.0) before running DDL and raises ArchiveIntegrityError without touching files."""
    import sqlite3
    legacy_dir = tmp_path / "legacy_v1_archive"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    db_path = legacy_dir / "archive.db"

    # Create a mock v1.0 SQLite database
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE schema_info (key TEXT PRIMARY KEY, value TEXT);")
    conn.execute("INSERT INTO schema_info (key, value) VALUES ('version', 'pythia-archive-r2/1.0');")
    conn.execute("CREATE TABLE legacy_data (id INT);")
    conn.execute("INSERT INTO legacy_data VALUES (42);")
    conn.commit()
    conn.close()

    stat_before = db_path.stat()

    # Attempting to open EvidenceArchive on legacy_dir must raise ArchiveIntegrityError
    with pytest.raises(ArchiveIntegrityError, match="Incompatible archive schema version 'pythia-archive-r2/1.0'"):
        EvidenceArchive(legacy_dir)

    # Verify database was NOT modified
    stat_after = db_path.stat()
    assert stat_after.st_mtime == stat_before.st_mtime

    # Verify legacy table intact
    conn2 = sqlite3.connect(db_path)
    res = conn2.execute("SELECT id FROM legacy_data;").fetchone()
    assert res[0] == 42
    conn2.close()


# -------------------------------------------------------------------------
# 12. CAS Durability Barrier Tests (C3 Invariants)
# -------------------------------------------------------------------------

def test_cas_durability_barrier_trace_before_db_commit(tmp_path: Path):
    """Trace confirms file, leaf prefix, parent prefix, and blobs directory barriers before DB commit."""
    archive = EvidenceArchive(tmp_path / "barrier_trace")
    cap = make_capture(source_id="nyfed_effr", body=b'{"trace":1}')
    calls = []
    real_fsync = os.fsync

    def trace_fsync(fd):
        target = Path(os.readlink(f"/proc/self/fd/{fd}"))
        calls.append({
            "directory": stat.S_ISDIR(os.fstat(fd).st_mode),
            "path": str(target.relative_to(archive.state_dir)),
        })
        return real_fsync(fd)

    hook_calls = []

    def hook(point):
        if point == "inside_db_tx_before_commit":
            hook_calls.append(len(calls))

    os.fsync = trace_fsync
    try:
        rec = archive.record_capture(cap, receipt_id="trace-001", test_hook=hook)
    finally:
        os.fsync = real_fsync

    blob = archive._blob_path(rec.blob_sha256)
    required = {str(p.relative_to(archive.state_dir)) for p in (blob.parent, blob.parent.parent, archive.blobs_dir)}
    synced_dirs = {x["path"] for x in calls if x["directory"]}

    # All required directory entries must be fsynced
    assert required.issubset(synced_dirs), f"Missing directory barriers: {required - synced_dirs}"
    # File staging must be fsynced
    assert any(not x["directory"] for x in calls), "Staging file was not fsynced"
    # All barriers must occur before inside_db_tx_before_commit
    assert hook_calls and hook_calls[0] >= 4, f"Fsync barriers did not complete before DB tx: {hook_calls}"


def test_cas_durability_barrier_covers_fresh_prefix_parents(tmp_path: Path):
    """Fresh state directory store blob ensures leaf, prefix parent, and root blobs directories are synced."""
    archive = EvidenceArchive(tmp_path / "fresh_parents")
    cap = make_capture(source_id="nyfed_effr", body=b'{"fresh_parents":1}')
    calls = []
    real_fsync = os.fsync

    def trace_fsync(fd):
        target = Path(os.readlink(f"/proc/self/fd/{fd}"))
        calls.append({
            "directory": stat.S_ISDIR(os.fstat(fd).st_mode),
            "path": str(target.relative_to(archive.state_dir)),
        })
        return real_fsync(fd)

    os.fsync = trace_fsync
    try:
        rec = archive.record_capture(cap, receipt_id="fresh-parents-001")
    finally:
        os.fsync = real_fsync

    blob = archive._blob_path(rec.blob_sha256)
    required = {str(p.relative_to(archive.state_dir)) for p in (blob.parent, blob.parent.parent, archive.blobs_dir)}
    synced_dirs = {x["path"] for x in calls if x["directory"]}
    assert not (required - synced_dirs)


def test_cas_durability_barrier_delayed_publisher_second_writer_syncs(tmp_path: Path):
    """When publisher is delayed after link, second writer executes durability barrier before DB commit."""
    archive = EvidenceArchive(tmp_path / "delayed_pub")
    cap = make_capture(source_id="nyfed_effr", body=b'{"delayed_pub":1}')
    published = threading.Event()
    release = threading.Event()
    real_link = os.link
    real_fsync = os.fsync
    events = []
    errors = []

    def delayed_link(src, dst, *args, **kwargs):
        val = real_link(src, dst, *args, **kwargs)
        if threading.current_thread().name == "publisher":
            published.set()
            if not release.wait(timeout=10):
                raise RuntimeError("Release timed out")
        return val

    def race_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            events.append(str(Path(os.readlink(f"/proc/self/fd/{fd}"))))
        return real_fsync(fd)

    def publisher():
        try:
            archive.record_capture(cap, receipt_id="pub-001")
        except Exception as exc:
            errors.append(repr(exc))

    os.link = delayed_link
    os.fsync = race_fsync
    writer = threading.Thread(target=publisher, name="publisher")
    writer.start()
    try:
        assert published.wait(timeout=5), "Publisher failed to reach link"
        second = archive.record_capture(cap, receipt_id="second-001")
        # Second writer must not commit without performing directory fsync
        assert second.seq >= 1
        assert len(events) >= 3, f"Expected at least 3 directory fsyncs from second writer, got {len(events)}"
    finally:
        release.set()
        writer.join(timeout=5)
        os.link = real_link
        os.fsync = real_fsync

    assert not writer.is_alive() and not errors, errors
    assert len(archive.query_receipts()) == 2


def test_cas_durability_barrier_fsync_error_prevents_db_commit(tmp_path: Path):
    """Error during CAS directory fsync prevents database transaction from committing."""
    archive = EvidenceArchive(tmp_path / "fsync_err")
    cap = make_capture(source_id="nyfed_effr", body=b'{"fsync_err":1}')
    real_fsync = os.fsync

    def failing_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(5, "Simulated I/O error during directory fsync")
        return real_fsync(fd)

    os.fsync = failing_fsync
    try:
        with pytest.raises(OSError, match="Simulated I/O error"):
            archive.record_capture(cap, receipt_id="fail-fsync-001")
    finally:
        os.fsync = real_fsync

    # No receipt committed
    assert archive.get_receipt("fail-fsync-001") is None
    assert len(archive.query_receipts()) == 0


def test_cas_durability_barrier_retry_after_halted_publisher(tmp_path: Path):
    """Retry after halted publisher cleanly reuses existing blob, completes barrier, and commits."""
    archive = EvidenceArchive(tmp_path / "retry_pub")
    cap = make_capture(source_id="nyfed_effr", body=b'{"retry_pub":1}')

    # Simulate publisher halted after link
    real_link = os.link

    def halt_after_link(src, dst, *args, **kwargs):
        val = real_link(src, dst, *args, **kwargs)
        raise RuntimeError("Publisher abruptly killed after link")

    os.link = halt_after_link
    try:
        with pytest.raises(RuntimeError, match="Publisher abruptly killed"):
            archive.record_capture(cap, receipt_id="attempt-1")
    finally:
        os.link = real_link

    # Destination file exists on disk (orphaned)
    blob_path = archive._blob_path(hashlib.sha256(cap.body).hexdigest())
    assert blob_path.is_file()
    assert len(archive.query_receipts()) == 0

    # Retry with same capture
    rec = archive.record_capture(cap, receipt_id="attempt-2")
    assert rec.seq == 1
    assert rec.blob_sha256 == hashlib.sha256(cap.body).hexdigest()
    assert len(archive.query_receipts()) == 1
    integrity = archive.verify_integrity()
    assert integrity["healthy"] is True


# -------------------------------------------------------------------------
# 13. Reviewer R2.1 Defect Controls Resolution Suite
# -------------------------------------------------------------------------

def test_reviewer_r21_defects_fixed(tmp_path: Path):
    """Verify that all 5 defects identified in R2.1 review are fixed and forbidden."""
    folder = tmp_path / "r21_fixes"
    folder.mkdir()
    cap = make_capture(
        source_id="nyfed_effr",
        body=b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-16","percentRate":3.88}]}',
        headers={"content-type": "application/json"},
    )
    fixed_diag = {"status": "valid", "validation_level": "content", "reason": "fixture", "checker_version": "review/1"}

    # Defect 1: ambient test routing cannot activate on default worker
    hits = []
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    old = os.environ.get("PYTHIA_TEST_LOOPBACK_MAP")
    os.environ["PYTHIA_TEST_LOOPBACK_MAP"] = json.dumps({"https://markets.newyorkfed.org": f"http://127.0.0.1:{srv.server_address[1]}"})
    try:
        with SourceProbe(timeout=0.10) as probe:
            res = probe.capture_source("nyfed_effr", CANONICAL_SOURCES["nyfed_effr"])
        assert len(hits) == 0, "Default worker routed to ambient loopback"
    finally:
        if old is None: os.environ.pop("PYTHIA_TEST_LOOPBACK_MAP", None)
        else: os.environ["PYTHIA_TEST_LOOPBACK_MAP"] = old
        srv.shutdown(); srv.server_close()

    # Defect 2: fresh prefix directory barriers
    arch2 = EvidenceArchive(folder / "fresh-dirs")
    calls = []
    real_fsync = os.fsync
    def trace_f(fd):
        calls.append({"directory": stat.S_ISDIR(os.fstat(fd).st_mode), "path": str(Path(os.readlink(f"/proc/self/fd/{fd}")).relative_to(arch2.state_dir))})
        return real_fsync(fd)
    os.fsync = trace_f
    try:
        rec2 = arch2.record_capture(cap, receipt_id="fresh")
    finally:
        os.fsync = real_fsync
    b2 = arch2._blob_path(rec2.blob_sha256)
    req2 = {str(p.relative_to(arch2.state_dir)) for p in (b2.parent, b2.parent.parent, arch2.blobs_dir)}
    synced2 = {x["path"] for x in calls if x["directory"]}
    assert not (req2 - synced2), f"Missing barriers: {req2 - synced2}"

    # Defect 3: publisher link delay race
    arch3 = EvidenceArchive(folder / "race")
    pub_done = threading.Event(); rel = threading.Event()
    real_link = os.link; events = []
    def del_link(src, dst, *a, **kw):
        v = real_link(src, dst, *a, **kw)
        if threading.current_thread().name == "pub":
            pub_done.set()
            rel.wait(timeout=10)
        return v
    def rf(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode): events.append(os.readlink(f"/proc/self/fd/{fd}"))
        return real_fsync(fd)
    os.link = del_link; os.fsync = rf
    w = threading.Thread(target=lambda: arch3.record_capture(cap, receipt_id="pub"), name="pub")
    w.start()
    try:
        assert pub_done.wait(timeout=5)
        sec = arch3.record_capture(cap, receipt_id="sec")
        assert sec.seq >= 1
        assert len(events) >= 3, "Second writer committed with 0 directory fsync"
    finally:
        rel.set(); w.join(timeout=5)
        os.link = real_link; os.fsync = real_fsync

    # Defect 4: direct header type collision rejected
    arch4 = EvidenceArchive(folder / "hdr")
    with pytest.raises(ValueError, match="header values must be str"):
        arch4.record_capture(replace(cap, headers={"etag": 1}), receipt_id="hdr", probe_result=fixed_diag)

    # Defect 5: direct final url integer rejected
    arch5 = EvidenceArchive(folder / "url")
    with pytest.raises(ValueError, match="final_url.*must be string"):
        arch5.record_capture(replace(cap, final_url=123), receipt_id="url", probe_result=fixed_diag)
