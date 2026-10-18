import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from python.evaluation.evidence_archive import ArchiveConflictError, ArchiveIntegrityError, EvidenceArchive
from python.evaluation.source_probe import CANONICAL_SOURCES, CaptureResult, SourceProbe


def capture(body=b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-16","percentRate":3.88}]}'):
    return CaptureResult(
        source_id="nyfed_effr",
        initial_url=CANONICAL_SOURCES["nyfed_effr"],
        final_url=CANONICAL_SOURCES["nyfed_effr"],
        body=body,
        status_code=200,
        headers={"content-type": "application/json", "etag": "v1"},
        fetch_started_at="2026-10-18T05:00:00.000000Z",
        fetch_completed_at="2026-10-18T05:00:01.000000Z",
        duration_ms=1000.0,
        bytes_received=len(body),
        bytes_stored=len(body),
        complete=True,
        limit_exceeded=False,
        error_msg=None,
        collector_version="review-capture/1",
    )


def run_all_controls():
    results = {}
    previous_checks = []
    r21_defect_checks = []

    with tempfile.TemporaryDirectory(prefix="pythia-r22-controls-") as folder:
        directory = Path(folder)
        cap = capture()

        # PART 1: 10 Previous Controls (R2.0 / R2.1 invariants)
        archive = EvidenceArchive(directory / "idempotency")
        original = archive.record_capture(cap, receipt_id="stable-id")
        replay = archive.record_capture(cap, receipt_id="stable-id")
        previous_checks.append({"case": "identical_payload_positive", "meets_requirement": replay == original})

        for name, changed in [
            ("changed_header", replace(cap, headers={**cap.headers, "etag": "v2"})),
            ("changed_fetch_time", replace(cap, fetch_completed_at="2026-10-18T05:00:02.000000Z")),
            ("changed_collector_version", replace(cap, collector_version="review-capture/2")),
        ]:
            rejected = False
            try:
                archive.record_capture(changed, receipt_id="stable-id")
            except ArchiveConflictError:
                rejected = True
            previous_checks.append({
                "case": name,
                "expected": "ArchiveConflictError",
                "actual": "rejected" if rejected else "accepted as replay",
                "meets_requirement": rejected,
                "defect_reproduced": not rejected,
            })

        negative = replace(
            cap,
            body=b"",
            status_code=None,
            final_url=None,
            headers={},
            bytes_received=None,
            bytes_stored=None,
            complete=False,
            error_msg="timeout",
        )
        archive.record_capture(negative, receipt_id="negative-id")
        rejected = False
        try:
            archive.record_capture(replace(negative, error_msg="resolver failed"), receipt_id="negative-id")
        except ArchiveConflictError:
            rejected = True
        previous_checks.append({
            "case": "changed_negative_error",
            "expected": "ArchiveConflictError",
            "actual": "rejected" if rejected else "accepted as replay",
            "meets_requirement": rejected,
            "defect_reproduced": not rejected,
        })

        cas = EvidenceArchive(directory / "cas-integrity")
        first = cas.record_capture(cap, receipt_id="before-corruption")
        blob_path = cas._blob_path(first.blob_sha256)
        wrong = bytes([cap.body[0] ^ 1]) + cap.body[1:]
        blob_path.write_bytes(wrong)
        committed = False
        error = None
        try:
            cas.record_capture(cap, receipt_id="after-corruption")
            committed = True
        except Exception as exc:
            error = type(exc).__name__
        integrity = cas.verify_integrity()
        previous_checks.append({
            "case": "existing_blob_same_size_wrong_hash",
            "original_hash": first.blob_sha256,
            "fixture_corrupted_hash": hashlib.sha256(wrong).hexdigest(),
            "same_size": len(wrong) == len(cap.body),
            "new_receipt_committed": committed,
            "exception": error,
            "total_receipts": len(cas.query_receipts()),
            "verify_healthy": integrity["healthy"],
            "meets_requirement": not committed and not integrity["healthy"] and error == "ArchiveIntegrityError",
            "defect_reproduced": committed,
        })

        sid = "github_deepseek_v3_releases"
        body = b"[]"
        full = {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body_b64": base64.b64encode(body).decode("ascii"),
            "bytes_received": 2,
            "bytes_stored": 2,
            "complete": True,
            "limit_exceeded": False,
            "final_url": CANONICAL_SOURCES[sid],
            "error_msg": None,
        }
        malformed = [
            ("missing_explicit_eof", {k: v for k, v in full.items() if k != "complete"}),
            ("invalid_base64_ignored_suffix", dict(full, body_b64="W10=!!")),
            ("contradictory_complete_counters", dict(full, bytes_received=3)),
            ("missing_required_body_metadata", {"complete": True}),
        ]
        for name, payload in malformed:
            worker = directory / (name + ".py")
            worker.write_text("import json,sys\nsys.stdin.read()\nprint(" + repr(json.dumps(payload)) + ")\n")
            with SourceProbe(worker_cmd=[sys.executable, str(worker)]) as probe:
                captured = probe.capture_source(sid, CANONICAL_SOURCES[sid])
                diagnostic = probe.probe_single({"source_id": sid, "url": CANONICAL_SOURCES[sid]}, capture=captured)
            target = EvidenceArchive(directory / name)
            receipt = target.record_capture(captured, receipt_id=name, probe_result=diagnostic)
            accepted_full = receipt.blob_sha256 is not None and receipt.revision_id is not None
            previous_checks.append({
                "case": name,
                "capture_complete": captured.complete,
                "capture_error": captured.error_msg,
                "probe_status": diagnostic["status"],
                "full_blob_hash": receipt.blob_sha256,
                "revision_created": receipt.revision_id is not None,
                "meets_requirement": not accepted_full,
                "defect_reproduced": accepted_full,
            })

        # PART 2: 5 Reviewer Defects (C1, C2, C3 Invariants)
        # 1. Ambient test routing cannot activate on default worker
        hits = []
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        env_key = "PYTHIA_TEST_LOOPBACK_MAP"
        prev = os.environ.get(env_key)
        os.environ[env_key] = json.dumps({"https://markets.newyorkfed.org": f"http://127.0.0.1:{srv.server_port}"})
        try:
            with SourceProbe(timeout=0.10) as probe:
                res = probe.capture_source("nyfed_effr", CANONICAL_SOURCES["nyfed_effr"])
            r21_defect_checks.append({
                "case": "default_worker_ambient_test_route",
                "loopback_gets": len(hits),
                "ambient_route_activated": len(hits) > 0,
                "defect_forbidden": len(hits) == 0,
            })
        finally:
            if prev is None: os.environ.pop(env_key, None)
            else: os.environ[env_key] = prev
            srv.shutdown(); srv.server_close()

        # 2. Fresh prefix directory barriers
        arch2 = EvidenceArchive(directory / "fresh-dirs")
        calls = []
        real_fsync = os.fsync
        def trace_fsync(fd):
            target = Path(os.readlink(f"/proc/self/fd/{fd}"))
            calls.append({"directory": stat.S_ISDIR(os.fstat(fd).st_mode), "path": str(target.relative_to(arch2.state_dir))})
            return real_fsync(fd)
        os.fsync = trace_fsync
        try:
            rec2 = arch2.record_capture(cap, receipt_id="fresh")
        finally:
            os.fsync = real_fsync
        b2 = arch2._blob_path(rec2.blob_sha256)
        req2 = {str(p.relative_to(arch2.state_dir)) for p in (b2.parent, b2.parent.parent, arch2.blobs_dir)}
        synced2 = {x["path"] for x in calls if x["directory"]}
        r21_defect_checks.append({
            "case": "fresh_prefix_directory_barriers",
            "required_directory_paths": sorted(req2),
            "synced_directory_paths": sorted(synced2),
            "missing_directory_barriers": sorted(req2 - synced2),
            "defect_forbidden": len(req2 - synced2) == 0,
        })

        # 3. Publisher delayed after link - second writer must execute directory fsync
        arch3 = EvidenceArchive(directory / "publish-race")
        pub_done = threading.Event(); rel = threading.Event()
        real_link = os.link; events = []; errs = []
        def del_link(src, dst, *a, **kw):
            v = real_link(src, dst, *a, **kw)
            if threading.current_thread().name == "pub":
                pub_done.set()
                rel.wait(timeout=10)
            return v
        def race_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                events.append(str(Path(os.readlink(f"/proc/self/fd/{fd}"))))
            return real_fsync(fd)
        os.link = del_link; os.fsync = race_fsync
        w = threading.Thread(target=lambda: arch3.record_capture(cap, receipt_id="pub"), name="pub")
        w.start()
        try:
            assert pub_done.wait(timeout=5)
            sec = arch3.record_capture(cap, receipt_id="sec")
            r21_defect_checks.append({
                "case": "reuse_before_publisher_directory_sync",
                "second_receipt_committed": sec.seq >= 1,
                "directory_fsync_count_before_second_commit": len(events),
                "defect_forbidden": sec.seq >= 1 and len(events) >= 3,
            })
        finally:
            rel.set(); w.join(timeout=5)
            os.link = real_link; os.fsync = real_fsync

        # 4. Direct header type collision
        fixed_diag = {"status": "valid", "validation_level": "content", "reason": "fixture", "checker_version": "review/1"}
        arch4 = EvidenceArchive(directory / "header-type")
        int_header = replace(cap, headers={"etag": 1})
        exc4 = None
        try:
            arch4.record_capture(int_header, receipt_id="header", probe_result=fixed_diag)
        except Exception as e:
            exc4 = type(e).__name__
        r21_defect_checks.append({
            "case": "direct_header_type_collision",
            "rejected_with_value_error": exc4 == "ValueError",
            "exception": exc4,
            "defect_forbidden": exc4 == "ValueError",
        })

        # 5. Direct final_url integer
        arch5 = EvidenceArchive(directory / "final-url-type")
        int_url = replace(cap, final_url=123)
        exc5 = None
        try:
            arch5.record_capture(int_url, receipt_id="bad-url", probe_result=fixed_diag)
        except Exception as e:
            exc5 = type(e).__name__
        r21_defect_checks.append({
            "case": "direct_final_url_integer",
            "rejected_with_value_error": exc5 == "ValueError",
            "exception": exc5,
            "defect_forbidden": exc5 == "ValueError",
        })

    all_prev = all(x["meets_requirement"] for x in previous_checks)
    all_r21 = all(x["defect_forbidden"] for x in r21_defect_checks)
    summary = {
        "reviewed_commit_baseline": "829a2b17e3f0077fb4e860cc27865b044dbe467c",
        "utc": datetime.now(timezone.utc).isoformat(),
        "total_controls": len(previous_checks) + len(r21_defect_checks),
        "previous_controls_count": len(previous_checks),
        "r21_defect_checks_count": len(r21_defect_checks),
        "all_previous_requirements_met": all_prev,
        "all_r21_defects_forbidden": all_r21,
        "all_controls_passed": all_prev and all_r21,
        "previous_controls": previous_checks,
        "r21_defect_checks": r21_defect_checks,
    }
    return summary


if __name__ == "__main__":
    summary = run_all_controls()
    output_path = Path("/home/pythia/pythia-evidence-r2/docs/research/2026-10-18/r2/revision2/reviewer_controls_result.json")
    output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    assert summary["all_controls_passed"], "Not all controls passed!"
