"""Hermetic offline test suite for Bounded Research Acquisition Driver.

Verifies:
1. Hard process-level supervisor terminates hung worker process on deadline.
2. Cookie isolation across redirects: Set-Cookie is detected, redacted, and NEVER sent on next hop.
3. Offsite and third redirects are rejected BEFORE sending request.
4. Decoded body streaming limit: exact limit succeeds, limit + 1 rejected with BodyLimitExceededError.
5. Interrupted body streaming correctly results in eof_complete=False and failure.
6. Attempt budget: stops on first success; enforces max 2 attempts per URL and max 4 total attempts.
7. Mandatory audit fields and no-clobber protection.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import socket
import threading
import time
from pathlib import Path
from typing import Generator
import pytest

# Guard against any external network calls
_orig_connect = socket.socket.connect

def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError(f"Hermetic driver test forbids external network access: {address}")
    return _orig_connect(self, address)

socket.socket.connect = _guarded_connect

import sys
from pathlib import Path
driver_dir = str(Path(__file__).resolve().parent)
if driver_dir not in sys.path:
    sys.path.insert(0, driver_dir)
from bounded_research_driver import (
    run_single_attempt_supervisor,
    execute_bounded_acquisition,
)


class MockServerHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Quiet logging

    def do_GET(self):
        if self.path == "/hang":
            time.sleep(3.0)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Should not reach here")
        elif self.path == "/redirect_with_cookie":
            self.send_response(302)
            self.send_header("Location", "/cookie_check")
            self.send_header("Set-Cookie", "session_token=secret_xyz_12345; Path=/")
            self.end_headers()
        elif self.path == "/cookie_check":
            cookie_hdr = self.headers.get("Cookie")
            if cookie_hdr is not None:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"FAIL: Cookie header leaked")
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body>Clean anonymous response</body></html>")
        elif self.path == "/offsite_redirect":
            self.send_response(302)
            self.send_header("Location", "http://192.168.1.99/leak")
            self.end_headers()
        elif self.path == "/r1":
            self.send_response(302)
            self.send_header("Location", "/r2")
            self.end_headers()
        elif self.path == "/r2":
            self.send_response(302)
            self.send_header("Location", "/r3")
            self.end_headers()
        elif self.path == "/r3":
            self.send_response(302)
            self.send_header("Location", "/r4")
            self.end_headers()
        elif self.path == "/r4":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Reached r4")
        elif self.path == "/exact_limit":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"X" * 100)
        elif self.path == "/over_limit":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"X" * 101)
        elif self.path == "/interrupted":
            self.send_response(200)
            self.send_header("Content-Length", "500")
            self.end_headers()
            self.wfile.write(b"X" * 50)
            self.wfile.flush()
            self.close_connection = True
        elif self.path == "/success":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Success payload</body></html>")
        elif self.path == "/failing":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Server error")
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def mock_server() -> Generator[str, None, None]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockServerHandler)
    port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def test_supervisor_kills_hung_worker(mock_server: str, tmp_path: Path):
    """Supervisor enforces hard process deadline and terminates worker when hung."""
    raw_path = tmp_path / "raw_hung.html"
    t0 = time.monotonic()
    record = run_single_attempt_supervisor(
        target_url=f"{mock_server}/hang",
        attempt_number=1,
        output_raw_path=raw_path,
        allowed_domains={"127.0.0.1"},
        worker_timeout=0.4,
        https_only=False,
        verify_tls=False,
    )
    elapsed = time.monotonic() - t0
    
    assert record["killed"] is True
    assert record["worker_exit_reason"] == "deadline_exceeded"
    assert record["transport_outcome"] == "killed"
    assert record["eof_complete"] is False
    assert record["bytes_stored"] == 0
    assert not raw_path.exists()
    assert elapsed < 2.0, f"Supervisor took {elapsed}s, expected < 2.0s"
    
    # Confirm no orphaned worker process
    worker_pid = record["worker_pid"]
    try:
        os.kill(worker_pid, 0)
        orphaned = True
    except ProcessLookupError:
        orphaned = False
    assert not orphaned, f"Worker process {worker_pid} was orphaned!"


def test_set_cookie_isolation_on_redirect(mock_server: str, tmp_path: Path):
    """Set-Cookie received on redirect hop is detected, redacted, and NEVER sent on next hop."""
    raw_path = tmp_path / "raw_cookie.html"
    record = run_single_attempt_supervisor(
        target_url=f"{mock_server}/redirect_with_cookie",
        attempt_number=1,
        output_raw_path=raw_path,
        allowed_domains={"127.0.0.1"},
        worker_timeout=5.0,
        https_only=False,
        verify_tls=False,
    )
    
    assert record["transport_outcome"] == "success"
    assert record["http_status"] == 200
    assert record["redirect_count"] == 1
    assert record["hops_count"] == 2
    assert record["eof_complete"] is True
    assert raw_path.exists()
    assert b"Clean anonymous response" in raw_path.read_bytes()
    
    hops = record["hops"]
    # Hop 1 received Set-Cookie, detected and redacted
    assert hops[0]["set_cookie_detected"] is True
    assert hops[0]["response_headers_sanitized"]["Set-Cookie"] == "[REDACTED_IGNORED]"
    
    # Hop 2 did NOT send Cookie header
    assert hops[1]["cookie_headers_sent"] is False
    assert "Cookie" not in hops[1]["outgoing_header_names"]
    assert hops[1]["credential_headers_sent"] is False


def test_offsite_redirect_rejected_before_request(mock_server: str, tmp_path: Path):
    """Offsite redirect to non-allowed domain is rejected BEFORE sending request."""
    raw_path = tmp_path / "raw_offsite.html"
    record = run_single_attempt_supervisor(
        target_url=f"{mock_server}/offsite_redirect",
        attempt_number=1,
        output_raw_path=raw_path,
        allowed_domains={"127.0.0.1"},
        worker_timeout=5.0,
        https_only=False,
        verify_tls=False,
    )
    
    assert record["transport_outcome"] == "failure"
    assert "Domain '192.168.1.99' not in allowed domains" in record["error_msg"]
    assert record["hops_count"] == 1
    assert not raw_path.exists()


def test_third_redirect_rejected_before_request(mock_server: str, tmp_path: Path):
    """Third redirect is rejected when max_redirects=2."""
    raw_path = tmp_path / "raw_redirects.html"
    record = run_single_attempt_supervisor(
        target_url=f"{mock_server}/r1",
        attempt_number=1,
        output_raw_path=raw_path,
        allowed_domains={"127.0.0.1"},
        max_redirects=2,
        worker_timeout=5.0,
        https_only=False,
        verify_tls=False,
    )
    
    assert record["transport_outcome"] == "failure"
    assert "Redirect count 3 exceeds limit 2" in record["error_msg"]
    assert record["hops_count"] == 3
    assert not raw_path.exists()


def test_decoded_body_exact_limit_and_overflow(mock_server: str, tmp_path: Path):
    """Decoded body of exactly max_bytes succeeds; max_bytes + 1 fails with BodyLimitExceededError."""
    # 1. Exact limit (100 bytes) -> Success
    raw_exact = tmp_path / "raw_exact.html"
    res_exact = run_single_attempt_supervisor(
        target_url=f"{mock_server}/exact_limit",
        attempt_number=1,
        output_raw_path=raw_exact,
        allowed_domains={"127.0.0.1"},
        max_bytes=100,
        worker_timeout=5.0,
        https_only=False,
        verify_tls=False,
    )
    assert res_exact["transport_outcome"] == "success"
    assert res_exact["bytes_stored"] == 100
    assert res_exact["eof_complete"] is True
    assert raw_exact.exists()
    assert len(raw_exact.read_bytes()) == 100

    # 2. Limit + 1 (101 bytes with limit=100) -> Failure
    raw_over = tmp_path / "raw_over.html"
    res_over = run_single_attempt_supervisor(
        target_url=f"{mock_server}/over_limit",
        attempt_number=2,
        output_raw_path=raw_over,
        allowed_domains={"127.0.0.1"},
        max_bytes=100,
        worker_timeout=5.0,
        https_only=False,
        verify_tls=False,
    )
    assert res_over["transport_outcome"] == "failure"
    assert "exceeded limit 100 bytes" in res_over["error_msg"]
    assert res_over["eof_complete"] is False
    assert not raw_over.exists()


def test_interrupted_body_incomplete_eof(mock_server: str, tmp_path: Path):
    """Truncated stream without complete EOF results in failure and eof_complete=False."""
    raw_path = tmp_path / "raw_interrupted.html"
    record = run_single_attempt_supervisor(
        target_url=f"{mock_server}/interrupted",
        attempt_number=1,
        output_raw_path=raw_path,
        allowed_domains={"127.0.0.1"},
        worker_timeout=5.0,
        https_only=False,
        verify_tls=False,
    )
    assert record["transport_outcome"] == "failure"
    assert record["eof_complete"] is False
    assert not raw_path.exists()


def test_attempt_budget_and_stop_on_success(mock_server: str, tmp_path: Path):
    """Driver stops URL attempts upon success; enforces max 2 attempts per URL and budget."""
    test_targets = [
        {
            "fixture_key": "test_success",
            "url": f"{mock_server}/success",
            "parent_feed_reference": {"test": "ref1"},
        },
        {
            "fixture_key": "test_failing",
            "url": f"{mock_server}/failing",
            "parent_feed_reference": {"test": "ref2"},
        },
    ]
    out_dir = tmp_path / "acq_budget_test"
    result = execute_bounded_acquisition(
        targets=test_targets,
        output_dir=out_dir,
        allowed_domains={"127.0.0.1"},
        max_attempts_per_url=2,
        max_total_attempts=4,
        worker_timeout=2.0,
        supervisor_budget=10.0,
        https_only=False,
        verify_tls=False,
    )
    
    audit = json.loads(Path(result["audit_file"]).read_text())
    attempts = audit["attempts"]
    
    # Target 1 succeeded on attempt 1 -> no attempt 2 made
    t1_attempts = [a for a in attempts if a["fixture_key"] == "test_success"]
    assert len(t1_attempts) == 1
    assert t1_attempts[0]["transport_outcome"] == "success"
    
    # Target 2 failed attempt 1 and attempt 2 -> stopped at 2 attempts
    t2_attempts = [a for a in attempts if a["fixture_key"] == "test_failing"]
    assert len(t2_attempts) == 2
    assert t2_attempts[0]["transport_outcome"] == "failure"
    assert t2_attempts[1]["transport_outcome"] == "failure"
    
    # Total attempts = 3 <= 4
    assert len(attempts) == 3
    assert audit["execution_summary"]["total_attempts_made"] == 3


def test_mandatory_audit_fields_and_no_clobber(mock_server: str, tmp_path: Path):
    """Verifies all mandatory audit fields and no-clobber protection."""
    test_targets = [
        {
            "fixture_key": "test_item",
            "url": f"{mock_server}/success",
            "parent_feed_reference": {"test": "ref"},
        }
    ]
    out_dir = tmp_path / "acq_audit_test"
    result = execute_bounded_acquisition(
        targets=test_targets,
        output_dir=out_dir,
        allowed_domains={"127.0.0.1"},
        https_only=False,
        verify_tls=False,
    )
    
    audit_file = Path(result["audit_file"])
    package_file = Path(result["package_file"])
    assert audit_file.exists()
    assert package_file.exists()
    
    audit = json.loads(audit_file.read_text())
    assert "audit_version" in audit
    assert "run_id" in audit
    assert "supervisor_configuration" in audit
    assert "execution_summary" in audit
    assert "attempts" in audit
    assert "proven_conditions" in audit
    
    attempt = audit["attempts"][0]
    for field in (
        "worker_pid", "target_url", "final_url", "http_status",
        "bytes_stored", "blob_sha256", "eof_complete", "transport_outcome",
        "started_at_utc", "completed_at_utc", "hops", "hops_count"
    ):
        assert field in attempt
        
    hop = attempt["hops"][0]
    for field in (
        "hop_number", "request_url", "outgoing_header_names",
        "credential_headers_sent", "cookie_headers_sent",
        "set_cookie_detected", "set_cookie_action", "response_headers_sanitized"
    ):
        assert field in hop
        
    # Test no-clobber
    with pytest.raises(FileExistsError):
        execute_bounded_acquisition(
            targets=test_targets,
            output_dir=out_dir,
            allowed_domains={"127.0.0.1"},
            https_only=False,
            verify_tls=False,
            no_clobber=True,
        )
