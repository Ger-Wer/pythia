"""Pythia R1.3: Bounded single-run probe for the 8 canonical evidence sources.

Runs sequential diagnostic HTTP/HTTPS requests against sources specified in
source_registry.json. Performs strict transport, envelope, and content validation
under controlled constraints (streaming 2 MiB limit, overall 15s deadline per source
enforced via short-lived worker process under parent control via Popen + communicate,
max 3 redirects within domain allowlists, anonymous transport with no ambient
credentials or persistent cookies).

Usage:
    python -m python.evaluation.source_probe --output <path-to-json-output>
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.cookiejar
import json
import logging
import math
import os
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import feedparser
import requests

PROBE_VERSION = "pythia-probe-r2.1/1.0"
COLLECTOR_VERSION = "pythia-collector-r2.1/1.0"
STREAM_LIMIT_BYTES = 2 * 1024 * 1024  # 2 MiB = 2,097,152 bytes
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_REDIRECTS = 3
CLEANUP_GRACE_SECONDS = 0.15
MAX_CLEANUP_SECONDS = 0.50

DEFAULT_USER_AGENT = "Pythia-source-probe/2.0 (+https://github.com/Ger-Wer/pythia)"


@dataclass(frozen=True)
class CaptureResult:
    """Typed container for a single bounded fetch attempt."""

    source_id: str
    initial_url: str
    final_url: Optional[str]
    body: bytes
    status_code: Optional[int]
    headers: Dict[str, str]
    fetch_started_at: str
    fetch_completed_at: str
    duration_ms: float
    bytes_received: Optional[int]
    bytes_stored: Optional[int]
    complete: bool
    limit_exceeded: bool
    error_msg: Optional[str]
    collector_version: str = COLLECTOR_VERSION

    @property
    def content_type(self) -> Optional[str]:
        for k, v in self.headers.items():
            if k.lower() == "content-type":
                return v
        return None


CANONICAL_SOURCES: Dict[str, str] = {
    "nyfed_effr": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json",
    "fed_monetary_rss": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "fed_speeches_rss": "https://www.federalreserve.gov/feeds/speeches.xml",
    "bls_cpi_rss": "https://www.bls.gov/feed/cpi.rss",
    "atlanta_gdpnow": "https://www.atlantafed.org/research-and-data/data/gdpnow",
    "github_deepseek_v3_releases": "https://api.github.com/repos/deepseek-ai/DeepSeek-V3/releases?per_page=2",
    "hf_deepseek_models": "https://huggingface.co/api/models?author=deepseek-ai&sort=lastModified&direction=-1&limit=3",
    "arena_text_overall": "https://arena.ai/leaderboard/text/overall-no-style-control",
}

ALLOWED_DOMAINS: Dict[str, Set[str]] = {
    "nyfed_effr": {"markets.newyorkfed.org"},
    "fed_monetary_rss": {"www.federalreserve.gov", "federalreserve.gov"},
    "fed_speeches_rss": {"www.federalreserve.gov", "federalreserve.gov"},
    "bls_cpi_rss": {"www.bls.gov", "bls.gov"},
    "atlanta_gdpnow": {"www.atlantafed.org", "atlantafed.org"},
    "github_deepseek_v3_releases": {"api.github.com"},
    "hf_deepseek_models": {"huggingface.co"},
    "arena_text_overall": {"arena.ai"},
}

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    """Return ISO 8601 UTC timestamp string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class NullCookieJar(requests.cookies.RequestsCookieJar):
    """CookieJar that completely rejects storing, updating, or sending any cookies."""

    def set_cookie(self, *args: Any, **kwargs: Any) -> None:
        pass

    def update_cookies(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __iter__(self) -> Any:
        return iter(())

    def __len__(self) -> int:
        return 0


def create_anonymous_session() -> requests.Session:
    """Create an isolated, anonymous requests Session without ambient credentials or cookies."""
    sess = requests.Session()
    # Explicitly disable ambient environment (.netrc, ambient proxy credentials)
    sess.trust_env = False
    # Explicitly block all incoming and outgoing cookies via null jar
    sess.cookies = NullCookieJar()
    # Explicitly ensure no ambient authentication header
    sess.auth = None
    # Preserve TLS verification
    sess.verify = True
    return sess


def validate_registry(sources: Any) -> List[Dict[str, Any]]:
    """Validate that the registry contains exactly the 8 canonical sources with correct URLs."""
    if not isinstance(sources, list):
        raise ValueError(f"Registry 'sources' must be a list, got {type(sources).__name__}")

    if len(sources) != len(CANONICAL_SOURCES):
        raise ValueError(
            f"Registry must contain exactly {len(CANONICAL_SOURCES)} canonical sources, found {len(sources)}"
        )

    seen_ids: Set[str] = set()
    for s in sources:
        if not isinstance(s, dict):
            raise ValueError(f"Registry source entry must be an object, got {type(s).__name__}")
        sid = s.get("source_id")
        if not sid or not isinstance(sid, str):
            raise ValueError(f"Registry entry missing valid 'source_id': {s}")
        if sid in seen_ids:
            raise ValueError(f"Duplicate source_id in registry: '{sid}'")
        if sid not in CANONICAL_SOURCES:
            raise ValueError(f"Disallowed or extraneous source_id in registry: '{sid}'")
        seen_ids.add(sid)

        url = s.get("url")
        canonical_url = CANONICAL_SOURCES[sid]
        if url != canonical_url:
            raise ValueError(
                f"URL mismatch for '{sid}': expected '{canonical_url}', got '{url}'"
            )

    if seen_ids != set(CANONICAL_SOURCES.keys()):
        missing = set(CANONICAL_SOURCES.keys()) - seen_ids
        raise ValueError(f"Registry is missing required canonical source IDs: {missing}")

    return sources


def _fetch_bounded_core(
    sess: requests.Session,
    source_id: str,
    initial_url: str,
    timeout: float,
    max_redirects: int,
    max_bytes: int,
    user_agent: str,
) -> Dict[str, Any]:
    """Inner core fetch executed inside worker process."""
    current_url = initial_url
    redirect_count = 0
    allowed_domains = ALLOWED_DOMAINS.get(source_id, set())

    headers = {
        "User-Agent": user_agent,
        "Accept": "*/*",
    }

    start_time = time.perf_counter()

    while True:
        elapsed = time.perf_counter() - start_time
        if elapsed > timeout:
            duration_ms = elapsed * 1000.0
            return {
                "status_code": None,
                "headers": {},
                "content_type": None,
                "body": b"",
                "bytes_received": 0,
                "bytes_stored": 0,
                "complete": False,
                "limit_exceeded": False,
                "final_url": None,
                "duration_ms": duration_ms,
                "error_msg": f"Overall source deadline exceeded ({timeout}s): elapsed {elapsed:.2f}s",
            }

        remaining_timeout = max(0.001, timeout - elapsed)

        parsed = urllib.parse.urlparse(current_url)
        if parsed.scheme.lower() != "https":
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return {
                "status_code": None,
                "headers": {},
                "content_type": None,
                "body": b"",
                "bytes_received": 0,
                "bytes_stored": 0,
                "complete": False,
                "limit_exceeded": False,
                "final_url": None,
                "duration_ms": duration_ms,
                "error_msg": f"Non-HTTPS scheme rejected: {parsed.scheme}",
            }

        domain = parsed.netloc.lower()
        if domain not in allowed_domains:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return {
                "status_code": None,
                "headers": {},
                "content_type": None,
                "body": b"",
                "bytes_received": 0,
                "bytes_stored": 0,
                "complete": False,
                "limit_exceeded": False,
                "final_url": None,
                "duration_ms": duration_ms,
                "error_msg": f"Domain {domain} not in allowlist for source {source_id}",
            }

        try:
            resp = sess.get(
                current_url,
                headers=headers,
                stream=True,
                allow_redirects=False,
                timeout=remaining_timeout,
            )
        except requests.RequestException as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return {
                "status_code": None,
                "headers": {},
                "content_type": None,
                "body": b"",
                "bytes_received": 0,
                "bytes_stored": 0,
                "complete": False,
                "limit_exceeded": False,
                "final_url": None,
                "duration_ms": duration_ms,
                "error_msg": f"Network transport error: {type(exc).__name__}: {str(exc)}",
            }

        resp_headers = {str(k).lower(): str(v) for k, v in resp.headers.items()} if hasattr(resp, "headers") and resp.headers else {}

        # Handle redirects manually to enforce max_redirects and domain constraints
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            redirect_count += 1
            location = resp.headers.get("Location") if hasattr(resp, "headers") else None
            status_code = resp.status_code
            content_type = resp_headers.get("content-type")
            try:
                resp.close()
            except Exception:
                pass

            if redirect_count > max_redirects:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return {
                    "status_code": status_code,
                    "headers": resp_headers,
                    "content_type": content_type,
                    "body": b"",
                    "bytes_received": 0,
                    "bytes_stored": 0,
                    "complete": False,
                    "limit_exceeded": False,
                    "final_url": current_url,
                    "duration_ms": duration_ms,
                    "error_msg": f"Exceeded maximum redirect limit ({max_redirects})",
                }
            if not location:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return {
                    "status_code": status_code,
                    "headers": resp_headers,
                    "content_type": content_type,
                    "body": b"",
                    "bytes_received": 0,
                    "bytes_stored": 0,
                    "complete": False,
                    "limit_exceeded": False,
                    "final_url": current_url,
                    "duration_ms": duration_ms,
                    "error_msg": "Redirect response missing Location header",
                }
            current_url = urllib.parse.urljoin(current_url, location)
            continue

        # Stream body up to max_bytes + 1 byte
        content_type = resp_headers.get("content-type")
        status_code = resp.status_code
        chunks: List[bytes] = []
        bytes_received = 0
        bytes_stored = 0
        limit_exceeded = False
        read_error: Optional[str] = None

        try:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    chunk_len = len(chunk)
                    bytes_received += chunk_len
                    if bytes_stored + chunk_len > max_bytes:
                        limit_exceeded = True
                        remaining = max_bytes - bytes_stored
                        if remaining > 0:
                            chunks.append(chunk[:remaining])
                            bytes_stored += remaining
                        break
                    else:
                        chunks.append(chunk)
                        bytes_stored += chunk_len

                if (time.perf_counter() - start_time) > timeout:
                    read_error = f"Overall source deadline exceeded ({timeout}s) during stream transfer"
                    break
        except requests.RequestException as exc:
            read_error = f"Stream read error: {type(exc).__name__}: {str(exc)}"
        finally:
            try:
                resp.close()
            except Exception:
                pass

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        body = b"".join(chunks)

        if duration_ms > (timeout * 1000.0) and not read_error:
            read_error = f"Overall source deadline exceeded ({timeout}s): elapsed {duration_ms / 1000.0:.2f}s"

        complete = (status_code is not None and not limit_exceeded and read_error is None)

        return {
            "status_code": status_code,
            "headers": resp_headers,
            "content_type": content_type,
            "body": body,
            "bytes_received": bytes_received,
            "bytes_stored": bytes_stored,
            "complete": complete,
            "limit_exceeded": limit_exceeded,
            "final_url": current_url,
            "duration_ms": duration_ms,
            "error_msg": read_error,
        }


class SourceProbe:
    """Runs bounded diagnostic probes for configured evidence sources."""

    def __init__(
        self,
        registry_path: Optional[Path] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_redirects: int = MAX_REDIRECTS,
        max_bytes: int = STREAM_LIMIT_BYTES,
        user_agent: str = DEFAULT_USER_AGENT,
        worker_cmd: Optional[List[str]] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.registry_path = registry_path or Path(__file__).parent / "source_registry.json"
        self.timeout = float(timeout)
        self.max_redirects = max_redirects
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        if worker_cmd is not None:
            self.worker_cmd = list(worker_cmd)
        else:
            self.worker_cmd = [sys.executable, "-m", "python.evaluation.source_probe_worker"]
        self._owns_session = session is None
        if session is not None:
            self.session = session
        else:
            self.session = create_anonymous_session()

    def close(self) -> None:
        """Close internal session if owned."""
        if getattr(self, "_owns_session", False) and getattr(self, "session", None):
            try:
                self.session.close()
            except Exception:
                pass

    def __enter__(self) -> SourceProbe:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def load_registry(self) -> List[Dict[str, Any]]:
        """Load and return the list of source configurations from registry."""
        with open(self.registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        sources = data.get("sources", [])
        return validate_registry(sources)

    def capture_source(
        self,
        source_id: str,
        initial_url: str,
    ) -> CaptureResult:
        """Fetch URL with strict redirect, domain allowlist, deadline, and stream bounds
        enforced by an isolated worker process under parent deadline control via Popen + communicate.

        Returns a typed CaptureResult.
        """
        # Validate canonical source and URL prior to spawning worker process
        if source_id not in CANONICAL_SOURCES:
            raise ValueError(f"Unknown or non-canonical source_id: '{source_id}'")
        expected_url = CANONICAL_SOURCES[source_id]
        if initial_url != expected_url:
            raise ValueError(
                f"URL mismatch for '{source_id}': expected '{expected_url}', got '{initial_url}'"
            )

        start_monotonic = time.monotonic()
        started_at = utc_now_iso()
        deadline = start_monotonic + self.timeout

        worker_input = json.dumps({
            "source_id": source_id,
            "initial_url": initial_url,
            "timeout": self.timeout,
            "max_redirects": self.max_redirects,
            "max_bytes": self.max_bytes,
            "user_agent": self.user_agent,
        }).encode("utf-8")

        proc: Optional[subprocess.Popen] = None
        timed_out = False
        stdout_bytes = b""
        stderr_bytes = b""

        try:
            proc = subprocess.Popen(
                self.worker_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            remaining_timeout = max(0.0, deadline - time.monotonic())
            stdout_bytes, stderr_bytes = proc.communicate(
                input=worker_input,
                timeout=remaining_timeout,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
        except Exception as exc:
            elapsed = time.monotonic() - start_monotonic
            return CaptureResult(
                source_id=source_id,
                initial_url=initial_url,
                final_url=None,
                body=b"",
                status_code=None,
                headers={},
                fetch_started_at=started_at,
                fetch_completed_at=utc_now_iso(),
                duration_ms=elapsed * 1000.0,
                bytes_received=None,
                bytes_stored=None,
                complete=False,
                limit_exceeded=False,
                error_msg=f"Worker process launch error: {type(exc).__name__}: {str(exc)}",
                collector_version=COLLECTOR_VERSION,
            )
        finally:
            if proc is not None:
                cleanup_deadline = time.monotonic() + MAX_CLEANUP_SECONDS
                if timed_out or proc.poll() is None:
                    try:
                        proc.terminate()
                    except (ProcessLookupError, OSError):
                        pass

                    try:
                        grace_wait = min(CLEANUP_GRACE_SECONDS, max(0.0, cleanup_deadline - time.monotonic()))
                        proc.wait(timeout=grace_wait)
                    except subprocess.TimeoutExpired:
                        pass
                    except Exception:
                        pass

                    if proc.poll() is None:
                        try:
                            proc.kill()
                        except (ProcessLookupError, OSError):
                            pass
                        try:
                            kill_wait = max(0.0, cleanup_deadline - time.monotonic())
                            proc.wait(timeout=kill_wait)
                        except (subprocess.TimeoutExpired, Exception):
                            pass

                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    if pipe:
                        try:
                            pipe.close()
                        except Exception:
                            pass

        elapsed = time.monotonic() - start_monotonic
        duration_ms = elapsed * 1000.0
        completed_at = utc_now_iso()

        if timed_out:
            return CaptureResult(
                source_id=source_id,
                initial_url=initial_url,
                final_url=None,
                body=b"",
                status_code=None,
                headers={},
                fetch_started_at=started_at,
                fetch_completed_at=completed_at,
                duration_ms=duration_ms,
                bytes_received=None,
                bytes_stored=None,
                complete=False,
                limit_exceeded=False,
                error_msg=f"Overall source deadline exceeded ({self.timeout}s); worker process terminated.",
                collector_version=COLLECTOR_VERSION,
            )

        if proc is not None and proc.returncode != 0:
            err_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            return CaptureResult(
                source_id=source_id,
                initial_url=initial_url,
                final_url=None,
                body=b"",
                status_code=None,
                headers={},
                fetch_started_at=started_at,
                fetch_completed_at=completed_at,
                duration_ms=duration_ms,
                bytes_received=None,
                bytes_stored=None,
                complete=False,
                limit_exceeded=False,
                error_msg=f"Worker process terminated unexpectedly (exit code {proc.returncode}): {err_text[:200]}",
                collector_version=COLLECTOR_VERSION,
            )

        try:
            worker_result = json.loads(stdout_bytes.decode("utf-8"))
        except Exception as exc:
            return CaptureResult(
                source_id=source_id,
                initial_url=initial_url,
                final_url=None,
                body=b"",
                status_code=None,
                headers={},
                fetch_started_at=started_at,
                fetch_completed_at=completed_at,
                duration_ms=duration_ms,
                bytes_received=None,
                bytes_stored=None,
                complete=False,
                limit_exceeded=False,
                error_msg=f"Failed to parse worker output as JSON: {type(exc).__name__}: {str(exc)}",
                collector_version=COLLECTOR_VERSION,
            )

        if not isinstance(worker_result, dict):
            return CaptureResult(
                source_id=source_id,
                initial_url=initial_url,
                final_url=None,
                body=b"",
                status_code=None,
                headers={},
                fetch_started_at=started_at,
                fetch_completed_at=completed_at,
                duration_ms=duration_ms,
                bytes_received=None,
                bytes_stored=None,
                complete=False,
                limit_exceeded=False,
                error_msg="Worker output JSON must be an object.",
                collector_version=COLLECTOR_VERSION,
            )

        MANDATORY_WORKER_KEYS = {
            "status_code",
            "headers",
            "body_b64",
            "bytes_received",
            "bytes_stored",
            "complete",
            "limit_exceeded",
            "final_url",
            "error_msg",
        }
        missing_keys = [k for k in sorted(MANDATORY_WORKER_KEYS) if k not in worker_result]
        schema_err: Optional[str] = None
        if missing_keys:
            schema_err = f"Worker protocol error: missing required key(s): {', '.join(missing_keys)}"

        raw_complete = worker_result.get("complete")
        if type(raw_complete) is not bool:
            schema_err = schema_err or f"Worker 'complete' must be boolean, got {type(raw_complete).__name__}"
            complete = False
        else:
            complete = raw_complete

        raw_limit = worker_result.get("limit_exceeded")
        if type(raw_limit) is not bool:
            schema_err = schema_err or f"Worker 'limit_exceeded' must be boolean, got {type(raw_limit).__name__}"
            limit_exceeded = False
        else:
            limit_exceeded = raw_limit

        raw_status = worker_result.get("status_code")
        if raw_status is not None and (type(raw_status) is not int or isinstance(raw_status, bool)):
            schema_err = schema_err or f"Worker 'status_code' must be integer or null, got {type(raw_status).__name__}"
            status_code = None
        else:
            status_code = raw_status

        raw_final_url = worker_result.get("final_url")
        if raw_final_url is not None and type(raw_final_url) is not str:
            schema_err = schema_err or f"Worker 'final_url' must be string or null, got {type(raw_final_url).__name__}"
            final_url = None
        else:
            final_url = raw_final_url

        raw_headers = worker_result.get("headers")
        headers: Dict[str, str] = {}
        if raw_headers is not None:
            if type(raw_headers) is not dict or not all(type(k) is str and type(v) is str for k, v in raw_headers.items()):
                schema_err = schema_err or "Worker 'headers' must be an object with string keys and values"
            else:
                for hk, hv in raw_headers.items():
                    headers[hk.lower()] = hv
        else:
            schema_err = schema_err or "Worker 'headers' cannot be null"

        raw_received = worker_result.get("bytes_received")
        if raw_received is not None and (type(raw_received) is not int or isinstance(raw_received, bool) or raw_received < 0):
            schema_err = schema_err or f"Worker 'bytes_received' must be non-negative integer or null, got {raw_received!r}"
            bytes_received = None
        else:
            bytes_received = raw_received

        raw_stored = worker_result.get("bytes_stored")
        if raw_stored is not None and (type(raw_stored) is not int or isinstance(raw_stored, bool) or raw_stored < 0):
            schema_err = schema_err or f"Worker 'bytes_stored' must be non-negative integer or null, got {raw_stored!r}"
            bytes_stored = None
        else:
            bytes_stored = raw_stored

        body_b64 = worker_result.get("body_b64")
        body = b""
        if type(body_b64) is not str:
            schema_err = schema_err or f"Worker 'body_b64' must be str, got {type(body_b64).__name__}"
        else:
            try:
                body = base64.b64decode(body_b64, validate=True)
            except Exception as exc:
                schema_err = schema_err or f"Failed to decode worker body base64: {type(exc).__name__}: {str(exc)}"

        raw_error_msg = worker_result.get("error_msg")
        if raw_error_msg is not None and type(raw_error_msg) is not str:
            schema_err = schema_err or f"Worker 'error_msg' must be string or null, got {type(raw_error_msg).__name__}"
            worker_error_msg = None
        else:
            worker_error_msg = raw_error_msg

        if not schema_err:
            if bytes_stored is not None and bytes_stored != len(body):
                schema_err = f"Worker counter inconsistency: bytes_stored ({bytes_stored}) != len(body) ({len(body)})"
            elif bytes_received is not None and bytes_stored is not None and bytes_stored > bytes_received:
                schema_err = f"Worker counter inconsistency: bytes_stored ({bytes_stored}) > bytes_received ({bytes_received})"

        if complete:
            if schema_err is not None:
                complete = False
            elif limit_exceeded:
                schema_err = "Worker response marked complete=True cannot have limit_exceeded=True"
                complete = False
            elif worker_error_msg is not None:
                schema_err = f"Worker response marked complete=True cannot have error_msg: {worker_error_msg}"
                complete = False
            elif status_code is None or not (100 <= status_code < 600):
                schema_err = f"Worker response marked complete=True must have valid HTTP status code, got {status_code}"
                complete = False
            elif final_url is None or not final_url:
                schema_err = "Worker response marked complete=True must have non-empty final_url"
                complete = False
            elif bytes_received is None or bytes_stored is None:
                schema_err = "Worker response marked complete=True must have non-null byte counters"
                complete = False
            elif bytes_received != bytes_stored or bytes_stored != len(body):
                schema_err = f"Worker response marked complete=True counter mismatch: received={bytes_received}, stored={bytes_stored}, len={len(body)}"
                complete = False
            elif len(body) == 0:
                if not (bytes_received == 0 and bytes_stored == 0 and status_code is not None and not limit_exceeded and worker_error_msg is None):
                    schema_err = "Empty body without explicit zero counters cannot be complete"
                    complete = False

        if len(body) > self.max_bytes:
            limit_exceeded = True
            complete = False
            body = body[: self.max_bytes]
            schema_err = schema_err or f"Stream limit exceeded ({self.max_bytes} bytes)"

        error_msg = schema_err or worker_error_msg
        if schema_err:
            complete = False

        if not complete and (status_code is None or error_msg is not None):
            final_url = None

        # POST-DECODE DEADLINE CHECK & DURATION MEASUREMENT:
        elapsed = time.monotonic() - start_monotonic
        duration_ms = elapsed * 1000.0
        completed_at = utc_now_iso()

        if elapsed > self.timeout:
            return CaptureResult(
                source_id=source_id,
                initial_url=initial_url,
                final_url=None,
                body=b"",
                status_code=None,
                headers={},
                fetch_started_at=started_at,
                fetch_completed_at=completed_at,
                duration_ms=duration_ms,
                bytes_received=None,
                bytes_stored=None,
                complete=False,
                limit_exceeded=False,
                error_msg=f"Overall source deadline exceeded ({self.timeout}s): elapsed {elapsed:.3f}s; response rejected.",
                collector_version=COLLECTOR_VERSION,
            )

        return CaptureResult(
            source_id=source_id,
            initial_url=initial_url,
            final_url=final_url,
            body=body,
            status_code=status_code,
            headers=headers,
            fetch_started_at=started_at,
            fetch_completed_at=completed_at,
            duration_ms=duration_ms,
            bytes_received=bytes_received,
            bytes_stored=bytes_stored,
            complete=complete,
            limit_exceeded=limit_exceeded,
            error_msg=error_msg,
            collector_version=COLLECTOR_VERSION,
        )

    def _fetch_bounded(
        self,
        source_id: str,
        initial_url: str,
    ) -> CaptureResult:
        """Alias for capture_source for backward compatibility."""
        return self.capture_source(source_id, initial_url)

    def validate_content(
        self,
        source_id: str,
        http_status: Optional[int],
        content_type: Optional[str],
        body: bytes,
        limit_exceeded: bool,
        error_msg: Optional[str],
    ) -> Tuple[str, str, str, Optional[int]]:
        """Validate content against source contract.

        Returns:
            (status, validation_level, reason, item_count)
        """
        if error_msg:
            return "network_error", "transport", error_msg, None

        if http_status is None:
            return "network_error", "transport", "No HTTP response received", None

        # Check for stream limit violation
        if limit_exceeded:
            return (
                "invalid_content",
                "transport",
                f"Payload exceeded maximum stream limit ({self.max_bytes} bytes).",
                None,
            )

        # Check for HTTP blocked/denied status codes
        if http_status in (401, 403, 429):
            status_desc = {
                401: "Unauthorized",
                403: "Forbidden",
                429: "Too Many Requests",
            }.get(http_status, f"HTTP {http_status}")
            platform_name = {
                "nyfed_effr": "NY Fed",
                "fed_monetary_rss": "Federal Reserve Board",
                "fed_speeches_rss": "Federal Reserve Board",
                "bls_cpi_rss": "BLS Consumer Price Index",
                "atlanta_gdpnow": "Atlanta Fed",
                "github_deepseek_v3_releases": "GitHub API",
                "hf_deepseek_models": "Hugging Face API",
                "arena_text_overall": "LMSYS Chatbot Arena",
            }.get(source_id, source_id)
            return (
                "blocked",
                "transport",
                f"{platform_name} returned HTTP {http_status} {status_desc} (access blocked).",
                None,
            )

        if http_status != 200:
            return (
                "invalid_content",
                "transport",
                f"Unexpected HTTP status {http_status} (expected 200 OK).",
                None,
            )

        # Dispatch content validator based on source_id
        if source_id == "nyfed_effr":
            return self._validate_nyfed_effr(body)
        elif source_id == "fed_monetary_rss":
            return self._validate_fed_monetary_rss(body)
        elif source_id == "fed_speeches_rss":
            return self._validate_fed_speeches_rss(body)
        elif source_id == "bls_cpi_rss":
            return self._validate_bls_rss(body)
        elif source_id == "atlanta_gdpnow":
            return self._validate_atlanta_gdpnow(body, content_type)
        elif source_id == "github_deepseek_v3_releases":
            return self._validate_github_releases(body)
        elif source_id == "hf_deepseek_models":
            return self._validate_hf_models(body)
        elif source_id == "arena_text_overall":
            return self._validate_arena_overall(body, content_type)
        else:
            return "invalid_content", "envelope", f"Unknown source_id '{source_id}'", None

    def _validate_nyfed_effr(self, body: bytes) -> Tuple[str, str, str, Optional[int]]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return "invalid_content", "envelope", "Failed to parse NY Fed response as JSON.", None

        if not isinstance(payload, dict):
            return "invalid_content", "envelope", "Expected JSON object at top level.", None

        ref_rates = payload.get("refRates")
        if not isinstance(ref_rates, list):
            return "invalid_content", "content", "Missing or non-array 'refRates' field in response.", None

        if len(ref_rates) == 0:
            return "invalid_content", "content", "'refRates' array is empty.", 0

        valid_count = 0
        for idx, item in enumerate(ref_rates):
            if not isinstance(item, dict):
                return "invalid_content", "content", f"refRates[{idx}] is not an object.", None

            item_type = item.get("type")
            if not isinstance(item_type, str) or item_type != "EFFR":
                return (
                    "invalid_content",
                    "content",
                    f"refRates[{idx}] invalid series type: expected 'EFFR', got {item_type!r}.",
                    None,
                )

            eff_date = item.get("effectiveDate")
            if not isinstance(eff_date, str):
                return (
                    "invalid_content",
                    "content",
                    f"refRates[{idx}] missing string effectiveDate.",
                    None,
                )
            try:
                parts = eff_date.split("-")
                if len(parts) != 3:
                    raise ValueError("Date must have YYYY-MM-DD format")
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                datetime(year, month, day)
            except Exception:
                return (
                    "invalid_content",
                    "content",
                    f"refRates[{idx}] contains invalid calendar date: {eff_date!r}.",
                    None,
                )

            rate = item.get("percentRate")
            if isinstance(rate, bool) or not isinstance(rate, (int, float)):
                return (
                    "invalid_content",
                    "content",
                    f"refRates[{idx}] contains non-numeric percentRate: {rate!r}.",
                    None,
                )
            if math.isnan(rate) or math.isinf(rate):
                return (
                    "invalid_content",
                    "content",
                    f"refRates[{idx}] contains non-finite percentRate: {rate!r}.",
                    None,
                )

            valid_count += 1

        return (
            "valid",
            "content",
            f"Successfully validated {valid_count} EFFR reference rate records.",
            valid_count,
        )

    def _validate_fed_monetary_rss(self, body: bytes) -> Tuple[str, str, str, Optional[int]]:
        return self._validate_rss_feed(
            body=body,
            expected_feed_name="Federal Reserve Monetary Policy",
            allowed_channel_domains={"federalreserve.gov", "www.federalreserve.gov"},
            required_title_keywords={"monetary", "fomc"},
            forbidden_title_keywords={"banking", "orders", "speeches"},
            allowed_entry_domains={"federalreserve.gov", "www.federalreserve.gov"},
            required_entry_path_markers={"monetary"},
            forbidden_entry_path_markers={"/orders", "/banking"},
        )

    def _validate_fed_speeches_rss(self, body: bytes) -> Tuple[str, str, str, Optional[int]]:
        return self._validate_rss_feed(
            body=body,
            expected_feed_name="Federal Reserve Speeches",
            allowed_channel_domains={"federalreserve.gov", "www.federalreserve.gov"},
            required_title_keywords={"speeches", "remarks", "speech"},
            forbidden_title_keywords={"monetary policy"},
            allowed_entry_domains={"federalreserve.gov", "www.federalreserve.gov"},
            required_entry_path_markers={"speech", "speeches"},
            forbidden_entry_path_markers={"/monetary"},
        )

    def _validate_bls_rss(self, body: bytes) -> Tuple[str, str, str, Optional[int]]:
        raw_text = body.decode("utf-8", errors="replace")
        if "<html" in raw_text.lower() and "<rss" not in raw_text.lower() and "<feed" not in raw_text.lower():
            return "blocked", "envelope", "HTTP 200 returned HTML challenge page instead of CPI RSS feed.", None

        return self._validate_rss_feed(
            body=body,
            expected_feed_name="BLS Consumer Price Index",
            allowed_channel_domains={"bls.gov", "www.bls.gov"},
            required_title_keywords={"cpi", "consumer price index"},
            forbidden_title_keywords={"employment situation", "producer price index", "ppi"},
            allowed_entry_domains={"bls.gov", "www.bls.gov"},
            required_entry_path_markers={"cpi"},
            forbidden_entry_path_markers={"empsit", "ppi"},
            entry_identity_keywords={"cpi", "consumer price index"},
        )

    def _validate_rss_feed(
        self,
        body: bytes,
        expected_feed_name: str,
        allowed_channel_domains: Set[str],
        required_title_keywords: Set[str],
        forbidden_title_keywords: Set[str],
        allowed_entry_domains: Set[str],
        required_entry_path_markers: Set[str],
        forbidden_entry_path_markers: Optional[Set[str]] = None,
        entry_identity_keywords: Optional[Set[str]] = None,
    ) -> Tuple[str, str, str, Optional[int]]:
        forbidden_entry_path_markers = forbidden_entry_path_markers or set()
        entry_identity_keywords = entry_identity_keywords or set()

        # Step 1: Strict XML integrity parsing via standard library ElementTree
        try:
            xml_root = ET.fromstring(body)
        except ET.ParseError as exc:
            raw_text = body.decode("utf-8", errors="replace").lower()
            if "<html" in raw_text and "<rss" not in raw_text and "<feed" not in raw_text:
                return "blocked", "envelope", f"HTTP 200 returned HTML challenge page instead of {expected_feed_name}.", None
            return "invalid_content", "envelope", f"Malformed or truncated XML: {exc}", None

        tag_lower = xml_root.tag.lower()
        if tag_lower != "rss" and tag_lower != "feed" and not tag_lower.endswith("}feed") and not tag_lower.endswith("}rss"):
            if "html" in tag_lower:
                return "blocked", "envelope", f"HTTP 200 returned HTML document instead of {expected_feed_name}.", None
            return "invalid_content", "envelope", f"Unsupported XML root element for {expected_feed_name}: '{xml_root.tag}'", None

        # Step 2: Feed metadata extraction via feedparser
        feed = feedparser.parse(body)
        channel_title = getattr(feed.feed, "title", "")
        channel_link = getattr(feed.feed, "link", "")

        title_lower = channel_title.lower() if isinstance(channel_title, str) else ""
        link_lower = channel_link.lower() if isinstance(channel_link, str) else ""

        if link_lower:
            parsed_link = urllib.parse.urlparse(link_lower)
            channel_domain = parsed_link.netloc.lower()
            if allowed_channel_domains and channel_domain not in allowed_channel_domains:
                return (
                    "invalid_content",
                    "content",
                    f"Feed identity mismatch: channel link domain '{channel_domain}' not in expected domains for {expected_feed_name}.",
                    None,
                )

        if required_title_keywords:
            if not any(kw in title_lower for kw in required_title_keywords):
                return (
                    "invalid_content",
                    "content",
                    f"Feed identity mismatch: expected {expected_feed_name}, got channel title '{channel_title}'.",
                    None,
                )

        if any(fkw in title_lower for fkw in forbidden_title_keywords):
            return (
                "invalid_content",
                "content",
                f"Feed identity mismatch: channel title '{channel_title}' contains forbidden marker for {expected_feed_name}.",
                None,
            )

        entries = getattr(feed, "entries", [])
        if not entries:
            return "invalid_content", "content", f"RSS feed '{channel_title}' contains zero entries.", 0

        # Step 3: Strict entry validation
        valid_items = 0
        for entry in entries:
            title = getattr(entry, "title", None)
            link = getattr(entry, "link", None)
            if not isinstance(title, str) or len(title.strip()) == 0:
                continue
            if not isinstance(link, str) or len(link.strip()) == 0:
                continue

            parsed_entry_link = urllib.parse.urlparse(link)
            entry_domain = parsed_entry_link.netloc.lower()
            if allowed_entry_domains and entry_domain not in allowed_entry_domains:
                continue

            path_lower = parsed_entry_link.path.lower()
            entry_title_lower = title.lower()

            if any(fpm in path_lower for fpm in forbidden_entry_path_markers):
                continue

            if required_entry_path_markers:
                if not any(pm in path_lower for pm in required_entry_path_markers):
                    continue

            if entry_identity_keywords:
                if not any(ik in entry_title_lower or ik in path_lower for ik in entry_identity_keywords):
                    continue

            valid_items += 1

        if valid_items != len(entries) or valid_items == 0:
            return (
                "invalid_content",
                "content",
                f"Feed entries validation failed: {valid_items}/{len(entries)} entries valid for {expected_feed_name}.",
                valid_items,
            )

        return (
            "valid",
            "content",
            f"Valid {expected_feed_name} feed with {len(entries)} items ({valid_items} valid).",
            len(entries),
        )

    def _validate_atlanta_gdpnow(self, body: bytes, content_type: Optional[str]) -> Tuple[str, str, str, Optional[int]]:
        raw_text = body.decode("utf-8", errors="replace")
        if "<html" not in raw_text.lower():
            return "invalid_content", "envelope", "Response is not an HTML document.", None

        return (
            "unverified",
            "envelope",
            "HTML document received (transport and envelope confirmed); quantitative model table extraction not verified in R1.",
            None,
        )

    def _validate_github_releases(self, body: bytes) -> Tuple[str, str, str, Optional[int]]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return "invalid_content", "envelope", "Failed to parse GitHub response as JSON.", None

        if isinstance(payload, dict):
            message = str(payload.get("message", ""))
            if "rate limit" in message.lower():
                return "blocked", "transport", f"GitHub API rate limit exceeded: {message}", None
            return "invalid_content", "envelope", f"Expected releases array, got JSON object: {message}", None

        if not isinstance(payload, list):
            return "invalid_content", "envelope", "Expected JSON array of releases.", None

        if len(payload) == 0:
            return "valid", "content", "Valid empty GitHub releases array (item_count=0).", 0

        valid_items = 0
        for item in payload:
            if not isinstance(item, dict):
                return "invalid_content", "content", "Release entry is not a dictionary.", None
            tag = item.get("tag_name")
            if not isinstance(tag, str) or len(tag.strip()) == 0:
                return "invalid_content", "content", f"Release entry missing non-empty string tag_name: {tag!r}", None
            html_url = item.get("html_url")
            if not isinstance(html_url, str):
                return "invalid_content", "content", f"Release entry missing string html_url: {html_url!r}", None

            parsed = urllib.parse.urlparse(html_url)
            if parsed.netloc.lower() not in ("github.com", "api.github.com"):
                return "invalid_content", "content", f"Invalid GitHub release host: '{parsed.netloc}'", None
            parts = [p for p in parsed.path.strip("/").split("/") if p]
            if len(parts) < 3 or parts[0] != "deepseek-ai" or parts[1] != "DeepSeek-V3" or parts[2] != "releases":
                return (
                    "invalid_content",
                    "content",
                    f"Release repo/path mismatch: expected 'deepseek-ai/DeepSeek-V3/releases', got '{'/'.join(parts[:3])}'",
                    None,
                )
            valid_items += 1

        return (
            "valid",
            "content",
            f"Valid GitHub releases array for deepseek-ai/DeepSeek-V3 with {valid_items} releases.",
            valid_items,
        )

    def _validate_hf_models(self, body: bytes) -> Tuple[str, str, str, Optional[int]]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return "invalid_content", "envelope", "Failed to parse Hugging Face response as JSON.", None

        if isinstance(payload, dict):
            error_msg = str(payload.get("error", ""))
            return "invalid_content", "envelope", f"Hugging Face API returned error: {error_msg}", None

        if not isinstance(payload, list):
            return "invalid_content", "envelope", "Expected JSON array of models from Hugging Face API.", None

        if len(payload) == 0:
            return "valid", "content", "Valid empty Hugging Face models array (item_count=0).", 0

        valid_items = 0
        for item in payload:
            if not isinstance(item, dict):
                return "invalid_content", "content", "Model entry is not a dictionary.", None
            model_id = item.get("id")
            if not isinstance(model_id, str) or len(model_id.strip()) == 0:
                return "invalid_content", "content", f"Model entry missing valid non-empty string id: {model_id!r}", None
            if not model_id.startswith("deepseek-ai/"):
                return (
                    "invalid_content",
                    "content",
                    f"Model id does not match expected author 'deepseek-ai/': '{model_id}'",
                    None,
                )
            name_part = model_id.split("/", 1)[1]
            if not name_part or len(name_part.strip()) == 0:
                return (
                    "invalid_content",
                    "content",
                    f"Model id has empty name after author prefix: '{model_id}'",
                    None,
                )
            author = item.get("author")
            if author is not None and author != "deepseek-ai":
                return "invalid_content", "content", f"Model author '{author}' contradicts namespace 'deepseek-ai'", None
            valid_items += 1

        return (
            "valid",
            "content",
            f"Valid Hugging Face models array with {valid_items} models from author deepseek-ai.",
            valid_items,
        )

    def _validate_arena_html(self, body: bytes, content_type: Optional[str]) -> Tuple[str, str, str, Optional[int]]:
        raw_text = body.decode("utf-8", errors="replace")
        if "<html" not in raw_text.lower():
            return "invalid_content", "envelope", "Response is not an HTML document.", None

        return (
            "unverified",
            "envelope",
            "HTML document received (transport and envelope confirmed); leaderboard table extraction not verified in R1.",
            None,
        )

    _validate_arena_overall = _validate_arena_html

    def probe_single(
        self,
        source_cfg: Dict[str, Any],
        capture: Optional[CaptureResult] = None,
    ) -> Dict[str, Any]:
        """Execute a single bounded probe for a source configuration.

        If capture is not provided, executes a single fetch using capture_source().
        """
        source_id = source_cfg["source_id"]
        initial_url = source_cfg["url"]

        if capture is not None:
            if capture.source_id != source_id or capture.initial_url != initial_url:
                raise ValueError(
                    f"Capture mismatch: capture is for ({capture.source_id}, {capture.initial_url}) "
                    f"but source_cfg specifies ({source_id}, {initial_url})"
                )
        else:
            capture = self.capture_source(source_id, initial_url)

        status, validation_level, reason, item_count = self.validate_content(
            source_id=source_id,
            http_status=capture.status_code,
            content_type=capture.content_type,
            body=capture.body,
            limit_exceeded=capture.limit_exceeded,
            error_msg=capture.error_msg,
        )

        # Invariant: SHA-256 of complete body computed ONLY after successful EOF without error or overflow
        if (
            capture.limit_exceeded
            or capture.error_msg is not None
            or capture.status_code is None
            or capture.status_code >= 400
            or status != "valid"
            or not capture.complete
        ):
            body_sha256 = None
        else:
            body_sha256 = hashlib.sha256(capture.body).hexdigest()

        return {
            "source_id": source_id,
            "platform": source_cfg.get("platform"),
            "family": source_cfg.get("family"),
            "initial_url": initial_url,
            "final_url": capture.final_url,
            "http_status": capture.status_code,
            "fetch_started_at": capture.fetch_started_at,
            "fetch_completed_at": capture.fetch_completed_at,
            "duration_ms": round(capture.duration_ms, 2),
            "bytes_received": capture.bytes_received,
            "bytes_stored": capture.bytes_stored,
            "content_type": capture.content_type,
            "body_sha256": body_sha256,
            "status": status,
            "validation_level": validation_level,
            "reason": reason,
            "item_count": item_count,
            "published_at": None,  # Strictly null in R1/R2 probe
            "checker_version": PROBE_VERSION,
            "usable_for_forecast": False,  # Strict invariant
        }

    def run(self) -> Dict[str, Any]:
        """Run sequential probes across all registered sources and compile report."""
        started_at = utc_now_iso()

        try:
            sources = self.load_registry()
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            logger.error("Registry validation failed: %s", exc)
            return {
                "probe_run_version": PROBE_VERSION,
                "started_at": started_at,
                "completed_at": utc_now_iso(),
                "total_sources": 0,
                "status_counts": {
                    "valid": 0,
                    "blocked": 0,
                    "invalid_content": 0,
                    "network_error": 0,
                    "unverified": 0,
                },
                "summary_status": "INVALID_REGISTRY",
                "all_healthy": False,
                "usable_for_forecast_all": False,
                "registry_error": str(exc),
                "results": [],
            }

        results: List[Dict[str, Any]] = []
        status_counts = {
            "valid": 0,
            "blocked": 0,
            "invalid_content": 0,
            "network_error": 0,
            "unverified": 0,
        }

        for cfg in sources:
            source_id = cfg.get("source_id", "unknown")
            try:
                res = self.probe_single(cfg)
            except Exception as exc:
                logger.exception("Unexpected exception probing %s", source_id)
                res = {
                    "source_id": source_id,
                    "platform": cfg.get("platform"),
                    "family": cfg.get("family"),
                    "initial_url": cfg.get("url"),
                    "final_url": cfg.get("url"),
                    "http_status": None,
                    "fetch_started_at": utc_now_iso(),
                    "fetch_completed_at": utc_now_iso(),
                    "duration_ms": 0.0,
                    "bytes_received": None,
                    "bytes_stored": None,
                    "content_type": None,
                    "body_sha256": None,
                    "status": "network_error",
                    "validation_level": "transport",
                    "reason": f"Unhandled probe exception: {type(exc).__name__}: {str(exc)}",
                    "item_count": None,
                    "published_at": None,
                    "checker_version": PROBE_VERSION,
                    "usable_for_forecast": False,
                }
            results.append(res)
            st = res.get("status", "network_error")
            status_counts[st] = status_counts.get(st, 0) + 1

        completed_at = utc_now_iso()

        # Probe health summary: requires all canonical sources present AND all valid
        all_valid = (status_counts["valid"] == len(CANONICAL_SOURCES)) and (len(sources) == len(CANONICAL_SOURCES))
        summary_status = "ALL_VALID" if all_valid else "PARTIAL_REACHABLE"

        return {
            "probe_run_version": PROBE_VERSION,
            "started_at": started_at,
            "completed_at": completed_at,
            "total_sources": len(sources),
            "status_counts": status_counts,
            "summary_status": summary_status,
            "all_healthy": all_valid,
            "usable_for_forecast_all": False,
            "results": results,
        }


def format_table(results: List[Dict[str, Any]]) -> str:
    """Format probe results as a readable markdown/text table."""
    headers = ["source_id", "HTTP", "Recv", "Stored", "ms", "Status", "Level", "Items", "Reason"]
    rows = []
    for r in results:
        rows.append([
            r["source_id"],
            str(r["http_status"]) if r["http_status"] is not None else "ERR",
            str(r.get("bytes_received")) if r.get("bytes_received") is not None else "null",
            str(r.get("bytes_stored")) if r.get("bytes_stored") is not None else "null",
            str(int(r["duration_ms"])),
            r["status"],
            r["validation_level"],
            str(r["item_count"]) if r["item_count"] is not None else "—",
            r["reason"][:40] + ("…" if len(r["reason"]) > 40 else ""),
        ])

    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(cell))

    lines = []
    header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_line = "-|-".join("-" * col_widths[i] for i in range(len(headers)))
    lines.append(f"| {header_line} |")
    lines.append(f"| {sep_line} |")
    for row in rows:
        row_line = " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row))
        lines.append(f"| {row_line} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pythia R1.2 source probe")
    parser.add_argument("--output", "-o", type=Path, help="Output path for probe JSON results", required=False)
    args = parser.parse_args()

    with SourceProbe() as probe:
        summary = probe.run()

    print("\n=== Pythia R1.3 Source Probe Summary ===")
    print(f"Total Sources: {summary['total_sources']}")
    print(f"Status Counts: {summary['status_counts']}")
    print(f"Summary Status: {summary['summary_status']} (all_healthy={summary['all_healthy']})")
    if summary.get("registry_error"):
        print(f"Registry Error: {summary['registry_error']}")
    print("\n" + format_table(summary["results"]) + "\n")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"Detailed JSON results written to {args.output}")

    if summary["summary_status"] == "INVALID_REGISTRY":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
