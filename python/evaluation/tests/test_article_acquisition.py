"""Tests for Pythia R3.2 article acquisition and archiving.

Hermetic: all network traffic goes to a scripted HTTP server on 127.0.0.1.
Covers the R3.2 acceptance criteria:
- the accepted canonical archive gate is unchanged by default;
- the article registry gate (allowlist membership + exact URL);
- revision lifecycle A -> B -> A with idempotent replay;
- bounded capture disciplines (multi-member gzip, missing trailer, kill);
- end-to-end: archived real FOMC statement -> extraction -> valid bundle.
"""

from __future__ import annotations

import gzip
import http.server
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict

import pytest

from python.evaluation.article_acquisition import (
    ARTICLE_ACQUISITION_VERSION,
    DEFAULT_ARTICLE_SOURCES,
    archive_article,
    capture_article,
    extract_archived_article,
)
from python.evaluation.evidence_archive import EvidenceArchive
from python.evaluation.evidence_reader import ReadOnlyArchiveReader
from python.evaluation.source_probe import CaptureResult, utc_now_iso

REPO_ROOT = Path(__file__).resolve().parents[3]
R9_FIXTURE = (
    REPO_ROOT
    / "docs/research/2026-10-22/r3/increment1/revision9/fixtures/raw_monetary20260916a.html"
)
R9_BUNDLE = (
    REPO_ROOT
    / "docs/research/2026-10-22/r3/increment1/revision9/bundles/statement_bundle_a5e650b7191fae4cd2bef0ffc62a15b2c9b960248dc81e1873e063bcb9c0d3cf.json"
)

TEST_REGISTRY = {"test_article": "http://127.0.0.1/__PORT__/article"}


def make_capture(source_id: str, url: str, body: bytes, **over: Any) -> CaptureResult:
    fields: Dict[str, Any] = {
        "source_id": source_id,
        "initial_url": url,
        "final_url": url,
        "body": body,
        "status_code": 200,
        "headers": {"Content-Type": "text/html"},
        "fetch_started_at": utc_now_iso(),
        "fetch_completed_at": utc_now_iso(),
        "duration_ms": 12.5,
        "bytes_received": len(body),
        "bytes_stored": len(body),
        "complete": True,
        "limit_exceeded": False,
        "error_msg": None,
        "collector_version": ARTICLE_ACQUISITION_VERSION,
    }
    fields.update(over)
    return CaptureResult(**fields)


TRANSPORT_OK = {
    "status": "ok",
    "validation_level": "transport",
    "reason": "test transport",
    "checker_version": "test",
}


class ScriptedServer:
    def __init__(self) -> None:
        self.handlers: Dict[str, Callable[[http.server.BaseHTTPRequestHandler], None]] = {}
        self.server: http.server.ThreadingHTTPServer | None = None
        self.port = 0

    def start(self) -> None:
        handlers = self.handlers

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                fn = handlers.get(self.path)
                if fn is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                fn(self)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def registry(self) -> Dict[str, str]:
        return {"test_article": f"http://127.0.0.1:{self.port}/article"}


def serve_bytes(handler: http.server.BaseHTTPRequestHandler, payload: bytes, encoding: str | None) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html")
    if encoding:
        handler.send_header("Content-Encoding", encoding)
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


# ---------------------------------------------------------------------------
# Registry gates
# ---------------------------------------------------------------------------

def test_default_archive_rejects_article_source(tmp_path: Path) -> None:
    """The accepted canonical gate is unchanged: article ids are refused."""
    archive = EvidenceArchive(tmp_path / "arch")
    sid = "fed_article_monetary20260916a"
    capture = make_capture(sid, DEFAULT_ARTICLE_SOURCES[sid], b"<html/>")
    with pytest.raises(ValueError):
        archive.record_capture(capture=capture, probe_result=TRANSPORT_OK)


