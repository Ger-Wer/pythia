"""Hermetic Test Suite for Pythia R3.1.9 Bounded Research Acquisition Driver.

Tests all driver invariants strictly offline using scripted local HTTP responses:
- T1: Complete compressed EOF verification (gzip/deflate trailers, 2 MiB limits, unsupported encodings).
- N1: No wire bytes after decoder EOF are dropped: multi-member gzip decodes in full,
  trailing garbage is rejected, the decoded limit spans all members.
- N2: cookie_isolation_on_each_hop is never vacuously true with zero observed hops.
- T2: Atomic publication and fail-closed pre-flight no-clobber protection.
- T3: Truthful audit recording (unknowns vs observed, actual 5 outgoing request headers).
- Process supervisor: hard timeout enforcement and SIGKILL cleanup without orphans.
- URL gate: strict domain, scheme, port, and userinfo validation.

Network guard:
External network access (DNS, connect, connect_ex) is strictly forbidden via socket guard.
Only numeric-loopback (127.0.0.1) for local scripted mock server is permitted.
"""

from __future__ import annotations

import gzip
import hashlib
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List

# Strict network guard: forbid non-loopback network access
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_getaddrinfo = socket.getaddrinfo


def guarded_connect(sock: socket.socket, address: Any) -> Any:
    if isinstance(address, tuple) and len(address) >= 2:
        host, port = address[0], address[1]
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError(f"Network guard violation: attempt to connect to external host {host}:{port}")
    return _original_connect(sock, address)


def guarded_connect_ex(sock: socket.socket, address: Any) -> Any:
    if isinstance(address, tuple) and len(address) >= 2:
        host, port = address[0], address[1]
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError(f"Network guard violation: attempt to connect_ex to external host {host}:{port}")
    return _original_connect_ex(sock, address)


def guarded_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise RuntimeError(f"Network guard violation: attempt to resolve external host {host}")
    return _original_getaddrinfo(host, port, *args, **kwargs)


socket.socket.connect = guarded_connect
socket.socket.connect_ex = guarded_connect_ex
socket.getaddrinfo = guarded_getaddrinfo

# Import driver under test
ACQUISITION_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ACQUISITION_DIR))

import bounded_research_driver as d


class MockScriptedServer:
    """Threaded HTTP server for scripted hermetic responses."""

    def __init__(self) -> None:
        self.server: Optional[http.server.ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.port: int = 0
        self.requests_received: List[Dict[str, Any]] = []
        self.scripted_handlers: Dict[str, Any] = {}

    def start(self) -> None:
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                headers_dict = dict(self.headers)
                outer.requests_received.append({
                    "path": self.path,
                    "headers": headers_dict,
                    "header_names": sorted(list(self.headers.keys())),
                    "cookie_present": "Cookie" in self.headers,
                    "authorization_present": "Authorization" in self.headers,
                    "proxy_authorization_present": "Proxy-Authorization" in self.headers,
                })

                if self.path in outer.scripted_handlers:
                    outer.scripted_handlers[self.path](self)
                    return

                # Default fallback
                self.send_response(404)
                self.end_headers()

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_supervisor_kills_hung_worker() -> None:
    """Supervisor enforces hard process deadline via SIGKILL on hung worker."""
    server = MockScriptedServer()

    def handle_hang(handler: http.server.BaseHTTPRequestHandler) -> None:
        time.sleep(2.0)
        handler.send_response(200)
        handler.end_headers()
        handler.wfile.write(b"should not arrive")

    server.scripted_handlers["/hang"] = handle_hang
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            raw_path = Path(td) / "raw.html"
            result = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/hang",
                attempt_number=1,
                output_raw_path=raw_path,
                allowed_domains={"127.0.0.1"},
                worker_timeout=0.3,
                https_only=False,
                verify_tls=False,
            )

            assert result["killed"] is True
            assert result["transport_outcome"] == "worker_killed_timeout"
            assert result["eof_complete"] is False
            assert result["hops_count"] is None
            assert result["hops"] is None
            assert result["redirect_count"] is None
            assert result["final_url"] is None
            assert not raw_path.exists()
    finally:
        server.stop()


