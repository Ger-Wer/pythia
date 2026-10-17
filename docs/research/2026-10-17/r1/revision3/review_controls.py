"""Reviewer regression controls runner for Pythia R1.3.

Re-verifies the 5 specific architectural review controls on amper:
1. BLS Employment Situation feed is rejected for CPI (content identity).
2. Fed Banking Applications feed is rejected for Monetary Policy (content identity).
3. Truncated XML is rejected at envelope parse time.
4. Real requests loopback drip is interrupted promptly by supervisor deadline.
5. Blocking DNS resolver in worker process is interrupted promptly by supervisor deadline.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from python.evaluation.source_probe import CANONICAL_SOURCES, SourceProbe


def run_review_controls() -> dict:
    probe = SourceProbe()

    results = {
        "baseline_commit": "e92e12912e74c673d187d89273c129fc407a8682",
        "target_branch": "codex/evidence-r1-sources",
        "python_version": sys.version.split()[0],
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": [],
    }

    # 1. BLS Employment Situation is not CPI
    empsit_feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        '<title>Bureau of Labor Statistics</title>'
        '<link>https://www.bls.gov/</link>'
        '<item><title>Employment Situation</title>'
        '<link>https://www.bls.gov/news.release/empsit.nr0.htm</link>'
        '</item></channel></rss>'
    ).encode()
    st1, lvl1, rsn1, cnt1 = probe.validate_content(
        source_id="bls_cpi_rss",
        http_status=200,
        content_type="application/rss+xml",
        body=empsit_feed,
        limit_exceeded=False,
        error_msg=None,
    )
    results["checks"].append({
        "case": "bls_employment_feed_is_not_cpi",
        "status": st1,
        "validation_level": lvl1,
        "reason": rsn1,
        "item_count": cnt1,
        "expected": "invalid_content",
        "meets_expectation": st1 == "invalid_content" and lvl1 == "content",
    })

    # 2. Fed Bank Orders are not Monetary Policy
    orders_feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        '<title>FRB: Press Release - Banking Applications</title>'
        '<link>https://www.federalreserve.gov/</link>'
        '<item><title>Banking order</title>'
        '<link>https://www.federalreserve.gov/newsevents/pressreleases/orders20261016a.htm</link>'
        '</item></channel></rss>'
    ).encode()
    st2, lvl2, rsn2, cnt2 = probe.validate_content(
        source_id="fed_monetary_rss",
        http_status=200,
        content_type="application/rss+xml",
        body=orders_feed,
        limit_exceeded=False,
        error_msg=None,
    )
    results["checks"].append({
        "case": "fed_bank_orders_are_not_monetary_policy",
        "status": st2,
        "validation_level": lvl2,
        "reason": rsn2,
        "item_count": cnt2,
        "expected": "invalid_content",
        "meets_expectation": st2 == "invalid_content" and lvl2 == "content",
    })

    # 3. Truncated RSS XML
    valid_feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        '<title>Federal Reserve Press Releases - Monetary Policy</title>'
        '<link>https://www.federalreserve.gov/</link>'
        '<item><title>FOMC statement</title>'
        '<link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20261016a.htm</link>'
        '</item></channel></rss>'
    ).encode()
    truncated_xml = valid_feed.removesuffix(b"</channel></rss>")
    st3, lvl3, rsn3, cnt3 = probe.validate_content(
        source_id="fed_monetary_rss",
        http_status=200,
        content_type="application/rss+xml",
        body=truncated_xml,
        limit_exceeded=False,
        error_msg=None,
    )
    results["checks"].append({
        "case": "truncated_rss_xml",
        "status": st3,
        "validation_level": lvl3,
        "reason": rsn3,
        "item_count": cnt3,
        "expected": "invalid_content",
        "meets_expectation": st3 == "invalid_content" and lvl3 == "envelope",
    })

    # 4. Real requests loopback drip interrupted by deadline
    body_data = b"x" * 120
    with tempfile.TemporaryDirectory() as td:
        marker_file = Path(td) / "server_marker.json"

        class DripHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                bytes_sent = 0
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body_data)))
                self.end_headers()
                try:
                    for b in body_data:
                        self.wfile.write(bytes([b]))
                        self.wfile.flush()
                        bytes_sent += 1
                        marker_file.write_text(json.dumps({"received": True, "bytes_sent": bytes_sent}))
                        time.sleep(0.025)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), DripHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        server_port = server.server_address[1]

        drip_worker_script = Path(td) / "drip_worker.py"
        drip_worker_code = f"""import sys, json, time, requests

