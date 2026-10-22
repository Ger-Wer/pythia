"""Restore labels for the four one-shot blocked negatives (neg_bls_cpi_page,
neg_bls_cpi_rss, neg_arena, neg_fed_404) whose manifest entries were reset by
a capture-script rerun. Labels follow the v1 rule for blocked transports;
splits follow the same frozen stratified rule within the family group.
"""
import json
import sqlite3
from pathlib import Path

BASE = Path("/home/pythia/pythia-evidence-r4/docs/research/2026-10-22/r4")
MANIFEST_PATH = BASE / "corpus_manifest.json"
DB = "/home/pythia/pythia-evidence-corpus/archive.db"

manifest = json.loads(MANIFEST_PATH.read_text())
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
group = sorted(k for k, d in manifest["documents"].items() if d["family"] == "negative_blocked")
for i, sid in enumerate(group):
    entry = manifest["documents"][sid]
    if entry.get("label"):
        continue
    row = conn.execute(
        "SELECT http_status FROM receipts WHERE source_id = ? ORDER BY seq DESC LIMIT 1", (sid,)
    ).fetchone()
    entry["label"] = {
        "expected_class": "blocked",
        "coverage": "transport",
        "facts": {"http_status": row[0] if row else None},
        "kind": "negative",
        "synthetic": False,
        "split": "control" if i % 5 in (3, 4) else "tuning",
    }
    print("relabeled:", sid, entry["label"])
conn.close()
MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
print("done")
