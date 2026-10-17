"""Reviewer and author verification control for parent IPC deadline under R1.3.

Runs on amper to verify that subprocess.Popen + communicate(timeout=remaining)
strictly interrupts partial/stalled IPC messages, accepts 2 MiB messages cleanly,
rejects late messages, and reaps worker processes within the 0.50s cleanup bound.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from python.evaluation.source_probe import CANONICAL_SOURCES, STREAM_LIMIT_BYTES, SourceProbe


def run_ipc_controls() -> dict:
    results = {
        "baseline_commit": "e92e12912e74c673d187d89273c129fc407a8682",
        "target_branch": "codex/evidence-r1-sources",
        "python_version": sys.version.split()[0],
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "controls": [],
    }

    # 1. Partial IPC message stalls after ready header (the exact P2 defect from R1.2 review)
    budget_1 = 0.20
    cleanup_allowance_1 = 0.50
    with tempfile.TemporaryDirectory() as td:
        marker_file = Path(td) / "partial_ipc_marker.txt"
        worker_file = Path(td) / "delayed_worker.py"
        worker_code = f"""import sys, time

raw = sys.stdin.read()
# Write partial JSON prefix and flush
sys.stdout.write('{{"status_code": 200, "content_type": "application/json", "body_b64": "')
sys.stdout.flush()

with open(r"{marker_file}", "w") as f:
    f.write("partial_sent")

# Sleep significantly longer than parent budget (1.0s vs 0.20s)
time.sleep(1.0)
sys.stdout.write('some_remaining_bytes"}}\\n')
sys.stdout.flush()
"""
        worker_file.write_text(worker_code)

        cfg1 = {"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}
        t0 = time.monotonic()
        with SourceProbe(timeout=budget_1, worker_cmd=[sys.executable, str(worker_file)]) as probe:
            res1 = probe.probe_single(cfg1)
        elapsed_1 = time.monotonic() - t0

        marker_confirmed = marker_file.is_file() and marker_file.read_text() == "partial_sent"
        meets_1 = (
            res1["status"] == "network_error"
            and elapsed_1 <= budget_1 + cleanup_allowance_1
            and res1["body_sha256"] is None
            and res1["bytes_received"] is None
            and marker_confirmed
        )

        results["controls"].append({
            "control": "partial_ipc_message_stalls_after_ready_header",
            "budget_seconds": budget_1,
            "cleanup_allowance_seconds": cleanup_allowance_1,
            "elapsed_seconds": round(elapsed_1, 4),
            "status": res1["status"],
            "validation_level": res1["validation_level"],
            "reported_duration_ms": res1["duration_ms"],
            "body_sha256": res1["body_sha256"],
            "bytes_received": res1["bytes_received"],
            "bytes_stored": res1["bytes_stored"],
            "http_status": res1["http_status"],
            "reason": res1["reason"],
            "marker_confirmed": marker_confirmed,
            "expected": "network_error within budget + cleanup, null hash/counters, worker reaped",
            "meets_expectation": bool(meets_1),
            "external_network": False,
        })

    # 2. Full 2 MiB message transfer through IPC pipe without deadlock
    body_2 = b" " * (2 * 1024 * 1024 - 1000) + json.dumps({
        "refRates": [{"type": "EFFR", "effectiveDate": "2026-10-16", "percentRate": 3.88}]
    }).encode("utf-8")
    assert len(body_2) <= STREAM_LIMIT_BYTES
    expected_hash_2 = hashlib.sha256(body_2).hexdigest()

    with tempfile.TemporaryDirectory() as td:
        worker_file_2 = Path(td) / "large_worker.py"
        worker_code_2 = """import sys, json, base64

raw = sys.stdin.read()
params = json.loads(raw)
body = b" " * (2 * 1024 * 1024 - 1000) + json.dumps({
    "refRates": [{"type": "EFFR", "effectiveDate": "2026-10-16", "percentRate": 3.88}]
}).encode("utf-8")
out = {
    "status_code": 200,
    "content_type": "application/json",
    "body_b64": base64.b64encode(body).decode("ascii"),
    "bytes_received": len(body),
    "bytes_stored": len(body),
    "limit_exceeded": False,
    "final_url": params["initial_url"],
    "error_msg": None,
}
print(json.dumps(out))
"""
        worker_file_2.write_text(worker_code_2)

        cfg2 = {"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}
        t0 = time.monotonic()
        with SourceProbe(timeout=5.0, worker_cmd=[sys.executable, str(worker_file_2)]) as probe:
            res2 = probe.probe_single(cfg2)
        elapsed_2 = time.monotonic() - t0

        meets_2 = (
            res2["status"] == "valid"
            and res2["http_status"] == 200
            and res2["body_sha256"] == expected_hash_2
            and res2["bytes_stored"] == len(body_2)
            and elapsed_2 < 1.0
        )

        results["controls"].append({
            "control": "full_2mib_message_through_ipc_pipe",
            "budget_seconds": 5.0,
            "elapsed_seconds": round(elapsed_2, 4),
            "status": res2["status"],
            "validation_level": res2["validation_level"],
            "reported_duration_ms": res2["duration_ms"],
            "bytes_stored": res2["bytes_stored"],
            "body_sha256": res2["body_sha256"],
            "sha256_match": res2["body_sha256"] == expected_hash_2,
            "pipe_deadlock_detected": False,
            "expected": "valid, full ~2 MiB payload parsed, sha256 match, no deadlock",
            "meets_expectation": bool(meets_2),
            "external_network": False,
        })

    # 3. Fast small message transfer through IPC pipe
    with tempfile.TemporaryDirectory() as td:
        worker_file_3 = Path(td) / "small_worker.py"
        worker_code_3 = """import sys, json, base64

