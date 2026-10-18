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
import tempfile
import threading
import time
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
    assert "http_status" in str(exc_info2.value)


def test_concurrent_writers_safety(archive: EvidenceArchive):
    """Concurrent writers recording identical and distinct receipts do not corrupt CAS or SQLite."""
    shared_body = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-16","percentRate":3.88}]}'

    def worker_task(idx: int) -> ArchiveReceipt:
        # Some workers use same receipt_id, others use distinct
        rid = f"concurrent-worker-{idx % 4}"
        cap = make_capture(source_id="nyfed_effr", body=shared_body)
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
    "content_type": "application/json",
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
    "content_type": "application/json",
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
