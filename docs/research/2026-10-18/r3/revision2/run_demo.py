"""Pythia R3.0.2: Revision 2 Offline Demonstration Script.

Runs offline demonstration on a safe temporary copy of live-r2-revision3:
1. Strict network guard (all socket connect/getaddrinfo blocked).
2. Read-only verification of all 32 original CAS blobs across all 4 live archives before.
3. WAL-safe copy of live-r2-revision3 via SQLite backup API and blob tree copy.
4. Deterministic fact extraction & bundle publication for 3 explicit receipts (cutoff=8).
5. Comprehensive verification of all 35 proofs against CAS blobs (must be 35/35 valid).
6. Lineage and origin group verification (no independent_evidence_count, canonical NYFed URL, path preserved).
7. Read-only verification of all 32 original CAS blobs across all 4 live archives after.
8. Writes published bundle to docs/research/2026-10-18/r3/revision2/demo_dossier_bundle.json.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Strict network guard
def forbidden_network(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError("Zero-network guard: network access strictly forbidden during demo")

socket.socket.connect = forbidden_network
socket.socket.connect_ex = forbidden_network
socket.getaddrinfo = forbidden_network

# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(PROJECT_ROOT))

from python.evaluation.event_dossier import DOSSIER_VERSION, EventDescriptor, build_event_dossier
from python.evaluation.evidence_extract import EXTRACTOR_VERSION, FACT_SCHEMA_VERSION, ProofSpan
from python.evaluation.source_probe import CANONICAL_SOURCES

DATA_BASE = Path("/home/pythia/pythia-evidence-r2-data")
ARCHIVE_NAMES = ["live-r2-run1", "live-r2-revision1", "live-r2-revision2", "live-r2-revision3"]


def verify_original_archives() -> Dict[str, Any]:
    """Verify hashes and sizes of all 32 original blobs across the 4 live archives via read-only mode."""
    results = {}
    total_blobs = 0
    all_healthy = True

    for name in ARCHIVE_NAMES:
        arc_dir = DATA_BASE / name
        db_path = arc_dir / "archive.db"
        blobs_dir = arc_dir / "blobs"
        if not db_path.is_file():
            raise FileNotFoundError(f"Missing db: {db_path}")

        ro_uri = f"{db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(ro_uri, uri=True)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT receipt_id, seq, source_id, status, blob_sha256 FROM receipts ORDER BY seq ASC;")
            rows = cursor.fetchall()
            arc_results = []
            for rid, seq, sid, status, sha in rows:
                blob_info = {"receipt_id": rid, "seq": seq, "source_id": sid, "status": status, "blob_sha256": sha}
                if sha:
                    total_blobs += 1
                    bpath = blobs_dir / sha[:2] / sha[2:4] / f"{sha}.bin"
                    if not bpath.is_file():
                        blob_info["intact"] = False
                        all_healthy = False
                    else:
                        data = bpath.read_bytes()
                        actual_sha = hashlib.sha256(data).hexdigest()
                        blob_info["intact"] = (actual_sha == sha)
                        blob_info["size"] = len(data)
                        if not blob_info["intact"]:
                            all_healthy = False
                arc_results.append(blob_info)
            results[name] = arc_results
        finally:
            conn.close()

    return {
        "healthy": all_healthy,
        "total_blobs": total_blobs,
        "archives": results,
    }


def snapshot_signature(state: Path) -> str:
    conn = sqlite3.connect(f"{(state / 'archive.db').resolve().as_uri()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT * FROM receipts ORDER BY seq").fetchall()
        revs = conn.execute("SELECT * FROM revisions ORDER BY first_seen_seq").fetchall()
    finally:
        conn.close()
    blobs = {str(p.relative_to(state)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (state / "blobs").rglob("*.bin")}
    return hashlib.sha256(json.dumps([rows, revs, blobs], sort_keys=True).encode()).hexdigest()


def run_demo() -> Dict[str, Any]:
    print("=" * 70)
    print("PYTHIA R3.0.2 OFFLINE DEMONSTRATION RUNNER (REVISION 2)")
    print("=" * 70)

    # 1. Verify all 4 live archives BEFORE demo
    print("\n[Step 1] Verifying 32 blobs across 4 live archives BEFORE demo...")
    before_status = verify_original_archives()
    sig_before = {name: snapshot_signature(DATA_BASE / name) for name in ARCHIVE_NAMES}
    print(f"Total blobs verified: {before_status['total_blobs']}; Healthy: {before_status['healthy']}")
    assert before_status["healthy"] is True
    assert before_status["total_blobs"] == 32
    print("All 32 original CAS blobs across all 4 live archives are 100% intact.")

    # 2. Setup isolated temporary directory with WAL-safe snapshot
    print("\n[Step 2] Creating temporary WAL-safe copy of live-r2-revision3...")
    with tempfile.TemporaryDirectory(prefix="pythia-demo-r302-") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        copy_dir = tmp_dir / "archive_snapshot"
        copy_dir.mkdir(parents=True)
        out_dir = tmp_dir / "dossier_output"
        out_dir.mkdir(parents=True)

        src_dir = DATA_BASE / "live-r2-revision3"
        src_db = src_dir / "archive.db"
        dst_db = copy_dir / "archive.db"

        # Backup SQLite safely via sqlite3 backup API
        src_conn = sqlite3.connect(f"{src_db.resolve().as_uri()}?mode=ro", uri=True)
        dst_conn = sqlite3.connect(str(dst_db))
        try:
            src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()

        # Copy blobs tree
        shutil.copytree(src_dir / "blobs", copy_dir / "blobs")
        print(f"Snapshot created at {copy_dir} (db size: {dst_db.stat().st_size} bytes)")

        # 3. Read available receipts from snapshot
        print("\n[Step 3] Selecting explicit receipt IDs from snapshot...")
        snap_conn = sqlite3.connect(f"{dst_db.resolve().as_uri()}?mode=ro", uri=True)
        selected_rids = []
        receipt_details = []
        cutoff = 8

        try:
            cursor = snap_conn.cursor()
            cursor.execute(
                """
                SELECT receipt_id, seq, source_id, status, blob_sha256
                FROM receipts
                WHERE source_id IN ('nyfed_effr', 'fed_monetary_rss', 'fed_speeches_rss')
                ORDER BY seq ASC;
                """
            )
            rows = cursor.fetchall()
            for rid, seq, sid, status, sha in rows:
                selected_rids.append(rid)
                receipt_details.append({"receipt_id": rid, "seq": seq, "source_id": sid, "status": status, "sha": sha})
                print(f"  Selected: seq={seq} | source={sid} | receipt_id={rid}")
        finally:
            snap_conn.close()

        assert len(selected_rids) == 3, f"Expected 3 receipts, got {len(selected_rids)}"

        # 4. Construct EventDescriptor
        descriptor = EventDescriptor(
            event_id="fixture_fomc_202610_context",
            question="Will the Federal Reserve adjust the target federal funds rate range at the October/November 2026 FOMC meeting?",
            deadline="2026-11-04T18:00:00+00:00",
            rules=None,  # Explicitly null: market rules incomplete
            provenance="fixture",
            contract_status="incomplete",
        )
        print("\n[Step 4] Event Descriptor:")
        print(json.dumps(descriptor.to_dict(), indent=2))

        # 5. Build Dossier Bundle
        print(f"\n[Step 5] Building frozen dossier in {out_dir} (cutoff as_of_seq={cutoff})...")
        bundle_path, bundle_data = build_event_dossier(
            archive_dir=copy_dir,
            as_of_seq=cutoff,
            receipt_ids=selected_rids,
            event_descriptor=descriptor,
            output_dir=out_dir,
        )

        digest = bundle_data["bundle_digest"]
        dossier = bundle_data["dossier"]
        manifest = dossier["manifest"]
        facts = dossier["facts"]
        origin_groups = dossier["origin_groups"]
        metadata = dossier["metadata"]

        # Verify digest
        canon_bytes = json.dumps(dossier, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        computed_digest = hashlib.sha256(canon_bytes).hexdigest()
        assert computed_digest == digest, f"Digest mismatch: {computed_digest} != {digest}"

        print(f"\n[Step 6] Dossier Published Successfully!")
        print(f"  Bundle File:   {bundle_path.name}")
        print(f"  Bundle Digest: {digest}")
        print(f"  As-Of Seq:     {manifest['as_of_seq']}")
        print(f"  Requested:     {manifest['total_requested']}")
        print(f"  Verified:      {manifest['total_verified']}")
        print(f"  Facts Count:   {manifest['total_facts_extracted']}")
        print(f"  Rejections:    {manifest['total_rejected']}")
        print(f"  Origin Groups: {len(origin_groups)}")
        print(f"  Usable Forecast: {metadata['usable_for_forecast']}")
        print(f"  Dossier Type:    {metadata['dossier_type']}")

        # 6. Verify ALL 35 proofs against raw CAS blobs
        print("\n[Step 7] Verifying all 35 fact proofs against raw CAS blobs...")
        source_counts = Counter()
        source_valid = Counter()
        bom_sources = set()
        proof_details = []

        for fact in facts:
            sha = fact["blob_sha256"]
            blob_path = copy_dir / "blobs" / sha[:2] / sha[2:4] / f"{sha}.bin"
            blob_bytes = blob_path.read_bytes()
            proof = ProofSpan(**fact["proof"])
            is_valid = proof.verify_against_blob(blob_bytes)
            source_counts[fact["source_id"]] += 1
            source_valid[fact["source_id"]] += int(is_valid)
            if blob_bytes.startswith(b"\xef\xbb\xbf"):
                bom_sources.add(fact["source_id"])
            proof_details.append({
                "fact_id": fact["fact_id"],
                "source_id": fact["source_id"],
                "valid": is_valid,
                "quote_len": len(proof.raw_quote_text) if proof.raw_quote_text else 0,
            })

        all_valid = all(p["valid"] for p in proof_details)
        print(f"  Total Facts: {len(facts)}")
        print(f"  Valid Proofs: {sum(source_valid.values())} / {len(facts)}")
        print(f"  Sources with BOM: {sorted(bom_sources)}")
        for sid, cnt in sorted(source_counts.items()):
            print(f"    - {sid}: {source_valid[sid]} / {cnt} valid")
        assert all_valid, f"Proof verification failed! Only {sum(source_valid.values())}/{len(facts)} valid."
        assert len(facts) == 35, f"Expected 35 facts, got {len(facts)}"

        # 7. Verify origin groups contracts (F6)
        print("\n[Step 8] Verifying origin groups contracts (F6)...")
        for g in origin_groups:
            assert "independent_evidence_count" not in g, f"Group {g['origin_id']} claims independent_evidence_count!"
        nyfed_g = next(g for g in origin_groups if g["entity_type"] == "rate_series")
        assert nyfed_g["canonical_url"] == CANONICAL_SOURCES["nyfed_effr"], (
            f"NYFed canonical URL mismatch: {nyfed_g['canonical_url']} != {CANONICAL_SOURCES['nyfed_effr']}"
        )
        print("  All origin groups conform to F6 contracts.")

        # 8. Copy published bundle to destination artifact folder
        dest_dir = PROJECT_ROOT / "docs/research/2026-10-18/r3/revision2"
        dest_dir.mkdir(parents=True, exist_ok=True)
        bundle_dest = dest_dir / "demo_dossier_bundle.json"
        bundle_dest.write_text(bundle_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\n[Step 9] Published bundle written to: {bundle_dest}")

        # Summary
        summary = {
            "demo_version": "R3.0.2",
            "utc": datetime.now(timezone.utc).isoformat(),
            "bundle_file": bundle_dest.name,
            "bundle_digest": digest,
            "cutoff_seq": cutoff,
            "receipt_ids": selected_rids,
            "total_facts": len(facts),
            "all_demo_proofs_valid": all_valid,
            "valid_proofs": sum(source_valid.values()),
            "invalid_proofs": len(facts) - sum(source_valid.values()),
            "source_proof_counts": {s: {"total": n, "valid": source_valid[s]} for s, n in source_counts.items()},
            "raw_bom_sources": sorted(bom_sources),
            "origin_groups_count": len(origin_groups),
            "claimed_independent_evidence": sum(g.get("independent_evidence_count", 0) for g in origin_groups),
            "nyfed_canonical_url": nyfed_g["canonical_url"],
            "dossier_version": dossier["dossier_version"],
            "extractor_version": dossier["extractor_version"],
            "fact_schema_version": dossier["fact_schema_version"],
            "metadata": metadata,
        }
        summary_path = dest_dir / "demo_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    # 9. Verify all 4 live archives AFTER demo
    print("\n[Step 10] Verifying 32 blobs across 4 live archives AFTER demo...")
    after_status = verify_original_archives()
    sig_after = {name: snapshot_signature(DATA_BASE / name) for name in ARCHIVE_NAMES}
    print(f"Total blobs verified: {after_status['total_blobs']}; Healthy: {after_status['healthy']}")
    assert after_status["healthy"] is True
    assert after_status["total_blobs"] == 32
    assert sig_before == sig_after, "Archive signatures changed!"
    print("All 32 original CAS blobs across all 4 live archives are 100% intact and unchanged.")

    print("\n" + "=" * 70)
    print("DEMO COMPLETED SUCCESSFULLY: 35/35 PROOFS VALID, ZERO CLOBBER, ZERO NETWORK")
    print("=" * 70)
    return summary


if __name__ == "__main__":
    run_demo()