raw = sys.stdin.read()
resp = requests.get("http://127.0.0.1:{server_port}/drip", stream=True, timeout=10.0)
chunks = []
for chunk in resp.iter_content(chunk_size=1):
    chunks.append(chunk)

out = {{
    "status_code": resp.status_code, "content_type": "application/json",
    "body_b64": "", "bytes_received": len(b"".join(chunks)),
    "bytes_stored": len(b"".join(chunks)), "limit_exceeded": False,
    "final_url": "http://127.0.0.1:{server_port}/drip", "error_msg": None
}}
print(json.dumps(out))
"""
        drip_worker_script.write_text(drip_worker_code)

        budget_4 = 0.20
        with SourceProbe(timeout=budget_4, worker_cmd=[sys.executable, str(drip_worker_script)]) as drip_probe:
            cfg4 = {"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}
            t0 = time.monotonic()
            res4 = drip_probe.probe_single(cfg4)
            elapsed_4 = time.monotonic() - t0

        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)

        server_state = json.loads(marker_file.read_text()) if marker_file.is_file() else {}
        meets_4 = (
            res4["status"] == "network_error"
            and elapsed_4 <= budget_4 + 0.35
            and res4["body_sha256"] is None
            and server_state.get("received") is True
            and server_state.get("bytes_sent", 0) > 0
        )

        results["checks"].append({
            "case": "real_requests_loopback_drip_deadline",
            "budget_seconds": budget_4,
            "elapsed_seconds": round(elapsed_4, 4),
            "reported_ms": res4["duration_ms"],
            "status": res4["status"],
            "body_sha256": res4["body_sha256"],
            "bytes_received": res4["bytes_received"],
            "reason": res4["reason"],
            "server_received_request": server_state.get("received", False),
            "bytes_sent_before_cutoff": server_state.get("bytes_sent", 0),
            "external_http": False,
            "meets_expectation": bool(meets_4),
        })

    # 5. Blocking resolver in child interrupted by deadline
    with tempfile.TemporaryDirectory() as td:
        marker_file_5 = Path(td) / "resolver_marker.txt"
        resolver_worker_script = Path(td) / "resolver_worker.py"
        resolver_worker_code = f"""import sys, time, socket

raw = sys.stdin.read()
with open(r"{marker_file_5}", "w") as f:
    f.write("entered")

time.sleep(2.0)
raise socket.gaierror("synthetic resolver delay")
"""
        resolver_worker_script.write_text(resolver_worker_code)

        budget_5 = 0.20
        with SourceProbe(timeout=budget_5, worker_cmd=[sys.executable, str(resolver_worker_script)]) as res_probe:
            cfg5 = {"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}
            t0 = time.monotonic()
            res5 = res_probe.probe_single(cfg5)
            elapsed_5 = time.monotonic() - t0

        resolver_entered = marker_file_5.is_file() and marker_file_5.read_text() == "entered"
        meets_5 = (
            res5["status"] == "network_error"
            and elapsed_5 <= budget_5 + 0.35
            and res5["body_sha256"] is None
            and resolver_entered
        )

        results["checks"].append({
            "case": "blocking_resolver_deadline",
            "budget_seconds": budget_5,
            "elapsed_seconds": round(elapsed_5, 4),
            "reported_ms": res5["duration_ms"],
            "status": res5["status"],
            "reason": res5["reason"],
            "child_entered_resolver": resolver_entered,
            "external_dns": False,
            "meets_expectation": bool(meets_5),
        })

    results["all_checks_passed"] = all(c["meets_expectation"] for c in results["checks"])
    return results


def main() -> None:
    res = run_review_controls()
    output_json = json.dumps(res, indent=2)
    print(output_json)

    out_path = Path(__file__).parent / "review_controls.json"
    out_path.write_text(output_json + "\n", encoding="utf-8")
    print(f"\nWrote results to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