def test_article_registry_gate(tmp_path: Path) -> None:
    archive = EvidenceArchive(tmp_path / "arch", source_registry=DEFAULT_ARTICLE_SOURCES)
    sid = "fed_article_monetary20260916a"
    ok = make_capture(sid, DEFAULT_ARTICLE_SOURCES[sid], b"<html>v1</html>")
    receipt = archive.record_capture(capture=ok, probe_result=TRANSPORT_OK)
    assert receipt.revision_id is not None

    wrong_url = make_capture(sid, "https://www.federalreserve.gov/other.htm", b"<html/>")
    with pytest.raises(ValueError):
        archive.record_capture(capture=wrong_url, probe_result=TRANSPORT_OK)

    unknown = make_capture("fed_article_unknown", DEFAULT_ARTICLE_SOURCES[sid], b"<html/>")
    with pytest.raises(ValueError):
        archive.record_capture(capture=unknown, probe_result=TRANSPORT_OK)


def test_revision_lifecycle_aba(tmp_path: Path) -> None:
    """A -> B -> A yields three chained revisions; replaying the current body
    is idempotent (no fourth revision)."""
    archive = EvidenceArchive(tmp_path / "arch", source_registry=DEFAULT_ARTICLE_SOURCES)
    sid = "fed_article_monetary20260916a"
    url = DEFAULT_ARTICLE_SOURCES[sid]

    r1 = archive.record_capture(capture=make_capture(sid, url, b"<html>A</html>"), probe_result=TRANSPORT_OK)
    r2 = archive.record_capture(capture=make_capture(sid, url, b"<html>B</html>"), probe_result=TRANSPORT_OK)
    r3 = archive.record_capture(capture=make_capture(sid, url, b"<html>A</html>"), probe_result=TRANSPORT_OK)
    assert r1.revision_id and r2.revision_id and r3.revision_id
    assert len({r1.revision_id, r2.revision_id, r3.revision_id}) == 3

    reader = ReadOnlyArchiveReader(tmp_path / "arch")
    revs = {}
    for r in (r1, r2, r3):
        vc = reader.read_receipts([r.receipt_id], r.seq)[0]
        revs[r.receipt_id] = vc.revision
    assert revs[r2.receipt_id].prev_revision_id == r1.revision_id
    assert revs[r3.receipt_id].prev_revision_id == r2.revision_id

    r4 = archive.record_capture(capture=make_capture(sid, url, b"<html>A</html>"), probe_result=TRANSPORT_OK)
    assert r4.revision_id == r3.revision_id, "Unchanged body must replay idempotently"


# ---------------------------------------------------------------------------
# Bounded capture
# ---------------------------------------------------------------------------

def test_capture_multi_member_and_archive(tmp_path: Path) -> None:
    server = ScriptedServer()
    body1 = b"<html><body>member one</body></html>"
    body2 = b"<html><body>member two, different</body></html>"
    payload = gzip.compress(body1) + gzip.compress(body2)
    server.handlers["/article"] = lambda h: serve_bytes(h, payload, "gzip")
    server.start()
    try:
        registry = server.registry()
        capture, audit = capture_article("test_article", registry, https_only=False, verify_tls=False)
        assert capture.complete is True
        assert capture.body == body1 + body2, "Multi-member gzip must be archived in full"
        result = archive_article(tmp_path / "arch", "test_article", registry,
                                 https_only=False, verify_tls=False)
        assert result["complete"] is True
        assert result["revision_id"] is not None
        reader = ReadOnlyArchiveReader(tmp_path / "arch")
        vc = reader.read_receipts([result["receipt_id"]], result["seq"])[0]
        assert vc.blob_bytes == body1 + body2
    finally:
        server.stop()


