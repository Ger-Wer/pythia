"""Pythia R2.1: Isolated worker process for single-source bounded fetch and probe.

Reads probe execution parameters as JSON from stdin, creates an isolated
anonymous requests Session, executes _fetch_bounded_core, and outputs
a single-line JSON result with base64-encoded body and response headers to stdout.
All logging and diagnostics are written strictly to stderr.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.parse
from typing import Any, Dict

import requests

from python.evaluation.source_probe import (
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    MAX_REDIRECTS,
    STREAM_LIMIT_BYTES,
    _fetch_bounded_core,
    create_anonymous_session,
)


class LoopbackRouteAdapter(requests.adapters.HTTPAdapter):
    """Testing adapter: routes requests for a canonical prefix to a loopback test server.

    Allows tests to verify _fetch_bounded_core and worker with gzip and single-GET
    without modifying the production domain allowlist or HTTPS scheme requirement.
    """

    def __init__(self, target_base: str) -> None:
        super().__init__()
        self.target_base = target_base.rstrip("/")

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        p = urllib.parse.urlparse(request.url)
        original_host = p.netloc
        request.url = self.target_base + p.path + (f"?{p.query}" if p.query else "")
        request.headers["Host"] = original_host
        return super().send(request, **kwargs)


def main() -> None:
    try:
        raw_input = sys.stdin.read()
        if not raw_input:
            sys.stderr.write("Worker error: empty input on stdin\n")
            sys.stderr.flush()
            sys.exit(1)

        params: Dict[str, Any] = json.loads(raw_input)
        source_id = params["source_id"]
        initial_url = params["initial_url"]
        timeout = float(params.get("timeout", DEFAULT_TIMEOUT_SECONDS))
        max_redirects = int(params.get("max_redirects", MAX_REDIRECTS))
        max_bytes = int(params.get("max_bytes", STREAM_LIMIT_BYTES))
        user_agent = str(params.get("user_agent", DEFAULT_USER_AGENT))
    except Exception as exc:
        sys.stderr.write(f"Worker parameter error: {type(exc).__name__}: {str(exc)}\n")
        sys.stderr.flush()
        sys.exit(1)

    sess = create_anonymous_session()

    # Route canonical URLs to loopback in test fixtures if requested
    loopback_map_str = os.environ.get("PYTHIA_TEST_LOOPBACK_MAP")
    if loopback_map_str:
        try:
            mapping = json.loads(loopback_map_str)
            for prefix, target_base in mapping.items():
                sess.mount(prefix, LoopbackRouteAdapter(target_base))
        except Exception as exc:
            sys.stderr.write(f"Worker loopback mount error: {type(exc).__name__}: {str(exc)}\n")

    try:
        res = _fetch_bounded_core(
            sess=sess,
            source_id=source_id,
            initial_url=initial_url,
            timeout=timeout,
            max_redirects=max_redirects,
            max_bytes=max_bytes,
            user_agent=user_agent,
        )

        body_bytes = res.get("body", b"")
        body_b64 = base64.b64encode(body_bytes).decode("ascii")

        output = {
            "status_code": res.get("status_code"),
            "headers": res.get("headers", {}),
            "content_type": res.get("content_type"),
            "body_b64": body_b64,
            "bytes_received": res.get("bytes_received"),
            "bytes_stored": res.get("bytes_stored"),
            "complete": bool(res.get("complete", False)),
            "limit_exceeded": bool(res.get("limit_exceeded", False)),
            "final_url": res.get("final_url"),
            "error_msg": res.get("error_msg"),
        }

        sys.stdout.write(json.dumps(output) + "\n")
        sys.stdout.flush()
    except Exception as exc:
        sys.stderr.write(f"Worker fetch exception: {type(exc).__name__}: {str(exc)}\n")
        sys.stderr.flush()
        sys.exit(2)
    finally:
        try:
            sess.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