def test_set_cookie_isolation_on_redirect() -> None:
    """Set-Cookie from 302 response is redacted and NOT sent on redirect."""
    server = MockScriptedServer()

    def handle_start(handler: http.server.BaseHTTPRequestHandler) -> None:
        handler.send_response(302)
        handler.send_header("Location", f"http://127.0.0.1:{server.port}/destination")
        handler.send_header("Set-Cookie", "session=supersecret123; Path=/; HttpOnly")
        handler.end_headers()

    def handle_dest(handler: http.server.BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html")
        body = b"<html><body>final destination</body></html>"
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    server.scripted_handlers["/start"] = handle_start
    server.scripted_handlers["/destination"] = handle_dest
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            raw_path = Path(td) / "raw.html"
            result = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/start",
                attempt_number=1,
                output_raw_path=raw_path,
                allowed_domains={"127.0.0.1"},
                worker_timeout=3.0,
                https_only=False,
                verify_tls=False,
            )

            assert result["transport_outcome"] == "success"
            assert result["redirect_count"] == 1
            assert result["hops_count"] == 2
            assert len(result["hops"]) == 2

            # Check that Cookie header was NOT sent on hop 2
            assert result["hops"][0]["cookie_headers_sent"] is False
            assert result["hops"][1]["cookie_headers_sent"] is False
            assert result["hops"][0]["credential_headers_sent"] is False
            assert result["hops"][1]["credential_headers_sent"] is False

            # Check that server observed NO cookie on either request
            assert len(server.requests_received) == 2
            assert server.requests_received[0]["cookie_present"] is False
            assert server.requests_received[1]["cookie_present"] is False

            # Check that Set-Cookie was redacted in audit
            assert result["hops"][0]["response_headers_sanitized"]["Set-Cookie"] == "[REDACTED_IGNORED]"
    finally:
        server.stop()


def test_offsite_redirect_rejected_before_request() -> None:
    """Redirect targeting offsite host is rejected before request is sent."""
    server = MockScriptedServer()

    def handle_offsite(handler: http.server.BaseHTTPRequestHandler) -> None:
        handler.send_response(302)
        handler.send_header("Location", "https://malicious-site.example.com/steal")
        handler.end_headers()

    server.scripted_handlers["/offsite"] = handle_offsite
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            raw_path = Path(td) / "raw.html"
            result = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/offsite",
                attempt_number=1,
                output_raw_path=raw_path,
                allowed_domains={"127.0.0.1"},
                worker_timeout=3.0,
                https_only=False,
                verify_tls=False,
            )

            assert result["transport_outcome"] == "failure"
            assert "TransportValidationError" in result["error_msg"]
            assert len(server.requests_received) == 1
            assert not raw_path.exists()
    finally:
        server.stop()


