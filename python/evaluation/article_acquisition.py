"""Pythia R3.2: bounded acquisition and archiving of referenced official articles.

Promotes the R3.1 research-driver transport disciplines into a maintained
product component:

- One bounded fetch attempt per call, executed in a supervised subprocess
  with a hard deadline (SIGKILL); every attempt becomes an archive receipt,
  success or failure.
- Strict stream decoding: decoder EOF must be proven (gzip/deflate trailers)
  and no wire bytes may remain unconsumed (concatenated gzip members decode
  as one body, trailing garbage rejects the capture).
- Decoded body limit 2 MiB; anonymous transport (trust_env=False, null
  cookie jar); manual redirect gate; response Set-Cookie values redacted.
- Archiving goes through EvidenceArchive with an explicit article source
  registry (see R3.2 decision record): the accepted canonical archives and
  their default validation behaviour are untouched.
- Extraction reuses the accepted FOMC statement extractor/bundle builder,
  fed from the archived blob with an R3.1-shaped manifest extended with
  archive lineage (receipt/revision references).

Every CaptureResult produced here satisfies the strict archive-boundary
invariants of validate_capture_result: complete=True only for a fully
decoded, EOF-proven body whose counters equal its length.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from python.evaluation.evidence_archive import EvidenceArchive
from python.evaluation.evidence_reader import ReadOnlyArchiveReader, VerifiedCapture
from python.evaluation.fed_statement_extract import build_fed_statement_bundle
from python.evaluation.source_probe import (
    CaptureResult,
    create_anonymous_session,
    utc_now_iso,
)

ARTICLE_ACQUISITION_VERSION = "pythia-article-acquisition-r32/1.0"
ARTICLE_CHECKER_VERSION = "pythia-article-transport-check-r32/1.0"

USER_AGENT = "PythiaArticleAcquisition/1.0 (Evidence Archive; +https://github.com/Ger-Wer/pythia)"

DEFAULT_MAX_BYTES = 2097152  # 2 MiB strict decoded body limit
DEFAULT_WORKER_TIMEOUT = 20.0  # hard per-attempt process deadline, seconds
DEFAULT_MAX_REDIRECTS = 2

# Articles referenced by the accepted Fed monetary RSS lineage (R3.1).
DEFAULT_ARTICLE_SOURCES: Dict[str, str] = {
    "fed_article_monetary20260916a": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm",
    "fed_article_monetary20260729a": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm",
}


class ArticleAcquisitionError(Exception):
    """Raised when an article cannot be captured, archived, or extracted."""


class TransportError(Exception):
    """Base transport error."""


class TransportValidationError(TransportError):
    """Target URL or redirect validation error."""


class TransportLimitError(TransportError):
    """Exceeded redirect or budget limit."""


class BodyLimitExceededError(TransportError):
    """Decoded body length exceeded max limit."""


class TransportDecodeError(TransportError):
    """Decompression or EOF decode error."""


# ---------------------------------------------------------------------------
# Stream decoding (same discipline as the accepted R3.1.9 research driver)
# ---------------------------------------------------------------------------

def _new_decompressor(encoding_header: str):
    if encoding_header in ("gzip", "x-gzip"):
        return zlib.decompressobj(16 + zlib.MAX_WBITS)
    return zlib.decompressobj()


def decode_http_stream(resp: requests.Response, max_bytes: int) -> Tuple[bytes, bool, int]:
    """Decode a response body with proven decoder EOF and zero leftover bytes."""
    encoding_header = resp.headers.get("Content-Encoding", "").strip().lower()

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


# ---------------------------------------------------------------------------
# Single-attempt worker (executed in a supervised subprocess)
# ---------------------------------------------------------------------------

def _allowed_hosts(url: str) -> Set[str]:
    host = urllib.parse.urlsplit(url).hostname or ""
    hosts = {host}
    if host.startswith("www."):
        hosts.add(host[4:])
    else:
        hosts.add("www." + host)
    return hosts


def _validate_url(url: str, allowed_hosts: Set[str], https_only: bool) -> None:
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
    if hostname.lower() not in allowed_hosts:
        raise TransportValidationError(f"Host '{hostname}' not in allowed set: {sorted(allowed_hosts)}")
    port = parsed.port
    if port is not None:
        if https_only and port != 443:
            raise TransportValidationError(f"Non-standard port {port} forbidden")
        if not https_only and port not in (80, 443) and hostname != "127.0.0.1":
            raise TransportValidationError(f"Non-standard port {port} forbidden")


def _fetch_worker(args: Dict[str, Any]) -> Dict[str, Any]:
    """One bounded fetch attempt. Writes the decoded body to args['body_path']
    on success only and returns a JSON-serializable audit record."""
    target_url: str = args["target_url"]
    body_path = Path(args["body_path"])
    max_bytes: int = args["max_bytes"]
    max_redirects: int = args["max_redirects"]
    timeout: float = args["timeout"]
    https_only: bool = args["https_only"]
    verify_tls: bool = args["verify_tls"]

    allowed_hosts = _allowed_hosts(target_url)
    start_mono = time.monotonic()
    started_at = utc_now_iso()

    current_url = target_url
    redirect_count = 0
    hops: List[Dict[str, Any]] = []
    outcome = "failure"
    error_msg: Optional[str] = None
    error_type: Optional[str] = None
    status_code: Optional[int] = None
    final_headers: Dict[str, str] = {}
    body_len = 0
    body_sha: Optional[str] = None
    eof_complete = False

    session = create_anonymous_session()
    try:
        while True:
            _validate_url(current_url, allowed_hosts, https_only=https_only)
            parsed_curr = urllib.parse.urlsplit(current_url)
            outgoing_headers = {
                "Host": parsed_curr.netloc,
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "gzip, deflate",
            }
            remaining = max(0.5, timeout - (time.monotonic() - start_mono))
            resp = session.get(
                current_url,
                headers=outgoing_headers,
                timeout=remaining,
                allow_redirects=False,
                stream=True,
                verify=verify_tls,
            )
            actual_names = sorted(resp.request.headers.keys())
            hop = {
                "hop_number": len(hops) + 1,
                "request_url": current_url,
                "outgoing_header_names": actual_names,
                "cookie_headers_sent": "Cookie" in resp.request.headers,
                "credential_headers_sent": (
                    "Authorization" in resp.request.headers
                    or "Proxy-Authorization" in resp.request.headers
                ),
                "response_status_code": resp.status_code,
            }
            sanitized: Dict[str, str] = {}
            set_cookie = False
            for k, v in resp.headers.items():
                if k.lower() == "set-cookie":
                    set_cookie = True
                    sanitized[k] = "[REDACTED_IGNORED]"
                else:
                    sanitized[k] = v
            hop["set_cookie_detected"] = set_cookie
            hop["response_headers_sanitized"] = sanitized
            status_code = resp.status_code
            final_headers = sanitized
            hops.append(hop)

            if resp.status_code in (301, 302, 303, 307, 308):
                redirect_count += 1
                location = resp.headers.get("Location")
                resp.close()
                if redirect_count > max_redirects:
                    raise TransportLimitError(f"Redirect count {redirect_count} exceeds limit {max_redirects}")
                if not location:
                    raise TransportError(f"HTTP {status_code} redirect missing Location header")
                current_url = urllib.parse.urljoin(current_url, location)
                continue

            if status_code != 200:
                resp.close()
                raise TransportError(f"Unexpected HTTP status {status_code}")

            try:
                decoded, eof_ok, _wire = decode_http_stream(resp, max_bytes)
                eof_complete = eof_ok
            finally:
                resp.close()
            tmp_path = body_path.with_name(f".tmp_{body_path.name}_{os.getpid()}")
            with open(tmp_path, "wb") as f:
                f.write(decoded)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, body_path)
            body_len = len(decoded)
            body_sha = hashlib.sha256(decoded).hexdigest()
            outcome = "success"
            break
    except Exception as exc:  # noqa: BLE001 - the worker reports, never crashes the parent
        outcome = "failure"
        error_type = type(exc).__name__
        error_msg = f"{error_type}: {exc}"
    finally:
        session.close()

    return {
        "outcome": outcome,
        "error_msg": error_msg,
        "error_type": error_type,
        "target_url": target_url,
        "final_url": current_url if status_code is not None else None,
        "http_status": status_code,
        "redirect_count": redirect_count,
        "hops": hops,
        "response_headers_sanitized": final_headers,
        "eof_complete": eof_complete,
        "bytes_stored": body_len if outcome == "success" else 0,
        "blob_sha256": body_sha,
        "started_at_utc": started_at,
        "completed_at_utc": utc_now_iso(),
        "duration_ms": round((time.monotonic() - start_mono) * 1000.0, 3),
        "killed": False,
    }


def _worker_main() -> None:
    args = json.loads(sys.stdin.read())
    result = _fetch_worker(args)
    sys.stdout.write(json.dumps(result))


# ---------------------------------------------------------------------------
# Supervised capture -> CaptureResult
# ---------------------------------------------------------------------------

def capture_article(
    source_id: str,
    registry: Optional[Dict[str, str]] = None,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    worker_timeout: float = DEFAULT_WORKER_TIMEOUT,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    https_only: bool = True,
    verify_tls: bool = True,
    work_dir: Optional[Path] = None,
) -> Tuple[CaptureResult, Dict[str, Any]]:
    """One supervised bounded capture attempt for a registered article.

    Returns (CaptureResult, audit). The CaptureResult is complete=True only
    when the body was fully decoded with proven EOF; every other outcome is
    an honest complete=False with an error message, fit to be archived as a
    failure receipt.
    """
    registry = DEFAULT_ARTICLE_SOURCES if registry is None else registry
    if source_id not in registry:
        raise ArticleAcquisitionError(f"Unknown article source_id: {source_id!r}")
    target_url = registry[source_id]

    own_work_dir = work_dir is None
    if own_work_dir:
        work_dir = Path(tempfile.mkdtemp(prefix="pythia-article-"))
    else:
        work_dir.mkdir(parents=True, exist_ok=True)
    body_path = work_dir / f"body_{source_id}.bin"

    project_root = Path(__file__).resolve().parents[2]
    script = (
        "import sys, json; "
        f"sys.path.insert(0, {str(project_root)!r}); "
        "from python.evaluation.article_acquisition import _worker_main; "
        "_worker_main()"
    )
    worker_args = {
        "target_url": target_url,
        "body_path": str(body_path),
        "max_bytes": max_bytes,
        "max_redirects": max_redirects,
        "timeout": worker_timeout,
        "https_only": https_only,
        "verify_tls": verify_tls,
    }
    env = os.environ.copy()
    for var in ("NETRC", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        env.pop(var, None)

    started_at = utc_now_iso()
    start_mono = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        preexec_fn=os.setsid,
    )
    killed = False
    stdout_data, stderr_data = "", ""
    try:
        stdout_data, stderr_data = proc.communicate(input=json.dumps(worker_args), timeout=worker_timeout)
    except subprocess.TimeoutExpired:
        killed = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
        stdout_data, stderr_data = proc.communicate()
    duration_ms = round((time.monotonic() - start_mono) * 1000.0, 3)
    completed_at = utc_now_iso()

    try:
        if killed:
            audit = {
                "outcome": "worker_killed_timeout",
                "error_msg": f"Worker exceeded hard deadline of {worker_timeout}s and was killed (SIGKILL)",
                "error_type": "WorkerKilledTimeout",
                "target_url": target_url,
                "final_url": None,
                "http_status": None,
                "redirect_count": None,
                "hops": None,
                "response_headers_sanitized": {},
                "eof_complete": False,
                "bytes_stored": 0,
                "blob_sha256": None,
                "started_at_utc": started_at,
                "completed_at_utc": completed_at,
                "duration_ms": duration_ms,
                "killed": True,
            }
            capture = CaptureResult(
                source_id=source_id,
                initial_url=target_url,
                final_url=None,
                body=b"",
                status_code=None,
                headers={},
                fetch_started_at=started_at,
                fetch_completed_at=completed_at,
                duration_ms=duration_ms,
                bytes_received=None,
                bytes_stored=0,
                complete=False,
                limit_exceeded=False,
                error_msg=audit["error_msg"],
                collector_version=ARTICLE_ACQUISITION_VERSION,
            )
            return capture, audit

        if proc.returncode != 0:
            raise ArticleAcquisitionError(
                f"Capture worker failed with exit code {proc.returncode}: {stderr_data.strip()[:400]}"
            )
        audit = json.loads(stdout_data)

        if audit["outcome"] == "success":
            body = body_path.read_bytes()
            actual_sha = hashlib.sha256(body).hexdigest()
            if actual_sha != audit["blob_sha256"] or len(body) != audit["bytes_stored"]:
                raise ArticleAcquisitionError("Worker body file does not match its audit record")
            capture = CaptureResult(
                source_id=source_id,
                initial_url=target_url,
                final_url=audit["final_url"],
                body=body,
                status_code=audit["http_status"],
                headers=audit["response_headers_sanitized"],
                fetch_started_at=audit["started_at_utc"],
                fetch_completed_at=audit["completed_at_utc"],
                duration_ms=audit["duration_ms"],
                bytes_received=len(body),
                bytes_stored=len(body),
                complete=True,
                limit_exceeded=False,
                error_msg=None,
                collector_version=ARTICLE_ACQUISITION_VERSION,
            )
            return capture, audit

        # Honest transport failure: no body, no revision-worthy bytes.
        capture = CaptureResult(
            source_id=source_id,
            initial_url=target_url,
            final_url=audit["final_url"],
            body=b"",
            status_code=audit["http_status"],
            headers=audit["response_headers_sanitized"],
            fetch_started_at=audit["started_at_utc"],
            fetch_completed_at=audit["completed_at_utc"],
            duration_ms=audit["duration_ms"],
            bytes_received=None,
            bytes_stored=0,
            complete=False,
            limit_exceeded=audit.get("error_type") == "BodyLimitExceededError",
            error_msg=audit["error_msg"] or "Transport failure",
            collector_version=ARTICLE_ACQUISITION_VERSION,
        )
        return capture, audit
    finally:
        if own_work_dir:
            try:
                body_path.unlink(missing_ok=True)
                work_dir.rmdir()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Archiving
# ---------------------------------------------------------------------------

def archive_article(
    state_dir: Path | str,
    source_id: str,
    registry: Optional[Dict[str, str]] = None,
    **capture_kwargs: Any,
) -> Dict[str, Any]:
    """Capture one article attempt and record it in the article archive.

    Returns the receipt identity plus the transport audit. A failed capture
    is archived as a failure receipt (no revision), exactly like the
    accepted R2 attempt semantics.
    """
    registry = DEFAULT_ARTICLE_SOURCES if registry is None else registry
    capture, audit = capture_article(source_id, registry, **capture_kwargs)
    archive = EvidenceArchive(state_dir, source_registry=registry)
    if capture.complete:
        probe_result = {
            "status": "ok",
            "validation_level": "transport",
            "reason": "Bounded article capture: proven compressed-stream EOF, decoded limit enforced, cookie isolation; content validation deferred to extraction.",
            "checker_version": ARTICLE_CHECKER_VERSION,
        }
    else:
        probe_result = {
            "status": "network_error",
            "validation_level": "transport",
            "reason": capture.error_msg or "Transport failure",
            "checker_version": ARTICLE_CHECKER_VERSION,
        }
    receipt = archive.record_capture(capture=capture, probe_result=probe_result)
    return {
        "receipt_id": receipt.receipt_id,
        "revision_id": receipt.revision_id,
        "seq": receipt.seq,
        "status": receipt.status,
        "blob_sha256": receipt.blob_sha256,
        "complete": capture.complete,
        "audit": audit,
    }


# ---------------------------------------------------------------------------
# Extraction from the archive
# ---------------------------------------------------------------------------

def build_article_manifest(
    vc: VerifiedCapture,
    *,
    archive_name: str,
    parent_feed_reference: Optional[Dict[str, Any]] = None,
    capture_audit: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the R3.1-shaped statement manifest for an archived article blob."""
    receipt = vc.receipt
    if vc.blob_bytes is None:
        raise ArticleAcquisitionError(f"Receipt {receipt.receipt_id} has no verified blob")
    return {
        "fixture_key": receipt.source_id,
        "source_url": receipt.initial_url,
        "final_url": receipt.final_url,
        "redirect_count": (capture_audit or {}).get("redirect_count"),
        "http_status": receipt.http_status,
        "content_type": receipt.headers.get("Content-Type") or receipt.headers.get("content-type"),
        "bytes_stored": len(vc.blob_bytes),
        "blob_sha256": receipt.blob_sha256,
        "fetch_started_at": receipt.fetch_started_at,
        "fetch_completed_at": receipt.fetch_completed_at,
        "collected_at": receipt.recorded_at,
        "duration_ms": receipt.duration_ms,
        "research_provenance": "research_fixture",
        "parent_feed_reference": parent_feed_reference,
        "transport_outcome": "success" if receipt.complete else "failure",
        "eof_complete": True if receipt.complete else False,
        "total_attempts_for_url": 1,
        "attempt_number": receipt.seq,
        "response_headers": receipt.headers,
        "error_msg": receipt.error_msg,
        "usable_for_forecast": False,
        "acquisition_verification_status": "VERIFIED_BOUNDED_RESEARCH",
        "issuer": "Federal Reserve Board",
        "archive_name": archive_name,
        "archive_receipt_id": receipt.receipt_id,
        "archive_revision_id": receipt.revision_id,
        "archive_seq": receipt.seq,
    }


