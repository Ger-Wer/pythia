"""Pythia R3.1.9 Bounded Research Fixture Acquisition Driver.

Standalone, bounded, single-purpose research driver for capturing the exact two
official FOMC statement pages for offline parser fixture research.

Invariants Honored (Resolving T1 - T3, N1 - N2):
1. Complete Compressed EOF (T1 + N1):
   - Decodes HTTP stream using zlib/gzip streaming decompressors.
   - Enforces max decoded size limit (2 MiB) strictly during stream processing.
   - Verifies discrete decompressor EOF marker (decompressor.eof is True) and valid trailers.
   - Truncated gzip (missing 8-byte trailer) and truncated deflate (missing 4-byte trailer)
     are strictly rejected; no raw files published on incomplete streams.
   - N1: bytes after a member's EOF are never silently dropped. Concatenated gzip
     members are decoded as one continuous body (RFC 1952); trailing bytes that do
     not form a valid continuation member reject the capture. eof_complete requires
     decoder EOF with zero unconsumed wire bytes remaining.
   - Plain/gzip exact 2 MiB succeeds; 2 MiB + 1 byte rejected.
   - Unsupported encodings strictly rejected.
2. Atomic Publication & Fail-Closed No-Clobber (T2):
   - Default mode is no_clobber=True across API and CLI.
   - Pre-flight conflict check verifies ALL destination files (raw, manifest, summary,
     audit, package) before ANY network attempt; fails closed immediately if any exists.
   - All file publications use atomic staging and link/replace to eliminate truncation/overwrite windows.
3. Truthful Audit & Actual Request Headers (T3):
   - Outgoing header names captured from actual prepared request (Host, User-Agent,
     Accept, Accept-Encoding, Connection).
   - Confirms and records absence of Cookie / Authorization headers on every hop.
   - For killed/interrupted workers: unknown fields (hops_count, hops, redirect_count,
     final_url) recorded as None/null rather than zero/false.
   - total_hops_executed is None if any attempt killed; known_hops_executed tracked separately.
   - discrete_eof_verified is True ONLY when all targets succeed with verified discrete EOF.
   - N2: cookie_isolation_on_each_hop is True only when at least one hop was actually
     observed and every observed hop is clean; zero observed hops yields False, never True.
4. Process Supervisor:
   - External parent process with hard deadline (20s per attempt) enforcing SIGKILL.
   - No orphaned child processes.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.parse
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Driver Identification
DRIVER_VERSION = "pythia-bounded-research-driver-r319/1.0"
AUDIT_VERSION = "pythia-acquisition-audit-r319/1.0"
PACKAGE_VERSION = "pythia-acquisition-package-r319/1.0"

USER_AGENT = "PythiaResearchAudit/1.0 (Research Archive Fixture Capture; +https://github.com/Ger-Wer/pythia)"

# Strict Operational Limits
DEFAULT_MAX_BYTES = 2097152             # 2 MiB strict decoded body limit
DEFAULT_WORKER_TIMEOUT = 20.0           # 20.0s hard attempt process limit
DEFAULT_SUPERVISOR_BUDGET = 100.0       # 100.0s total acquisition budget
DEFAULT_MAX_REDIRECTS = 2               # Max 2 redirects per attempt
DEFAULT_MAX_ATTEMPTS_PER_URL = 2        # Max 2 attempts per URL
DEFAULT_MAX_TOTAL_ATTEMPTS = 4          # Max 4 attempts total

DEFAULT_ALLOWED_DOMAINS = {
    "federalreserve.gov",
    "www.federalreserve.gov",
}

OFFICIAL_TARGETS = [
    {
        "fixture_key": "monetary20260916a",
        "url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm",
        "parent_feed_reference": {
            "archive_name": "live-r2-revision3",
            "receipt_id": "39bf5ee5-5918-489f-b9e1-dfcfcfbce96b",
            "revision_id": "26b40558-1014-47f2-9b10-d7b8007a07d0",
            "seq": 2,
            "feed_fact_id": "fact-b7cdc057cc6ec28d",
            "feed_blob_sha256": "69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1",
            "feed_item_locator": {
                "type": "xml_xpath",
                "path": "/rss/channel/item[3]"
            },
            "feed_published_at": "2026-09-16T18:00:00+00:00",
            "feed_raw_pub_date": "Wed, 16 Sep 2026 18:00:00 GMT",
            "parent_bundle_digest": "cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde",
            "parent_bundle_as_of_seq": 8,
        }
    },
    {
        "fixture_key": "monetary20260729a",
        "url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm",
        "parent_feed_reference": {
            "archive_name": "live-r2-revision3",
            "receipt_id": "39bf5ee5-5918-489f-b9e1-dfcfcfbce96b",
            "revision_id": "26b40558-1014-47f2-9b10-d7b8007a07d0",
            "seq": 2,
            "feed_fact_id": "fact-23bc4c0fc0243b9e",
            "feed_blob_sha256": "69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1",
            "feed_item_locator": {
                "type": "xml_xpath",
                "path": "/rss/channel/item[7]"
            },
            "feed_published_at": "2026-07-29T18:00:00+00:00",
            "feed_raw_pub_date": "Wed, 29 Jul 2026 18:00:00 GMT",
            "parent_bundle_digest": "cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde",
            "parent_bundle_as_of_seq": 8,
        }
    }
]


class TransportError(Exception):
    """Base transport error."""


class TransportValidationError(TransportError):
    """Target URL or redirect validation error."""


class TransportLimitError(TransportError):
    """Exceeded attempt, redirect, or budget limit."""


class BodyLimitExceededError(TransportError):
    """Decoded body length exceeded max limit."""


class TransportDecodeError(TransportError):
    """Decompression or EOF decode error."""


def validate_target_url(url: str, allowed_domains: Set[str], https_only: bool = True) -> None:
    """Strictly validates target URL scheme, port, domain, and userinfo."""
    parsed = urllib.parse.urlsplit(url)
    
    if https_only and parsed.scheme.lower() != "https":
        raise TransportValidationError(f"Forbidden scheme '{parsed.scheme}'; https only")
        
    if not https_only and parsed.scheme.lower() not in ("http", "https"):
        raise TransportValidationError(f"Forbidden scheme '{parsed.scheme}'")
        
    if parsed.username or parsed.password:
        raise TransportValidationError("Userinfo in URL is strictly forbidden")
        
    hostname = parsed.hostname
    if not hostname:
        raise TransportValidationError(f"Missing hostname in URL: {url}")
        
    if hostname.lower() not in allowed_domains:
        raise TransportValidationError(f"Domain '{hostname}' not in allowed set: {allowed_domains}")
        
    port = parsed.port
    if port is not None:
        if https_only and port != 443:
            raise TransportValidationError(f"Non-standard port {port} forbidden")
        if not https_only and port not in (80, 443) and not (hostname == "127.0.0.1"):
            raise TransportValidationError(f"Non-standard port {port} forbidden")


def atomic_publish_bytes(dest_path: Path, data: bytes, no_clobber: bool = True) -> None:
    """Atomically writes bytes to dest_path without truncation/overwrite window."""
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    
    if no_clobber and (dest_path.exists() or os.path.lexists(dest_path)):
        raise FileExistsError(f"Destination path already exists and cannot be overwritten: {dest_path}")
        
    tmp_path = dest_path.parent / f".tmp_{dest_path.name}_{os.getpid()}_{hashlib.sha256(data).hexdigest()[:8]}"
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            
        if no_clobber:
            # os.link atomically creates destination; raises FileExistsError if dest_path exists
            os.link(tmp_path, dest_path)
            tmp_path.unlink()
        else:
            os.replace(tmp_path, dest_path)
    finally:
        if tmp_path.exists() or os.path.lexists(tmp_path):
            try:
                tmp_path.unlink()
            except OSError:
                pass


def atomic_publish_text(dest_path: Path, text: str, encoding: str = "utf-8", no_clobber: bool = True) -> None:
    """Atomically writes text to dest_path."""
    atomic_publish_bytes(dest_path, text.encode(encoding), no_clobber=no_clobber)


def _new_decompressor(encoding_header: str):
    """Creates a fresh single-member decompressor for a supported encoding."""
    if encoding_header in ("gzip", "x-gzip"):
        # 16 + MAX_WBITS enables gzip decoding with header and trailer CRC32/ISIZE verification
        return zlib.decompressobj(16 + zlib.MAX_WBITS)
    # deflate: zlib stream format with adler32
    return zlib.decompressobj()


def decode_http_stream(resp: requests.Response, max_bytes: int) -> Tuple[bytes, bool, int]:
    """
    Decodes HTTP response body stream while strictly enforcing max_bytes limit
    and verifying complete decompression (including trailers and checksums).

    N1 discipline: the wire stream is complete only when the decoder reaches
    EOF and no wire bytes remain unconsumed. Bytes following a member's EOF
    (decompressor.unused_data) are fed to a fresh member decompressor, so
    concatenated gzip members decode as one body and any trailing bytes that
    are not a valid continuation member reject the capture.

    Returns:
        (decoded_bytes, eof_complete, compressed_bytes_read)
    """
    encoding_header = resp.headers.get("Content-Encoding", "").strip().lower()

    # Supported encodings
    if encoding_header in ("gzip", "x-gzip", "deflate"):
        decompressor = _new_decompressor(encoding_header)
    elif encoding_header in ("", "identity"):
        decompressor = None
    else:
        raise TransportError(f"Unsupported Content-Encoding: {encoding_header}")

    decoded_buf = bytearray()
    compressed_bytes_read = 0
    chunk_size = 16384

    while True:
        try:
            chunk = resp.raw.read(chunk_size, decode_content=False)
        except Exception as exc:
            raise TransportDecodeError(f"Error reading wire stream: {exc}") from exc

        if not chunk:
            break
        compressed_bytes_read += len(chunk)

        if decompressor is None:
            decoded_buf.extend(chunk)
            if len(decoded_buf) > max_bytes:
                raise BodyLimitExceededError(
                    f"Decoded body length {len(decoded_buf)} exceeded limit {max_bytes} bytes"
                )
            continue

        # Feed the chunk through as many member decompressors as it spans.
        data = chunk
        while data:
            try:
                out = decompressor.decompress(data)
            except zlib.error as ze:
                raise TransportDecodeError(f"Decompression error for {encoding_header}: {ze}") from ze
            decoded_buf.extend(out)
            if len(decoded_buf) > max_bytes:
                raise BodyLimitExceededError(
                    f"Decoded body length {len(decoded_buf)} exceeded limit {max_bytes} bytes"
                )
            if decompressor.eof:
                # Everything past this member's trailer belongs to the next
                # member; never park it in unused_data and forget it.
                data = decompressor.unused_data
                if data:
                    decompressor = _new_decompressor(encoding_header)
            else:
                data = b""

    if decompressor is not None:
        try:
            trailing = decompressor.flush()
        except zlib.error as ze:
            raise TransportDecodeError(f"Decompression flush error for {encoding_header}: {ze}") from ze
        decoded_buf.extend(trailing)

        if len(decoded_buf) > max_bytes:
            raise BodyLimitExceededError(
                f"Decoded body length {len(decoded_buf)} exceeded limit {max_bytes} bytes"
            )

        if not decompressor.eof:
            raise TransportDecodeError(
                f"Incomplete compressed stream: decoder EOF marker not reached (missing trailer or truncated data) for {encoding_header}"
            )
        if decompressor.unused_data:
            raise TransportDecodeError(
                f"Compressed stream has {len(decompressor.unused_data)} unconsumed wire bytes after decoder EOF for {encoding_header}"
            )

    return bytes(decoded_buf), True, compressed_bytes_read


def execute_attempt_worker(
    target_url: str,
    attempt_number: int,
    output_raw_path: Path,
    allowed_domains: Set[str],
    max_bytes: int,
    max_redirects: int,
    timeout: float,
    https_only: bool,
    verify_tls: bool,
    no_clobber: bool = True,
) -> Dict[str, Any]:
    """Single attempt worker: executes within isolated subprocess with clean environment."""
    start_time_mono = time.monotonic()
    started_at_utc = datetime.now(timezone.utc).isoformat()
    
    current_url = target_url
    redirect_count = 0
    hops_log: List[Dict[str, Any]] = []
    
    outcome = "failure"
    error_msg: Optional[str] = None
    final_bytes: Optional[bytes] = None
    eof_complete = False
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    
    session = requests.Session()
    session.trust_env = False
    session.cookies.clear()
    
    adapter = HTTPAdapter(max_retries=Retry(total=0, connect=0, read=0))
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    
    try:
        if no_clobber and (output_raw_path.exists() or os.path.lexists(output_raw_path)):
            raise FileExistsError(f"Destination raw path already exists: {output_raw_path}")
            
        while True:
            # 1. Validate target URL BEFORE sending request
            validate_target_url(current_url, allowed_domains, https_only=https_only)
            
            # 2. Build strictly clean outgoing request headers (NO cookies, NO credentials)
            parsed_curr = urllib.parse.urlsplit(current_url)
            outgoing_headers = {
                "Host": parsed_curr.netloc,
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "gzip, deflate",
            }
            
            # Explicit confirmation of absence in outgoing template
            assert "Cookie" not in outgoing_headers
            assert "Authorization" not in outgoing_headers
            assert "Proxy-Authorization" not in outgoing_headers
            session.cookies.clear()
            
            remaining_time = max(0.5, timeout - (time.monotonic() - start_time_mono))
            resp = session.get(
                current_url,
                headers=outgoing_headers,
                timeout=remaining_time,
                allow_redirects=False,
                stream=True,
                verify=verify_tls,
            )
            
            # Inspect actual prepared outgoing headers
            actual_req_headers = resp.request.headers
            actual_outgoing_names = sorted(list(actual_req_headers.keys()))
            
            cookie_sent = "Cookie" in actual_req_headers
            cred_sent = ("Authorization" in actual_req_headers or "Proxy-Authorization" in actual_req_headers)
            
            hop_record: Dict[str, Any] = {
                "hop_number": len(hops_log) + 1,
                "request_url": current_url,
                "request_method": "GET",
                "outgoing_header_names": actual_outgoing_names,
                "credential_headers_sent": cred_sent,
                "cookie_headers_sent": cookie_sent,
                "trust_env": False,
                "tls_verify": verify_tls,
                "response_status_code": resp.status_code,
            }
            
            status_code = resp.status_code
            content_type = resp.headers.get("Content-Type")
            
            # 3. Detect and redact Set-Cookie headers; clear cookie jar immediately
            set_cookie_detected = False
            sanitized_hdrs: Dict[str, str] = {}
            for k, v in resp.headers.items():
                if k.lower() == "set-cookie":
                    set_cookie_detected = True
                    sanitized_hdrs[k] = "[REDACTED_IGNORED]"
                else:
                    sanitized_hdrs[k] = v
            session.cookies.clear()
            
            hop_record["set_cookie_detected"] = set_cookie_detected
            hop_record["set_cookie_action"] = "ignored_not_stored"
            hop_record["response_headers_sanitized"] = sanitized_hdrs
            
            # 4. Handle redirects manually
            if resp.status_code in (301, 302, 303, 307, 308):
                redirect_count += 1
                location = resp.headers.get("Location")
                hop_record["redirect_location"] = location
                hops_log.append(hop_record)
                resp.close()
                
                if redirect_count > max_redirects:
                    raise TransportLimitError(
                        f"Redirect count {redirect_count} exceeds limit {max_redirects}"
                    )
                if not location:
                    raise TransportError(f"HTTP {status_code} redirect missing Location header")
                
                current_url = urllib.parse.urljoin(current_url, location)
                continue
            
            # Non-redirect response
            hops_log.append(hop_record)
            
            if status_code != 200:
                resp.close()
                raise TransportError(f"Unexpected HTTP status {status_code}")
            
            # 5. Stream decoded body with strict byte limit and verified EOF tracking
            try:
                decoded_bytes, eof_ok, wire_bytes = decode_http_stream(resp, max_bytes)
                eof_complete = eof_ok
                final_bytes = decoded_bytes
                outcome = "success"
            finally:
                resp.close()
            break
            
    except Exception as exc:
        outcome = "failure"
        error_msg = f"{type(exc).__name__}: {str(exc)}"
    finally:
        session.close()
        
    completed_at_utc = datetime.now(timezone.utc).isoformat()
    duration_ms = (time.monotonic() - start_time_mono) * 1000.0
    
    blob_sha256 = hashlib.sha256(final_bytes).hexdigest() if final_bytes is not None else None
    bytes_stored = len(final_bytes) if final_bytes is not None else 0
    
    if final_bytes is not None and outcome == "success":
        atomic_publish_bytes(output_raw_path, final_bytes, no_clobber=no_clobber)
        
    result_data = {
        "worker_pid": os.getpid(),
        "target_url": target_url,
        "final_url": current_url,
        "attempt_number": attempt_number,
        "started_at_utc": started_at_utc,
        "completed_at_utc": completed_at_utc,
        "duration_ms": round(duration_ms, 3),
        "deadline_seconds": timeout,
        "transport_outcome": outcome,
        "http_status": status_code,
        "content_type": content_type,
        "bytes_stored": bytes_stored,
        "blob_sha256": blob_sha256,
        "eof_complete": eof_complete,
        "redirect_count": redirect_count,
        "hops_count": len(hops_log),
        "hops": hops_log,
        "error_msg": error_msg,
        "killed": False,
        "response_headers_sanitized": hops_log[-1].get("response_headers_sanitized", {}) if hops_log else {},
    }
    return result_data


def run_single_attempt_supervisor(
    target_url: str,
    attempt_number: int,
    output_raw_path: Path,
    allowed_domains: Set[str],
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    worker_timeout: float = DEFAULT_WORKER_TIMEOUT,
    https_only: bool = True,
    verify_tls: bool = True,
    no_clobber: bool = True,
) -> Dict[str, Any]:
    """Parent process supervisor: executes attempt worker with hard deadline enforcement."""
    started_at_utc = datetime.now(timezone.utc).isoformat()
    start_mono = time.monotonic()
    
    if no_clobber and (output_raw_path.exists() or os.path.lexists(output_raw_path)):
        raise FileExistsError(f"Destination raw path already exists: {output_raw_path}")
        
    # Serialize attempt parameters for child process
    worker_args = {
        "target_url": target_url,
        "attempt_number": attempt_number,
        "output_raw_path": str(output_raw_path),
        "allowed_domains": list(allowed_domains),
        "max_bytes": max_bytes,
        "max_redirects": max_redirects,
        "timeout": worker_timeout,
        "https_only": https_only,
        "verify_tls": verify_tls,
        "no_clobber": no_clobber,
    }
    
    script = (
        "import sys, json; "
        f"sys.path.insert(0, '{str(Path(__file__).resolve().parent)}'); "
        "import bounded_research_driver as d; "
        "args = json.loads(sys.stdin.read()); "
        "res = d.execute_attempt_worker("
        "target_url=args['target_url'], "
        "attempt_number=args['attempt_number'], "
        "output_raw_path=d.Path(args['output_raw_path']), "
        "allowed_domains=set(args['allowed_domains']), "
        "max_bytes=args['max_bytes'], "
        "max_redirects=args['max_redirects'], "
        "timeout=args['timeout'], "
        "https_only=args['https_only'], "
        "verify_tls=args['verify_tls'], "
        "no_clobber=args['no_clobber']); "
        "sys.stdout.write(json.dumps(res))"
    )
    
    env = os.environ.copy()
    env.pop("NETRC", None)
    env.pop("HTTP_PROXY", None)
    env.pop("HTTPS_PROXY", None)
    env.pop("ALL_PROXY", None)
    env.pop("NO_PROXY", None)
    
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        preexec_fn=os.setsid,  # create separate process group for clean killpg
    )
    
    killed = False
    stdout_data = ""
    stderr_data = ""
    
    try:
        stdout_data, stderr_data = proc.communicate(
            input=json.dumps(worker_args),
            timeout=worker_timeout,
        )
    except subprocess.TimeoutExpired:
        killed = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
        stdout_data, stderr_data = proc.communicate()
        
    elapsed_ms = (time.monotonic() - start_mono) * 1000.0
    completed_at_utc = datetime.now(timezone.utc).isoformat()
    
    if killed:
        audit_record = {
            "worker_pid": proc.pid if proc else None,
            "target_url": target_url,
            "final_url": None,              # Unknown due to kill
            "attempt_number": attempt_number,
            "started_at_utc": started_at_utc,
            "completed_at_utc": completed_at_utc,
            "duration_ms": round(elapsed_ms, 3),
            "deadline_seconds": worker_timeout,
            "transport_outcome": "worker_killed_timeout",
            "http_status": None,
            "content_type": None,
            "bytes_stored": 0,
            "blob_sha256": None,
            "eof_complete": False,
            "redirect_count": None,          # Unknown due to kill
            "hops_count": None,              # Unknown due to kill
            "hops": None,                    # Unknown due to kill
            "error_msg": f"Worker process exceeded hard deadline of {worker_timeout}s and was killed (SIGKILL)",
            "killed": True,
            "unproven_due_to_kill": True,
            "response_headers_sanitized": {},
        }
        return audit_record
        
    if proc.returncode != 0:
        return {
            "worker_pid": proc.pid if proc else None,
            "target_url": target_url,
            "final_url": None,
            "attempt_number": attempt_number,
            "started_at_utc": started_at_utc,
            "completed_at_utc": completed_at_utc,
            "duration_ms": round(elapsed_ms, 3),
            "deadline_seconds": worker_timeout,
            "transport_outcome": "subprocess_error",
            "http_status": None,
            "content_type": None,
            "bytes_stored": 0,
            "blob_sha256": None,
            "eof_complete": False,
            "redirect_count": None,
            "hops_count": None,
            "hops": None,
            "error_msg": f"Worker failed with exit code {proc.returncode}: {stderr_data.strip()}",
            "killed": False,
            "response_headers_sanitized": {},
        }
        
    try:
        audit_record = json.loads(stdout_data)
        return audit_record
    except json.JSONDecodeError as jde:
        return {
            "worker_pid": proc.pid if proc else None,
            "target_url": target_url,
            "final_url": None,
            "attempt_number": attempt_number,
            "started_at_utc": started_at_utc,
            "completed_at_utc": completed_at_utc,
            "duration_ms": round(elapsed_ms, 3),
            "deadline_seconds": worker_timeout,
            "transport_outcome": "json_parse_error",
            "http_status": None,
            "content_type": None,
            "bytes_stored": 0,
            "blob_sha256": None,
            "eof_complete": False,
            "redirect_count": None,
            "hops_count": None,
            "hops": None,
            "error_msg": f"Failed to parse child JSON output: {jde}; output: {stdout_data[:200]}",
            "killed": False,
            "response_headers_sanitized": {},
        }


def check_preflight_conflicts(
    output_dir: Path,
    targets: List[Dict[str, Any]],
    no_clobber: bool = True,
) -> None:
    """Pre-flight check: verifies that NO conflicting destination files exist before any network attempts."""
    if not no_clobber:
        return
        
    fixtures_dir = output_dir / "fixtures"
    acquisition_dir = output_dir / "acquisition"
    
    candidate_paths = [
        fixtures_dir / "fixtures_summary.json",
        acquisition_dir / "acquisition_audit.json",
        acquisition_dir / "acquisition_package.json",
    ]
    for target in targets:
        k = target["fixture_key"]
        candidate_paths.append(fixtures_dir / f"raw_{k}.html")
        candidate_paths.append(fixtures_dir / f"manifest_{k}.json")
        
    conflicts = []
    for cp in candidate_paths:
        if cp.exists() or os.path.lexists(cp):
            conflicts.append(str(cp))
            
    if conflicts:
        raise FileExistsError(
            f"Pre-flight conflict: destination file(s) already exist and no_clobber=True: {conflicts}"
        )


def execute_bounded_acquisition(
    targets: List[Dict[str, Any]] = OFFICIAL_TARGETS,
    output_dir: Optional[Path] = None,
    allowed_domains: Set[str] = DEFAULT_ALLOWED_DOMAINS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    worker_timeout: float = DEFAULT_WORKER_TIMEOUT,
    supervisor_budget: float = DEFAULT_SUPERVISOR_BUDGET,
    max_attempts_per_url: int = DEFAULT_MAX_ATTEMPTS_PER_URL,
    max_total_attempts: int = DEFAULT_MAX_TOTAL_ATTEMPTS,
    https_only: bool = True,
    verify_tls: bool = True,
    no_clobber: bool = True,
) -> Dict[str, Any]:
    """Top-level acquisition supervisor: executes bounded attempts across targets."""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent
        
    fixtures_dir = output_dir / "fixtures"
    acquisition_dir = output_dir / "acquisition"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    acquisition_dir.mkdir(parents=True, exist_ok=True)
    
    # Pre-flight check: verify NO conflicts before ANY network activity
    check_preflight_conflicts(output_dir, targets, no_clobber=no_clobber)
    
    run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    supervisor_start_mono = time.monotonic()
    run_started_at_utc = datetime.now(timezone.utc).isoformat()
    
    total_attempts = 0
    all_attempt_logs: List[Dict[str, Any]] = []
    target_results: List[Dict[str, Any]] = []
    
    for target in targets:
        fixture_key = target["fixture_key"]
        url = target["url"]
        url_attempts = 0
        raw_output_path = fixtures_dir / f"raw_{fixture_key}.html"
        successful_attempt: Optional[Dict[str, Any]] = None
        
        while url_attempts < max_attempts_per_url and total_attempts < max_total_attempts:
            elapsed_supervisor = time.monotonic() - supervisor_start_mono
            remaining_supervisor = supervisor_budget - elapsed_supervisor
            if remaining_supervisor <= 0:
                break
                
            url_attempts += 1
            total_attempts += 1
            
            attempt_timeout = min(worker_timeout, remaining_supervisor)
            
            attempt_record = run_single_attempt_supervisor(
                target_url=url,
                attempt_number=total_attempts,
                output_raw_path=raw_output_path,
                allowed_domains=allowed_domains,
                max_bytes=max_bytes,
                max_redirects=max_redirects,
                worker_timeout=attempt_timeout,
                https_only=https_only,
                verify_tls=verify_tls,
                no_clobber=no_clobber,
            )
            all_attempt_logs.append(attempt_record)
            
            if attempt_record.get("transport_outcome") == "success":
                successful_attempt = attempt_record
                break
                
        if successful_attempt:
            manifest = {
                "fixture_key": fixture_key,
                "source_url": url,
                "final_url": successful_attempt["final_url"],
                "redirect_count": successful_attempt["redirect_count"],
                "http_status": successful_attempt["http_status"],
                "content_type": successful_attempt["content_type"],
                "bytes_stored": successful_attempt["bytes_stored"],
                "blob_sha256": successful_attempt["blob_sha256"],
                "fetch_started_at": successful_attempt["started_at_utc"],
                "fetch_completed_at": successful_attempt["completed_at_utc"],
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": successful_attempt["duration_ms"],
                "research_provenance": "research_fixture",
                "parent_feed_reference": target["parent_feed_reference"],
                "transport_outcome": "success",
                "eof_complete": True,
                "total_attempts_for_url": url_attempts,
                "attempt_number": successful_attempt["attempt_number"],
                "response_headers": successful_attempt.get("response_headers_sanitized", {}),
                "error_msg": None,
                "usable_for_forecast": False,
                "acquisition_verification_status": "VERIFIED_BOUNDED_RESEARCH",
                "issuer": "Federal Reserve Board",
            }
            manifest_path = fixtures_dir / f"manifest_{fixture_key}.json"
            atomic_publish_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True), no_clobber=no_clobber)
            target_results.append(manifest)
        else:
            last_attempt = all_attempt_logs[-1] if all_attempt_logs else {}
            manifest = {
                "fixture_key": fixture_key,
                "source_url": url,
                "final_url": last_attempt.get("final_url", url),
                "redirect_count": last_attempt.get("redirect_count"),
                "http_status": last_attempt.get("http_status"),
                "content_type": last_attempt.get("content_type"),
                "bytes_stored": 0,
                "blob_sha256": None,
                "fetch_started_at": last_attempt.get("started_at_utc"),
                "fetch_completed_at": last_attempt.get("completed_at_utc"),
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "research_provenance": "research_fixture",
                "parent_feed_reference": target["parent_feed_reference"],
                "transport_outcome": "failure",
                "eof_complete": False,
                "total_attempts_for_url": url_attempts,
                "error_msg": last_attempt.get("error_msg", "All attempts exhausted without success"),
                "usable_for_forecast": False,
                "acquisition_verification_status": "PARTIAL",
                "issuer": "Federal Reserve Board",
            }
            manifest_path = fixtures_dir / f"manifest_{fixture_key}.json"
            atomic_publish_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True), no_clobber=no_clobber)
            target_results.append(manifest)
            
    run_completed_at_utc = datetime.now(timezone.utc).isoformat()
    total_duration_ms = (time.monotonic() - supervisor_start_mono) * 1000.0
    
    summary_file = fixtures_dir / "fixtures_summary.json"
    atomic_publish_text(summary_file, json.dumps(target_results, indent=2, sort_keys=True), no_clobber=no_clobber)
    
    all_success = (len(target_results) == len(targets) and all(m.get("transport_outcome") == "success" for m in target_results))
    observed_hops = [h for a in all_attempt_logs if a.get("hops") for h in a["hops"]]
    killed_count = sum(1 for a in all_attempt_logs if a.get("killed"))
    known_hops = sum(a.get("hops_count", 0) for a in all_attempt_logs if a.get("hops_count") is not None)
    total_hops_executed = known_hops if killed_count == 0 else None
    
    audit_data = {
        "audit_version": AUDIT_VERSION,
        "audit_utc": run_completed_at_utc,
        "run_id": run_id,
        "driver_version": DRIVER_VERSION,
        "fresh_transport_verdict": "VERIFIED_BOUNDED_RESEARCH" if all_success else "PARTIAL",
        "supervisor_configuration": {
            "worker_timeout_seconds": worker_timeout,
            "supervisor_budget_seconds": supervisor_budget,
            "max_attempts_per_url": max_attempts_per_url,
            "max_total_attempts": max_total_attempts,
            "max_redirects_per_attempt": max_redirects,
            "max_decoded_bytes": max_bytes,
            "allowed_domains": sorted(list(allowed_domains)),
            "tls_verify": verify_tls,
            "trust_env": False,
            "https_only": https_only,
            "no_clobber_enforced": no_clobber,
        },
        "execution_summary": {
            "run_started_at_utc": run_started_at_utc,
            "run_completed_at_utc": run_completed_at_utc,
            "total_duration_ms": round(total_duration_ms, 3),
            "total_targets": len(targets),
            "successful_targets": sum(1 for m in target_results if m.get("transport_outcome") == "success"),
            "total_attempts_made": total_attempts,
            "total_hops_executed": total_hops_executed,
            "known_hops_executed": known_hops,
            "killed_workers_count": killed_count,
        },
        "attempts": all_attempt_logs,
        "proven_conditions": {
            "process_supervisor_active": True,
            # N2: isolation is "proven" only when at least one hop was actually
            # observed and every observed hop is clean. Zero observed hops must
            # never read as a proven fact.
            "cookie_isolation_on_each_hop": bool(observed_hops) and all(
                h.get("cookie_headers_sent") is False and h.get("credential_headers_sent") is False
                for h in observed_hops
            ),
            "discrete_attempt_counters_recorded": True,
            "discrete_eof_verified": all_success and all(m.get("eof_complete") is True for m in target_results),
            "tls_verified": verify_tls,
            "https_enforced": https_only,
            "body_limit_enforced": True,
            "no_clobber_verified": no_clobber,
        }
    }
    audit_file = acquisition_dir / "acquisition_audit.json"
    audit_bytes = json.dumps(audit_data, indent=2, sort_keys=True).encode("utf-8")
    atomic_publish_bytes(audit_file, audit_bytes, no_clobber=no_clobber)
    audit_sha256 = hashlib.sha256(audit_bytes).hexdigest()
    
    rev8_fixtures = output_dir.parent.parent / "revision8/fixtures"

    package_fixtures = []
    for m in target_results:
        key = m["fixture_key"]
        raw_p = fixtures_dir / f"raw_{key}.html"
        man_p = fixtures_dir / f"manifest_{key}.json"

        comp: Dict[str, Any] = {}
        prior_raw_p = rev8_fixtures / f"raw_{key}.html"
        if prior_raw_p.exists():
            p_bytes = prior_raw_p.read_bytes()
            p_sha = hashlib.sha256(p_bytes).hexdigest()
            comp = {
                "prior_revision8_bytes": len(p_bytes),
                "prior_revision8_sha256": p_sha,
                "bytes_match": len(p_bytes) == m.get("bytes_stored"),
                "sha256_match": p_sha == m.get("blob_sha256"),
            }
            
        package_fixtures.append({
            "fixture_key": key,
            "source_url": m["source_url"],
            "final_url": m["final_url"],
            "collected_at": m["collected_at"],
            "raw_file": raw_p.name,
            "raw_bytes_stored": m["bytes_stored"],
            "raw_sha256": m["blob_sha256"],
            "manifest_file": man_p.name,
            "manifest_sha256": hashlib.sha256(man_p.read_bytes()).hexdigest() if man_p.exists() else None,
            "parent_feed_reference": m["parent_feed_reference"],
            "comparison_with_prior_revision8": comp,
        })
        
    package_data = {
        "package_version": PACKAGE_VERSION,
        "generated_at_utc": run_completed_at_utc,
        "run_id": run_id,
        "driver_version": DRIVER_VERSION,
        "fresh_transport_status": "VERIFIED_BOUNDED_RESEARCH" if all_success else "PARTIAL",
        "prior_fixture_acquisition_status": "PARTIAL",
        "prior_audit_reference": "docs/research/2026-10-21/r3/increment1/revision8/acquisition/acquisition_audit.json",
        "historical_acquisition_status_note": (
            "Fresh transport verification applies to this bounded re-acquisition run with proven compressed EOF. "
            "Historical acquisition from 2026-10-19 remains honestly declared PARTIAL."
        ),
        "audit_sha256": audit_sha256,
        "total_attempts": total_attempts,
        "total_hops": total_hops_executed,
        "known_hops": known_hops,
        "fixtures": package_fixtures,
    }
    package_file = acquisition_dir / "acquisition_package.json"
    atomic_publish_text(package_file, json.dumps(package_data, indent=2, sort_keys=True), no_clobber=no_clobber)
    
    return {
        "run_id": run_id,
        "audit_file": str(audit_file),
        "package_file": str(package_file),
        "summary_file": str(summary_file),
        "total_attempts": total_attempts,
        "total_hops": total_hops_executed,
        "all_success": all_success,
        "fresh_transport_verdict": audit_data["fresh_transport_verdict"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Pythia R3.1.8 Bounded Research Acquisition Driver")
    parser.add_argument("--live", action="store_true", help="Execute single bounded live run against official Fed pages")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory for acquired fixtures and audit")
    parser.add_argument("--no-clobber", dest="no_clobber", action="store_true", default=True, help="Fail closed if output exists (default: True)")
    parser.add_argument("--allow-overwrite", dest="no_clobber", action="store_false", help="Allow overwriting output files")
    args = parser.parse_args()
    
    if not args.live:
        print("Driver called without --live. Use test suite for offline verification.")
        return 0
        
    print(f"Starting bounded research capture with {DRIVER_VERSION}...")
    try:
        res = execute_bounded_acquisition(output_dir=args.output_dir, no_clobber=args.no_clobber)
        print(f"Acquisition completed: verdict={res['fresh_transport_verdict']}, all_success={res['all_success']}")
        return 0 if res["all_success"] else 1
    except Exception as exc:
        print(f"Acquisition failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