def test_capture_missing_trailer_failure_receipt(tmp_path: Path) -> None:
    server = ScriptedServer()
    payload = gzip.compress(b"<html><body>truncated</body></html>")[:-8]
    server.handlers["/article"] = lambda h: serve_bytes(h, payload, "gzip")
    server.start()
    try:
        result = archive_article(tmp_path / "arch", "test_article", server.registry(),
                                 https_only=False, verify_tls=False)
        assert result["complete"] is False
        assert result["revision_id"] is None
        reader = ReadOnlyArchiveReader(tmp_path / "arch")
        vc = reader.read_receipts([result["receipt_id"]], result["seq"])[0]
        assert vc.receipt.complete is False
        assert vc.blob_bytes is None
    finally:
        server.stop()


def test_capture_killed_worker(tmp_path: Path) -> None:
    server = ScriptedServer()

    def hang(h: http.server.BaseHTTPRequestHandler) -> None:
        time.sleep(3.0)
        serve_bytes(h, b"<html>late</html>", None)

    server.handlers["/article"] = hang
    server.start()
    try:
        capture, audit = capture_article("test_article", server.registry(),
                                         https_only=False, verify_tls=False, worker_timeout=0.5)
        assert capture.complete is False
        assert audit["killed"] is True
        assert audit["outcome"] == "worker_killed_timeout"
        assert capture.error_msg and "killed" in capture.error_msg
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Extraction from the archive (end-to-end with the real R3.1 fixture)
# ---------------------------------------------------------------------------

def test_extract_archived_statement_end_to_end(tmp_path: Path) -> None:
    body = R9_FIXTURE.read_bytes()
    archive = EvidenceArchive(tmp_path / "arch", source_registry=DEFAULT_ARTICLE_SOURCES)
    sid = "fed_article_monetary20260916a"
    receipt = archive.record_capture(
        capture=make_capture(sid, DEFAULT_ARTICLE_SOURCES[sid], body),
        probe_result=TRANSPORT_OK,
    )
    # The full lineage reference accepted in R3.1 (the extractor enforces the
    # complete F1 contract; a partial reference is correctly rejected).
    parent_ref = {
        "archive_name": "live-r2-revision3",
        "receipt_id": "39bf5ee5-5918-489f-b9e1-dfcfcfbce96b",
        "revision_id": "26b40558-1014-47f2-9b10-d7b8007a07d0",
        "seq": 2,
        "feed_fact_id": "fact-b7cdc057cc6ec28d",
        "feed_blob_sha256": "69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1",
        "feed_item_locator": {"type": "xml_xpath", "path": "/rss/channel/item[3]"},
        "feed_published_at": "2026-09-16T18:00:00+00:00",
        "feed_raw_pub_date": "Wed, 16 Sep 2026 18:00:00 GMT",
        "parent_bundle_digest": "cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde",
        "parent_bundle_as_of_seq": 8,
    }
    result = extract_archived_article(
        tmp_path / "arch",
        receipt.receipt_id,
        receipt.seq,
        tmp_path / "bundles",
        archive_name="pythia-articles-r32",
        parent_feed_reference=parent_ref,
        capture_audit={"redirect_count": 0},
    )
    bundle = json.loads((tmp_path / "bundles" / result["bundle_file"]).read_text())
    doc = bundle["statement_bundle"]["document"]
    r9 = json.loads(R9_BUNDLE.read_text())
    q_new = [p["proof"]["quote_sha256"] for p in doc["paragraphs"]]
    q_r9 = [p["proof"]["quote_sha256"] for p in r9["statement_bundle"]["document"]["paragraphs"]]
    assert q_new == q_r9, "Archived extraction must reproduce the accepted statement content"
    assert bundle["statement_bundle"]["manifest"]["archive_receipt_id"] == receipt.receipt_id
    assert bundle["statement_bundle"]["manifest"]["archive_revision_id"] == receipt.revision_id
    # All proofs verify against the archived blob
    raw = body
    proofs = [doc["container_proof"], doc["date_proof"], doc["title_proof"], doc["release_time_proof"]]
    proofs += [p["proof"] for p in doc["paragraphs"]]
    import hashlib

    for pr in proofs:
        seg = raw[pr["byte_start"]:pr["byte_end"]]
        assert hashlib.sha256(seg).hexdigest() == pr["quote_sha256"]
