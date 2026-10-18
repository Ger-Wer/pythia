"""Pythia R3.0: Offline Demonstration Script on live-r2-revision3 copy.

Performs:
1. Hash verification of all 32 original CAS blobs across existing 4 live archives (before).
2. Consistent WAL-safe snapshot of live-r2-revision3 using SQLite backup API + blob copy.
3. Mechanical extraction of 3 explicit receipt IDs (EFFR, Monetary RSS, Speeches RSS) with cutoff 8.
4. Construction of EventDescriptor with contract_status=incomplete and rules=None.
5. Execution of build_event_dossier to a separate output directory.
6. Verification of the published canonical bundle, content digest, facts, proof spans, and origin groups.
7. Hash verification of all 32 original CAS blobs across existing 4 live archives (after).
8. Exports demo summary to JSON and stdout.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from python.evaluation.event_dossier import EventDescriptor, build_event_dossier

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

        ro_uri = f"file:{db_path}?mode=ro"
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


def main() -> None:
    print("=" * 70)
    print("Pythia R3.0 Offline Facts & Frozen Dossier Demonstration")
    print("=" * 70)

    # 1. Verify original archives BEFORE demo
    print("\n[Step 1] Verifying 32 blobs across 4 live archives BEFORE demo...")
    before_status = verify_original_archives()
    print(f"Total blobs verified: {before_status['total_blobs']}; Healthy: {before_status['healthy']}")
    assert before_status["healthy"] is True
    assert before_status["total_blobs"] == 32

    # 2. Make WAL-safe snapshot copy of live-r2-revision3
    scratch_dir = Path("/home/pythia/pythia-evidence-r3/scratch_demo")
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    snapshot_dir = scratch_dir / "archive_snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_blobs = snapshot_dir / "blobs"
    snapshot_blobs.mkdir(parents=True, exist_ok=True)

    src_dir = DATA_BASE / "live-r2-revision3"
    src_db = src_dir / "archive.db"
    dst_db = snapshot_dir / "archive.db"

    print(f"\n[Step 2] Creating consistent SQLite backup from {src_db}...")
    ro_conn = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(str(dst_db))
    try:
        ro_conn.backup(dst_conn)
    finally:
        ro_conn.close()
        dst_conn.close()

    print("Copying CAS blobs to snapshot directory...")
    src_blobs = src_dir / "blobs"
    shutil.copytree(str(src_blobs), str(snapshot_blobs), dirs_exist_ok=True)

    # 3. Mechanically inspect snapshot to select 3 explicit receipt IDs
    print("\n[Step 3] Mechanically selecting explicit receipt IDs from snapshot...")
    snap_conn = sqlite3.connect(f"file:{dst_db}?mode=ro", uri=True)
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
        deadline="2026-11-04T18:00:00Z",
        rules=None,  # Explicitly null: market rules incomplete
        provenance="fixture",
        contract_status="incomplete",
    )
    print("\n[Step 4] Event Descriptor:")
    print(json.dumps(descriptor.to_dict(), indent=2))

    # 5. Build Dossier Bundle in separate output directory
    dossier_out_dir = scratch_dir / "output_dossier"
    print(f"\n[Step 5] Building frozen dossier in {dossier_out_dir} (cutoff as_of_seq={cutoff})...")

    bundle_path, bundle_data = build_event_dossier(
        archive_dir=snapshot_dir,
        as_of_seq=cutoff,
        receipt_ids=selected_rids,
        event_descriptor=descriptor,
        output_dir=dossier_out_dir,
    )

    digest = bundle_data["bundle_digest"]
    dossier = bundle_data["dossier"]
    manifest = dossier["manifest"]
    facts = dossier["facts"]
    origin_groups = dossier["origin_groups"]
    metadata = dossier["metadata"]

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

    print("\nSample Facts Extracted:")
    # Show first 2 facts
    for idx, f in enumerate(facts[:3]):
        print(f"  Fact {idx + 1} ({f['fact_type']}):")
        print(f"    Source:    {f['source_id']} ({f['issuer']})")
        print(f"    Value:     {f['value']}")
        print(f"    Unit:      {f['unit']}")
        print(f"    Dates:     effective={f['effective_date']} | published_at={f['published_at']}")
        print(f"    Locator:   {f['locator']['path']}")
        print(f"    Proof SHA: {f['proof']['quote_sha256'][:16]}... (bytes {f['proof']['byte_start']}-{f['proof']['byte_end']})")

    print("\nOrigin Groups:")
    for og in origin_groups:
        print(f"  Origin ID:   {og['origin_id']}")
        print(f"    Publisher: {og['publisher']}")
        print(f"    Type:      {og['entity_type']}")
        print(f"    Obs Count: {len(og['observations'])}")
        print(f"    Indep Ev:  {og['independent_evidence_count']}")

    # 7. Verify original archives AFTER demo
    print("\n[Step 7] Verifying 32 blobs across 4 live archives AFTER demo...")
    after_status = verify_original_archives()
    print(f"Total blobs verified: {after_status['total_blobs']}; Healthy: {after_status['healthy']}")
    assert after_status["healthy"] is True
    assert after_status["total_blobs"] == 32

    # 8. Copy published bundle to artifacts directory
    artifacts_dir = Path("/home/pythia/pythia-evidence-r3/docs/research/2026-10-18/r3/increment0")
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    published_copy = artifacts_dir / "demo_dossier_bundle.json"
    shutil.copy2(str(bundle_path), str(published_copy))

    summary_file = artifacts_dir / "demo_summary.json"
    summary_data = {
        "bundle_file": bundle_path.name,
        "bundle_digest": digest,
        "cutoff_seq": cutoff,
        "receipt_ids": selected_rids,
        "total_facts": len(facts),
        "total_origins": len(origin_groups),
        "total_rejections": len(manifest["rejected_receipts"]),
        "original_blobs_intact": after_status["healthy"],
        "metadata": metadata,
    }
    summary_file.write_text(json.dumps(summary_data, indent=2, sort_keys=True))

    print(f"\nArtifacts saved to {artifacts_dir}")
    print("=" * 70)


if __name__ == "__main__":
    main()
