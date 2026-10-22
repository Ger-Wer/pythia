#!/usr/bin/env python3
"""R5 Layer A measurement: run the JEV deterministic screen over the whole
R4 corpus and compare with the frozen labels. Offline; read-only.

Phases: (1) screen every document; (2) batch duplicate-origin pass —
documents sharing a fingerprint form a group, the original is the
non-synthetic member, other members are re-screened with the original's
fingerprint in the seen set; (3) aggregate expectations from final rows.
"""
import json
from collections import defaultdict
from pathlib import Path

from python.evaluation.evidence_reader import ReadOnlyArchiveReader
from python.evaluation.jev_gate import screen_document

ROOT = Path(__file__).resolve().parents[4]
M = json.loads((ROOT / "docs/research/2026-10-22/r4/corpus_manifest.json").read_text())
reader = ReadOnlyArchiveReader("/home/pythia/pythia-evidence-corpus")

SPECIAL_WANTS = {
    "neg_syn_absurd_number": {"implausible_numbers"},
    "neg_syn_translation_fake": {"language_mismatch"},
    "neg_syn_login_wall": {"blocked_or_challenge", "unverified_shell"},
}

rows: dict[str, dict] = {}
bodies: dict[str, bytes] = {}
for sid, e in sorted(M["documents"].items()):
    if e.get("complete") is True and e.get("receipt_id"):
        vc = reader.read_receipts([e["receipt_id"]], e["seq"])[0]
        bodies[sid] = vc.blob_bytes
        res = screen_document(vc.blob_bytes, declared_language=(e.get("language") or "en").split("_")[0])
    else:
        res = screen_document(None)
    rows[sid] = res.to_dict()

fp_groups: dict[str, list[str]] = defaultdict(list)
for sid, res_d in rows.items():
    if res_d.get("fingerprint"):
        fp_groups[res_d["fingerprint"]].append(sid)
for fp, members in fp_groups.items():
    if len(members) < 2:
        continue
    members_sorted = sorted(members, key=lambda s: (bool((M["documents"][s].get("label") or {}).get("synthetic")), s))
    for dup_sid in members_sorted[1:]:
        e = M["documents"][dup_sid]
        res2 = screen_document(bodies[dup_sid], declared_language=(e.get("language") or "en").split("_")[0], seen_fingerprints={fp})
        rows[dup_sid] = res2.to_dict()

by_split: dict[str, dict] = {}
for sid, e in sorted(M["documents"].items()):
    label = e.get("label") or {}
    split = label.get("split", "unsplit")
    agg = by_split.setdefault(split, {"docs": 0, "false_rejects": [], "missed": [], "caught": 0, "out_of_scope": 0, "ok": 0})
    agg["docs"] += 1
    rules = {f["rule_id"] for f in rows[sid]["findings"]}
    expected = label.get("expected_class")
    if sid in SPECIAL_WANTS:
        if SPECIAL_WANTS[sid] & rules:
            agg["caught"] += 1
        else:
            agg["missed"].append((sid, sorted(SPECIAL_WANTS[sid]), sorted(rules)))
    elif expected == "valid" and label.get("cluster_id"):
        if "duplicate_origin" in rules:
            agg["caught"] += 1
        else:
            agg["missed"].append((sid, ["duplicate_origin"], sorted(rules)))
    elif expected == "valid":
        if not rows[sid]["passed"]:
            agg["false_rejects"].append((sid, sorted(rules)))
        else:
            agg["ok"] += 1
    elif expected == "blocked":
        if "no_body" in rules:
            agg["caught"] += 1
        else:
            agg["missed"].append((sid, ["no_body"], sorted(rules)))
    elif expected == "unverified":
        if "unverified_shell" in rules or "shell_suspect" in rules:
            agg["caught"] += 1
        else:
            agg["missed"].append((sid, ["unverified_shell|shell_suspect"], sorted(rules)))
    else:  # rejected (profile-level), truncated/undated synthetics: not Layer A scope
        agg["out_of_scope"] += 1

print(json.dumps(by_split, indent=1, ensure_ascii=False))
out = ROOT / "docs/research/2026-10-22/r5/screen_results.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({"gate": "layerA", "by_split": by_split, "results": rows}, indent=1))
print("wrote", out)