def test_third_redirect_rejected_before_request() -> None:
    """Third redirect exceeds limit of 2 and is rejected before 4th request."""
    server = MockScriptedServer()

    def r1(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(302)
        h.send_header("Location", f"http://127.0.0.1:{server.port}/r2")
        h.end_headers()

    def r2(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(302)
        h.send_header("Location", f"http://127.0.0.1:{server.port}/r3")
        h.end_headers()

    def r3(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(302)
        h.send_header("Location", f"http://127.0.0.1:{server.port}/r4")
        h.end_headers()

    server.scripted_handlers["/r1"] = r1
    server.scripted_handlers["/r2"] = r2
    server.scripted_handlers["/r3"] = r3
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            raw_path = Path(td) / "raw.html"
            result = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/r1",
                attempt_number=1,
                output_raw_path=raw_path,
                allowed_domains={"127.0.0.1"},
                max_redirects=2,
                worker_timeout=3.0,
                https_only=False,
                verify_tls=False,
            )

            assert result["transport_outcome"] == "failure"
            assert "TransportLimitError" in result["error_msg"]
            assert not raw_path.exists()
            assert len(server.requests_received) == 3  # r1, r2, r3, r4 is rejected before request
    finally:
        server.stop()


def test_gzip_valid_and_missing_trailer() -> None:
    """T1: Valid gzip succeeds with eof_complete=True; missing trailer is rejected."""
    server = MockScriptedServer()
    body = b"<html><body>complete gzip statement control</body></html>"
    gz_full = gzip.compress(body)
    gz_trunc = gz_full[:-8]  # missing 8-byte trailer

    def handle_valid(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        h.send_header("Content-Encoding", "gzip")
        h.send_header("Content-Length", str(len(gz_full)))
        h.end_headers()
        h.wfile.write(gz_full)

    def handle_trunc(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        h.send_header("Content-Encoding", "gzip")
        h.send_header("Content-Length", str(len(gz_trunc)))
        h.end_headers()
        h.wfile.write(gz_trunc)

    server.scripted_handlers["/gzip_valid"] = handle_valid
    server.scripted_handlers["/gzip_trunc"] = handle_trunc
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            # 1. Valid gzip
            raw_valid = Path(td) / "valid.html"
            res_valid = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/gzip_valid",
                attempt_number=1,
                output_raw_path=raw_valid,
                allowed_domains={"127.0.0.1"},
                https_only=False,
                verify_tls=False,
            )
            assert res_valid["transport_outcome"] == "success"
            assert res_valid["eof_complete"] is True
            assert res_valid["bytes_stored"] == len(body)
            assert raw_valid.read_bytes() == body

            # 2. Truncated gzip (missing 8-byte trailer)
            raw_trunc = Path(td) / "trunc.html"
            res_trunc = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/gzip_trunc",
                attempt_number=2,
                output_raw_path=raw_trunc,
                allowed_domains={"127.0.0.1"},
                https_only=False,
                verify_tls=False,
            )
            assert res_trunc["transport_outcome"] == "failure"
            assert res_trunc["eof_complete"] is False
            assert "TransportDecodeError" in res_trunc["error_msg"]
            assert "Incomplete compressed stream" in res_trunc["error_msg"]
            assert not raw_trunc.exists(), "Raw file must NOT be written on incomplete stream!"
    finally:
        server.stop()


def test_deflate_valid_and_missing_trailer() -> None:
    """T1: Valid deflate succeeds; missing 4-byte trailer is rejected."""
    server = MockScriptedServer()
    body = b"<html><body>complete deflate statement control</body></html>"
    def_full = zlib.compress(body)
    def_trunc = def_full[:-4]  # missing 4-byte adler32 trailer

    def handle_valid(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        h.send_header("Content-Encoding", "deflate")
        h.send_header("Content-Length", str(len(def_full)))
        h.end_headers()
        h.wfile.write(def_full)

    def handle_trunc(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        h.send_header("Content-Encoding", "deflate")
        h.send_header("Content-Length", str(len(def_trunc)))
        h.end_headers()
        h.wfile.write(def_trunc)

    server.scripted_handlers["/def_valid"] = handle_valid
    server.scripted_handlers["/def_trunc"] = handle_trunc
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            # 1. Valid deflate
            raw_valid = Path(td) / "def_valid.html"
            res_valid = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/def_valid",
                attempt_number=1,
                output_raw_path=raw_valid,
                allowed_domains={"127.0.0.1"},
                https_only=False,
                verify_tls=False,
            )
            assert res_valid["transport_outcome"] == "success"
            assert res_valid["eof_complete"] is True
            assert res_valid["bytes_stored"] == len(body)
            assert raw_valid.read_bytes() == body

            # 2. Truncated deflate
            raw_trunc = Path(td) / "def_trunc.html"
            res_trunc = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/def_trunc",
                attempt_number=2,
                output_raw_path=raw_trunc,
                allowed_domains={"127.0.0.1"},
                https_only=False,
                verify_tls=False,
            )
            assert res_trunc["transport_outcome"] == "failure"
            assert res_trunc["eof_complete"] is False
            assert "TransportDecodeError" in res_trunc["error_msg"]
            assert not raw_trunc.exists(), "Raw file must NOT be written on incomplete stream!"
    finally:
        server.stop()


def test_unsupported_encoding_rejected() -> None:
    """T1: Unsupported Content-Encoding (e.g. br, zstd) rejected cleanly."""
    server = MockScriptedServer()

    def handle_br(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(200)
        h.send_header("Content-Encoding", "br")
        h.send_header("Content-Type", "text/html")
        h.end_headers()
        h.wfile.write(b"brotli-data")

    server.scripted_handlers["/br"] = handle_br
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            raw_p = Path(td) / "br.html"
            res = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/br",
                attempt_number=1,
                output_raw_path=raw_p,
                allowed_domains={"127.0.0.1"},
                https_only=False,
                verify_tls=False,
            )
            assert res["transport_outcome"] == "failure"
            assert "Unsupported Content-Encoding" in res["error_msg"]
            assert not raw_p.exists()
    finally:
        server.stop()


def test_decoded_body_exact_limit_and_overflow() -> None:
    """T1: Exactly 2 MiB decoded passes; 2 MiB + 1 byte rejected with overflow error."""
    server = MockScriptedServer()
    exact_body = b"A" * 2097152
    over_body = exact_body + b"B"

    gz_exact = gzip.compress(exact_body)
    gz_over = gzip.compress(over_body)

    def handle_exact(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        h.send_header("Content-Encoding", "gzip")
        h.send_header("Content-Length", str(len(gz_exact)))
        h.end_headers()
        h.wfile.write(gz_exact)

    def handle_over(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        h.send_header("Content-Encoding", "gzip")
        h.send_header("Content-Length", str(len(gz_over)))
        h.end_headers()
        h.wfile.write(gz_over)

    server.scripted_handlers["/exact"] = handle_exact
    server.scripted_handlers["/over"] = handle_over
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            # 1. Exact 2 MiB
            raw_exact = Path(td) / "exact.html"
            res_exact = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/exact",
                attempt_number=1,
                output_raw_path=raw_exact,
                allowed_domains={"127.0.0.1"},
                max_bytes=2097152,
                https_only=False,
                verify_tls=False,
            )
            assert res_exact["transport_outcome"] == "success"
            assert res_exact["bytes_stored"] == 2097152
            assert res_exact["eof_complete"] is True
            assert raw_exact.stat().st_size == 2097152

            # 2. Over limit (2 MiB + 1 byte)
            raw_over = Path(td) / "over.html"
            res_over = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/over",
                attempt_number=2,
                output_raw_path=raw_over,
                allowed_domains={"127.0.0.1"},
                max_bytes=2097152,
                https_only=False,
                verify_tls=False,
            )
            assert res_over["transport_outcome"] == "failure"
            assert "BodyLimitExceededError" in res_over["error_msg"]
            assert not raw_over.exists()
    finally:
        server.stop()


def test_default_publication_fails_closed_on_existing() -> None:
    """T2: Default execution with no_clobber=True fails closed if existing output is present."""
    server = MockScriptedServer()

    def handle_var(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        body = b"<html><body>payload 1</body></html>"
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    server.scripted_handlers["/var"] = handle_var
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "output"
            targets = [{
                "fixture_key": "test_var",
                "url": f"http://127.0.0.1:{server.port}/var",
                "parent_feed_reference": {"test": True},
            }]

            # First run: should succeed and create files
            res1 = d.execute_bounded_acquisition(
                targets=targets,
                output_dir=out,
                allowed_domains={"127.0.0.1"},
                https_only=False,
                verify_tls=False,
                no_clobber=True,
            )
            assert res1["all_success"] is True

            # Snapshot all files and hashes
            files_before = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob("*") if p.is_file()}
            assert len(files_before) >= 5

            # Second run with default no_clobber=True MUST raise FileExistsError BEFORE any network requests
            server.requests_received.clear()
            conflict_raised = False
            try:
                d.execute_bounded_acquisition(
                    targets=targets,
                    output_dir=out,
                    allowed_domains={"127.0.0.1"},
                    https_only=False,
                    verify_tls=False,
                    no_clobber=True,
                )
            except FileExistsError:
                conflict_raised = True

            assert conflict_raised, "Expected FileExistsError when destination already exists!"
            assert len(server.requests_received) == 0, "No network requests should be made on pre-flight conflict!"

            # Verify files are 100% unchanged
            files_after = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob("*") if p.is_file()}
            assert files_before == files_after, "Files must remain 100% byte-identical on clobber rejection!"
    finally:
        server.stop()


def test_explicit_no_clobber_metadata_only() -> None:
    """T2: Pre-existing metadata files without raw file cause fail-closed pre-flight rejection."""
    server = MockScriptedServer()

    def handle_any(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(200)
        h.end_headers()
        h.wfile.write(b"ok")

    server.scripted_handlers["/any"] = handle_any
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "meta_out"
            (out / "acquisition").mkdir(parents=True)
            (out / "fixtures").mkdir()

            audit_path = out / "acquisition/acquisition_audit.json"
            manifest_path = out / "fixtures/manifest_synthetic.json"
            audit_path.write_bytes(b"prior audit sentinel")
            manifest_path.write_bytes(b"prior manifest sentinel")

            targets = [{
                "fixture_key": "synthetic",
                "url": f"http://127.0.0.1:{server.port}/any",
                "parent_feed_reference": {"synthetic": True},
            }]

            server.requests_received.clear()
            conflict_caught = False
            try:
                d.execute_bounded_acquisition(
                    targets=targets,
                    output_dir=out,
                    allowed_domains={"127.0.0.1"},
                    https_only=False,
                    verify_tls=False,
                    no_clobber=True,
                )
            except FileExistsError:
                conflict_caught = True

            assert conflict_caught, "Expected pre-flight rejection when metadata exists!"
            assert len(server.requests_received) == 0, "Zero network requests must be made!"
            assert audit_path.read_bytes() == b"prior audit sentinel"
            assert manifest_path.read_bytes() == b"prior manifest sentinel"
    finally:
        server.stop()


def test_killed_audit_truthful_reporting() -> None:
    """T3: Killed attempt records unknown fields as None and discrete_eof_verified=False."""
    server = MockScriptedServer()

    def handle_hang(h: http.server.BaseHTTPRequestHandler) -> None:
        time.sleep(1.0)
        h.send_response(200)
        h.end_headers()
        h.wfile.write(b"too late")

    server.scripted_handlers["/hang"] = handle_hang
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "killed_out"
            targets = [{
                "fixture_key": "hang",
                "url": f"http://127.0.0.1:{server.port}/hang",
                "parent_feed_reference": {"test": True},
            }]

            res = d.execute_bounded_acquisition(
                targets=targets,
                output_dir=out,
                allowed_domains={"127.0.0.1"},
                worker_timeout=0.3,
                supervisor_budget=2.0,
                max_attempts_per_url=1,
                max_total_attempts=1,
                https_only=False,
                verify_tls=False,
                no_clobber=True,
            )

            assert res["all_success"] is False
            assert res["fresh_transport_verdict"] == "PARTIAL"

            # Check audit JSON
            audit_file = Path(res["audit_file"])
            audit_data = json.loads(audit_file.read_text())

            exec_sum = audit_data["execution_summary"]
            assert exec_sum["killed_workers_count"] == 1
            assert exec_sum["successful_targets"] == 0
            assert exec_sum["total_hops_executed"] is None, "total_hops_executed must be None when worker killed!"

            prov = audit_data["proven_conditions"]
            assert prov["discrete_eof_verified"] is False, "discrete_eof_verified must be False on killed attempt!"

            att = audit_data["attempts"][0]
            assert att["killed"] is True
            assert att["hops_count"] is None
            assert att["hops"] is None
            assert att["redirect_count"] is None
            assert att["final_url"] is None
    finally:
        server.stop()


def test_outgoing_headers_actual_recording() -> None:
    """T3: Outgoing header names in audit reflect actual 5 sent headers (Host, User-Agent, Accept, Accept-Encoding, Connection)."""
    server = MockScriptedServer()

    def handle_headers(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        body = b"<html><body>headers test</body></html>"
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    server.scripted_handlers["/hdrs"] = handle_headers
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            raw_p = Path(td) / "raw.html"
            res = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/hdrs",
                attempt_number=1,
                output_raw_path=raw_p,
                allowed_domains={"127.0.0.1"},
                https_only=False,
                verify_tls=False,
            )

            assert res["transport_outcome"] == "success"
            hop = res["hops"][0]

            expected_names = ["Accept", "Accept-Encoding", "Connection", "Host", "User-Agent"]
            assert hop["outgoing_header_names"] == expected_names
            assert hop["cookie_headers_sent"] is False
            assert hop["credential_headers_sent"] is False

            # Check server observed headers
            server_names = server.requests_received[0]["header_names"]
            for name in expected_names:
                assert name in server_names
            assert server.requests_received[0]["cookie_present"] is False
            assert server.requests_received[0]["authorization_present"] is False
    finally:
        server.stop()


def test_url_gate_invalids() -> None:
    """URL gate rejects invalid scheme, non-standard port, userinfo, and offsite host."""
    invalids = [
        ("http://www.federalreserve.gov/", "Forbidden scheme 'http'"),
        ("https://www.federalreserve.gov:444/", "Non-standard port 444 forbidden"),
        ("https://user@www.federalreserve.gov/", "Userinfo in URL is strictly forbidden"),
        ("https://example.invalid/", "Domain 'example.invalid' not in allowed set"),
    ]

    for url, err_substr in invalids:
        rejected = False
        try:
            d.validate_target_url(url, d.DEFAULT_ALLOWED_DOMAINS, https_only=True)
        except d.TransportValidationError as exc:
            rejected = True
            assert err_substr in str(exc)
        assert rejected, f"Expected validation rejection for: {url}"


def _serve_bytes(server: MockScriptedServer, path: str, payload: bytes, encoding: str) -> None:
    def handler(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        h.send_header("Content-Encoding", encoding)
        h.send_header("Content-Length", str(len(payload)))
        h.end_headers()
        h.wfile.write(payload)

    server.scripted_handlers[path] = handler


def test_gzip_multi_member_fully_decoded() -> None:
    """N1: Concatenated gzip members decode as one continuous body (RFC 1952)."""
    server = MockScriptedServer()
    body1 = b"<html><body>first member content</body></html>"
    body2 = b"<html><body>second member, different content</body></html>"
    payload = gzip.compress(body1) + gzip.compress(body2)
    _serve_bytes(server, "/multi", payload, "gzip")
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            raw_p = Path(td) / "raw.html"
            res = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/multi",
                attempt_number=1,
                output_raw_path=raw_p,
                allowed_domains={"127.0.0.1"},
                https_only=False,
                verify_tls=False,
            )
            assert res["transport_outcome"] == "success"
            assert res["eof_complete"] is True
            assert raw_p.read_bytes() == body1 + body2, (
                "Multi-member gzip must publish the FULL concatenated body, not only the first member"
            )
            assert res["bytes_stored"] == len(body1) + len(body2)
    finally:
        server.stop()


def test_gzip_trailing_garbage_rejected() -> None:
    """N1: Bytes after a valid member that are not a valid member reject the capture."""
    server = MockScriptedServer()
    body = b"<html><body>valid member then garbage</body></html>"
    payload = gzip.compress(body) + b"GARBAGE-TAIL-123"
    _serve_bytes(server, "/garbage", payload, "gzip")
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            raw_p = Path(td) / "raw.html"
            res = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/garbage",
                attempt_number=1,
                output_raw_path=raw_p,
                allowed_domains={"127.0.0.1"},
                https_only=False,
                verify_tls=False,
            )
            assert res["transport_outcome"] == "failure"
            assert res["eof_complete"] is False
            assert "TransportDecodeError" in (res["error_msg"] or "")
            assert not raw_p.exists(), "Raw file must NOT be written when trailing bytes are unaccounted!"
    finally:
        server.stop()


def test_gzip_multi_member_combined_limit() -> None:
    """N1: The decoded-size limit applies to the sum of all members, not per member."""
    server = MockScriptedServer()
    body1 = b"a" * 1200
    body2 = b"b" * 1200
    payload = gzip.compress(body1) + gzip.compress(body2)
    _serve_bytes(server, "/multi_big", payload, "gzip")
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            raw_p = Path(td) / "raw.html"
            res = d.run_single_attempt_supervisor(
                target_url=f"http://127.0.0.1:{server.port}/multi_big",
                attempt_number=1,
                output_raw_path=raw_p,
                allowed_domains={"127.0.0.1"},
                max_bytes=2000,  # each member alone (1200) fits; combined (2400) must not
                https_only=False,
                verify_tls=False,
            )
            assert res["transport_outcome"] == "failure"
            assert res["eof_complete"] is False
            assert "BodyLimitExceededError" in (res["error_msg"] or "")
            assert not raw_p.exists()
    finally:
        server.stop()


def test_kill_only_audit_no_vacuous_proofs() -> None:
    """N2: An acquisition where every worker is killed proves nothing about cookies."""
    server = MockScriptedServer()

    def handle_hang(handler: http.server.BaseHTTPRequestHandler) -> None:
        time.sleep(2.0)
        handler.send_response(200)
        handler.end_headers()

    server.scripted_handlers["/hang"] = handle_hang
    server.start()

    try:
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "rev"
            targets = [
                {"fixture_key": "h1", "url": f"http://127.0.0.1:{server.port}/hang",
                 "parent_feed_reference": {"dummy": True}},
                {"fixture_key": "h2", "url": f"http://127.0.0.1:{server.port}/hang",
                 "parent_feed_reference": {"dummy": True}},
            ]
            d.execute_bounded_acquisition(
                targets=targets,
                output_dir=out_dir,
                allowed_domains={"127.0.0.1"},
                worker_timeout=0.3,
                supervisor_budget=10.0,
                max_attempts_per_url=1,
                max_total_attempts=2,
                https_only=False,
                verify_tls=False,
            )
            audit = json.loads((out_dir / "acquisition" / "acquisition_audit.json").read_text())
            assert audit["execution_summary"]["killed_workers_count"] == 2
            assert audit["execution_summary"]["total_hops_executed"] is None
            assert audit["proven_conditions"]["discrete_eof_verified"] is False
            assert audit["proven_conditions"]["cookie_isolation_on_each_hop"] is False, (
                "Zero observed hops must never be recorded as proven cookie isolation"
            )
            assert audit["fresh_transport_verdict"] == "PARTIAL"
    finally:
        server.stop()
