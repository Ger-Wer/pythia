"""R2.4 Reviewer Controls Execution.

Executes only on amper from the R2 checkout.
Repeats submitted R2.3 controls without __main__ to prevent overwriting prior JSON artifacts,
and executes post-validation deadline controls alongside independent full-ancestry D2 checks.
"""
import base64
import hashlib
import json
import os
import runpy
import stat
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from python.evaluation import source_probe as sp
from python.evaluation.evidence_archive import EvidenceArchive


def run_all_controls():
    submitted = runpy.run_path(
        "docs/research/2026-10-18/r2/revision3/reviewer_controls.py",
        run_name="review_no_main",
    )
    previous = submitted["run_all_controls"]()
    cap = submitted["capture"]()
    checks = []

    with tempfile.TemporaryDirectory(prefix="pythia-r24-controls-") as folder:
        root = Path(folder)
        sid = "github_deepseek_v3_releases"
        payload = {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body_b64": base64.b64encode(b"[]").decode(),
            "bytes_received": 2,
            "bytes_stored": 2,
            "complete": True,
            "limit_exceeded": False,
            "final_url": sp.CANONICAL_SOURCES[sid],
            "error_msg": None,
        }
        worker = root / "valid_worker.py"
        worker.write_text("import sys\nsys.stdin.read()\nprint(" + repr(json.dumps(payload)) + ")\n")

        with sp.SourceProbe(timeout=0.5, worker_cmd=[sys.executable, str(worker)]) as probe:
            positive = probe.capture_source(sid, sp.CANONICAL_SOURCES[sid])
        assert positive.complete, positive.error_msg

        # Post-validation deadline check with injected delay
        validator = sp.validate_capture_result
        calls = []

        def delayed_validation(candidate):
            validator(candidate)
            calls.append("real_shared_validator_completed")
            time.sleep(0.8)

        sp.validate_capture_result = delayed_validation
        started = time.monotonic()
        try:
            with sp.SourceProbe(timeout=0.5, worker_cmd=[sys.executable, str(worker)]) as probe:
                expired = probe.capture_source(sid, sp.CANONICAL_SOURCES[sid])
        finally:
            sp.validate_capture_result = validator
        wall = time.monotonic() - started

        receipt = EvidenceArchive(root / "deadline").record_capture(expired, receipt_id="expired")
        deadline_check = {
            "case": "deadline_crossed_inside_shared_validation",
            "budget_seconds": 0.5,
            "injected_validation_delay_seconds": 0.8,
            "wall_seconds": wall,
            "validator_calls": calls,
            "capture_complete": expired.complete,
            "capture_error": expired.error_msg,
            "reported_duration_ms": expired.duration_ms,
            "receipt_status": receipt.status,
            "full_revision_created": receipt.revision_id is not None,
            "full_blob_hash": receipt.blob_sha256,
            "requirement_met": not expired.complete and receipt.revision_id is None,
            "limitation": "Controlled parent validation delay, not a claim about normal validation speed or a need to preempt validator execution.",
        }

        # Fresh / reuse / error-retry: assert full absolute ancestry BEFORE DB commit.
        archive = EvidenceArchive(root / "new-base" / "deep" / "state")
        digest = hashlib.sha256(cap.body).hexdigest()
        blob = archive._blob_path(digest)
        required = {str(p) for p in blob.parents}
        real_fsync = os.fsync
        traces = []

        def trace(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                traces.append(os.readlink(f"/proc/self/fd/{fd}"))
            return real_fsync(fd)

        for name in ("fresh", "reuse"):
            traces.clear()
            snapshots = []

            def hook(stage):
                if stage == "inside_db_tx_before_commit":
                    snapshots.append(set(traces))

            os.fsync = trace
            try:
                rec = archive.record_capture(cap, receipt_id=name, test_hook=hook)
            finally:
                os.fsync = real_fsync
            missing = sorted(required - snapshots[0])
            checks.append({
                "case": name + "_full_ancestry_before_commit",
                "seq": rec.seq,
                "required": sorted(required),
                "synced": sorted(snapshots[0]),
                "missing": missing,
                "passed": not missing,
            })

        failed = EvidenceArchive(root / "error-base" / "deep" / "state")

        def fail_parent(fd):
            if os.readlink(f"/proc/self/fd/{fd}") == str(failed.state_dir.parent):
                raise OSError("review-only state.parent fsync failure")
            return real_fsync(fd)

        raised = False
        os.fsync = fail_parent
        try:
            failed.record_capture(cap, receipt_id="failed")
        except OSError:
            raised = True
        finally:
            os.fsync = real_fsync

        empty = not failed.query_receipts() and not failed.query_revisions()
        traces.clear()
        os.fsync = trace
        try:
            retried = failed.record_capture(cap, receipt_id="retry")
        finally:
            os.fsync = real_fsync
        retry_required = {str(p) for p in failed._blob_path(digest).parents}
        missing = sorted(retry_required - set(traces))
        checks.append({
            "case": "state_parent_failure_and_retry",
            "error_raised": raised,
            "archive_empty_after_failure": empty,
            "retry_seq": retried.seq,
            "missing": missing,
            "passed": raised and empty and retried.seq == 1 and not missing,
        })

        # Pause publisher immediately after link; second writer sees existing blob.
        race = EvidenceArchive(root / "race-base" / "deep" / "state")
        linked = threading.Event()
        release = threading.Event()
        real_link = os.link
        publisher_errors = []

        def paused_link(src, dst, *args, **kwargs):
            answer = real_link(src, dst, *args, **kwargs)
            if threading.current_thread().name == "publisher":
                linked.set()
                if not release.wait(10):
                    raise RuntimeError("review fixture publisher timeout")
            return answer

        def publish():
            try:
                race.record_capture(cap, receipt_id="publisher")
            except BaseException as exc:
                publisher_errors.append(repr(exc))

        os.link = paused_link
        thread = threading.Thread(target=publish, name="publisher")
        thread.start()
        try:
            assert linked.wait(5), "publisher never linked"
            traces.clear()
            before_commit = []

            def race_hook(stage):
                if stage == "inside_db_tx_before_commit":
                    before_commit.append(set(traces))

            os.fsync = trace
            try:
                second = race.record_capture(cap, receipt_id="second", test_hook=race_hook)
            finally:
                os.fsync = real_fsync
            publisher_was_paused = thread.is_alive() and not release.is_set()
            race_required = {str(p) for p in race._blob_path(digest).parents}
            missing = sorted(race_required - before_commit[0])
        finally:
            release.set()
            thread.join(10)
            os.link = real_link

        checks.append({
            "case": "reuse_while_publisher_paused_full_ancestry",
            "second_seq": second.seq,
            "publisher_was_paused": publisher_was_paused,
            "publisher_errors": publisher_errors,
            "missing": missing,
            "passed": publisher_was_paused and not missing and not publisher_errors and not thread.is_alive(),
        })

    all_d2 = all(c["passed"] for c in checks)
    all_passed = previous["all_controls_passed"] and all_d2 and deadline_check["requirement_met"]
    result = {
        "reviewed_commit_baseline": "bd25e57e5a3af9d5761db6ad9327210172746da4",
        "utc": datetime.now(timezone.utc).isoformat(),
        "submitted_controls_repeated": previous,
        "submitted_controls_passed": previous["all_controls_passed"],
        "independent_d2_checks": checks,
        "all_d2_passed": all_d2,
        "deadline_check": deadline_check,
        "remaining_deadline_defect_reproduced": not deadline_check["requirement_met"],
        "all_controls_passed": all_passed,
    }
    return result


if __name__ == "__main__":
    result = run_all_controls()
    output_path = Path.cwd() / "docs/research/2026-10-18/r2/revision4/reviewer_controls_result.json"
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    assert result["all_controls_passed"], "Not all controls passed!"