raw = sys.stdin.read()
params = json.loads(raw)
body = json.dumps({
    "refRates": [{"type": "EFFR", "effectiveDate": "2026-10-16", "percentRate": 3.88}]
}).encode("utf-8")
out = {
    "status_code": 200,
    "content_type": "application/json",
    "body_b64": base64.b64encode(body).decode("ascii"),
    "bytes_received": len(body),
    "bytes_stored": len(body),
    "limit_exceeded": False,
    "final_url": params["initial_url"],
    "error_msg": None,
}
print(json.dumps(out))
"""
        worker_file_3.write_text(worker_code_3)

        cfg3 = {"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}
        t0 = time.monotonic()
        with SourceProbe(timeout=2.0, worker_cmd=[sys.executable, str(worker_file_3)]) as probe:
            res3 = probe.probe_single(cfg3)
        elapsed_3 = time.monotonic() - t0

        meets_3 = (
            res3["status"] == "valid"
            and res3["http_status"] == 200
            and res3["body_sha256"] is not None
            and elapsed_3 < 0.5
        )

        results["controls"].append({
            "control": "small_fast_message_through_ipc_pipe",
            "budget_seconds": 2.0,
            "elapsed_seconds": round(elapsed_3, 4),
            "status": res3["status"],
            "validation_level": res3["validation_level"],
            "reported_duration_ms": res3["duration_ms"],
            "body_sha256": res3["body_sha256"],
            "expected": "valid, quick execution, correct duration",
            "meets_expectation": bool(meets_3),
            "external_network": False,
        })

    # 4. Late message rejected by parent deadline re-check
    budget_4 = 0.20
    with tempfile.TemporaryDirectory() as td:
        worker_file_4 = Path(td) / "late_worker.py"
        worker_code_4 = """import sys, json, time, base64

raw = sys.stdin.read()
# Sleep past the parent deadline
time.sleep(0.25)
body = json.dumps({
    "refRates": [{"type": "EFFR", "effectiveDate": "2026-10-16", "percentRate": 3.88}]
}).encode("utf-8")
out = {
    "status_code": 200,
    "content_type": "application/json",
    "body_b64": base64.b64encode(body).decode("ascii"),
    "bytes_received": len(body),
    "bytes_stored": len(body),
    "limit_exceeded": False,
    "final_url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json",
    "error_msg": None,
}
print(json.dumps(out))
"""
        worker_file_4.write_text(worker_code_4)

        cfg4 = {"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}
        t0 = time.monotonic()
        with SourceProbe(timeout=budget_4, worker_cmd=[sys.executable, str(worker_file_4)]) as probe:
            res4 = probe.probe_single(cfg4)
        elapsed_4 = time.monotonic() - t0

        meets_4 = (
            res4["status"] == "network_error"
            and res4["body_sha256"] is None
            and "deadline exceeded" in res4["reason"].lower()
        )

        results["controls"].append({
            "control": "late_message_rejected_by_deadline_recheck",
            "budget_seconds": budget_4,
            "elapsed_seconds": round(elapsed_4, 4),
            "status": res4["status"],
            "reported_duration_ms": res4["duration_ms"],
            "body_sha256": res4["body_sha256"],
            "reason": res4["reason"],
            "expected": "network_error, null hash, deadline re-check catches late message",
            "meets_expectation": bool(meets_4),
            "external_network": False,
        })

    results["all_controls_passed"] = all(c["meets_expectation"] for c in results["controls"])
    return results


def main() -> None:
    res = run_ipc_controls()
    output_json = json.dumps(res, indent=2)
    print(output_json)

    out_path = Path(__file__).parent / "ipc_control.json"
    out_path.write_text(output_json + "\n", encoding="utf-8")
    print(f"\nWrote results to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