def extract_archived_article(
    state_dir: Path | str,
    receipt_id: str,
    as_of_seq: int,
    output_dir: Path | str,
    *,
    archive_name: str,
    parent_feed_reference: Optional[Dict[str, Any]] = None,
    capture_audit: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Extract a statement bundle from an archived article receipt.

    Stages the verified blob and manifest to a temp dir and reuses the
    accepted build_fed_statement_bundle verbatim; the published bundle
    carries the archive lineage fields from the manifest.
    """
    reader = ReadOnlyArchiveReader(state_dir)
    captures = reader.read_receipts([receipt_id], as_of_seq)
    if len(captures) != 1:
        raise ArticleAcquisitionError(f"Expected exactly one verified capture for receipt {receipt_id}")
    vc = captures[0]
    manifest = build_article_manifest(
        vc,
        archive_name=archive_name,
        parent_feed_reference=parent_feed_reference,
        capture_audit=capture_audit,
    )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pythia-article-extract-") as td:
        stage = Path(td)
        raw_path = stage / f"raw_{vc.receipt.source_id}.html"
        man_path = stage / f"manifest_{vc.receipt.source_id}.json"
        raw_path.write_bytes(vc.blob_bytes or b"")
        man_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        bundle_file, bundle_data = build_fed_statement_bundle(raw_path, man_path, output_dir)
    return {
        "bundle_file": bundle_file.name,
        "bundle_digest": bundle_data["bundle_digest"],
        "receipt_id": receipt_id,
        "revision_id": vc.receipt.revision_id,
        "manifest": manifest,
    }
