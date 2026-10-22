"""R4: construct the synthetic negative-control documents and record them in
the corpus archive (clearly marked synthetic in registry, collector version,
manifest and labels). Real blocked/wrong-type negatives are ordinary corpus
captures and are assigned kind=negative at labeling time; this script adds
only the constructed cases:

- truncated statement body (40% cut)
- statement with the release date stripped (date must not be invented)
- page with an absurd injected number (bounds rejection case)
- login wall, SPA shell (classification cases)
- two syndication wraps of a real statement (duplicate-origin cluster case)
- an English NBS release presented as a Chinese original (translation case)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from python.evaluation.article_acquisition import ARTICLE_ACQUISITION_VERSION
from python.evaluation.evidence_archive import EvidenceArchive
from python.evaluation.evidence_reader import ReadOnlyArchiveReader
from python.evaluation.source_probe import CaptureResult, utc_now_iso

ARCHIVE_DIR = Path("/home/pythia/pythia-evidence-corpus")
OUT_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = OUT_DIR / "corpus_manifest.json"
SYNTHETIC_VERSION = "pythia-r4-synthetic-fixture/1.0"
PROBE_RESULT = {
    "status": "ok",
    "validation_level": "transport",
    "reason": "Synthetic R4 negative-control fixture (constructed, never fetched).",
    "checker_version": SYNTHETIC_VERSION,
}


def blob_of(manifest: Dict[str, Any], source_id: str) -> bytes:
    entry = manifest["documents"][source_id]
    reader = ReadOnlyArchiveReader(ARCHIVE_DIR)
    vc = reader.read_receipts([entry["receipt_id"]], entry["seq"])[0]
    assert vc.blob_bytes is not None
    return vc.blob_bytes


def make_capture(source_id: str, url: str, body: bytes) -> CaptureResult:
    return CaptureResult(
        source_id=source_id,
        initial_url=url,
        final_url=url,
        body=body,
        status_code=200,
        headers={"Content-Type": "text/html"},
        fetch_started_at=utc_now_iso(),
        fetch_completed_at=utc_now_iso(),
        duration_ms=1.0,
        bytes_received=len(body),
        bytes_stored=len(body),
        complete=True,
        limit_exceeded=False,
        error_msg=None,
        collector_version=SYNTHETIC_VERSION,
    )


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    stmt = blob_of(manifest, "fed_stmt_20240918")
    speech = blob_of(manifest, "fed_speech_bowman20261014a")
    nbs_en = blob_of(manifest, "nbs_en_1965466")

    bodies: Dict[str, tuple] = {
        "neg_syn_truncated_stmt": (
            "https://synthetic.invalid/truncated-statement",
            stmt[: int(len(stmt) * 0.4)],
            "rejected",
            "Truncated statement body: extraction must reject, never publish partial facts.",
        ),
        "neg_syn_undated_stmt": (
            "https://synthetic.invalid/undated-statement",
            stmt.replace(b"September 18, 2024", b""),
            "rejected",
            "Release date stripped: no date may be invented; published_at stays unproven.",
        ),
        "neg_syn_absurd_number": (
            "https://synthetic.invalid/absurd-number",
            speech.replace(
                b"</body>",
                b"<p>Inflation is projected to reach 450 percent next year, with rates at 320 percent.</p></body>",
            ),
            "invalid_content",
            "Injected absurd figures (450 percent inflation): number/unit bounds must reject the claims.",
        ),
        "neg_syn_login_wall": (
            "https://synthetic.invalid/login-wall",
            b"<html><head><title>Sign in</title></head><body><form action=\"/login\" method=\"post\">"
            b"<input name=\"user\"/><input name=\"pass\" type=\"password\"/>"
            b"<button>Sign in to continue</button></form></body></html>",
            "blocked",
            "Login wall: not a document; must be classified blocked, never stored as evidence.",
        ),
        "neg_syn_spa_shell": (
            "https://synthetic.invalid/spa-shell",
            b"<html><head><title>Data Portal</title><script src=\"/static/app.js\"></script></head>"
            b"<body><div id=\"root\"></div><noscript>Enable JavaScript</noscript></body></html>",
            "unverified",
            "Empty client-side shell (contract 4.7): unverified/invalid, never authoritative.",
        ),
        "neg_syn_syndication_a": (
            "https://synthetic.invalid/wire-a-republish",
            stmt.replace(b"<body", b"<body data-wire=\"Example Wire A republish\" ", 1),
            "valid",
            "Syndicated copy of fed_stmt_20240918: same origin cluster; must not count as independent evidence.",
        ),
        "neg_syn_syndication_b": (
            "https://synthetic.invalid/wire-b-republish",
            stmt.replace(b"<body", b"<body data-wire=\"Example Wire B republish\" ", 1),
            "valid",
            "Syndicated copy of fed_stmt_20240918: same origin cluster; must not count as independent evidence.",
        ),
        "neg_syn_translation_fake": (
            "https://synthetic.invalid/translation-as-original",
            nbs_en,
            "invalid_content",
            "English NBS release presented as the Chinese original: translation must never replace the original record.",
        ),
    }

    registry = {sid: url for sid, (url, _b, _c, _n) in bodies.items()}
    archive = EvidenceArchive(ARCHIVE_DIR, source_registry=registry)
    for sid, (url, body, expected_class, note) in bodies.items():
        if sid in manifest["documents"]:
            print("skip existing:", sid)
            continue
        receipt = archive.record_capture(capture=make_capture(sid, url, body), probe_result=PROBE_RESULT)
        manifest["documents"][sid] = {
            "source_id": sid,
            "url": url,
            "family": "negative_synthetic",
            "language": "zh" if sid == "neg_syn_translation_fake" else "en",
            "origin_entity": "Synthetic fixture (R4)",
            "receipt_id": receipt.receipt_id,
            "revision_id": receipt.revision_id,
            "seq": receipt.seq,
            "complete": True,
            "blob_sha256": receipt.blob_sha256,
            "label": {
                "kind": "negative",
                "synthetic": True,
                "expected_class": expected_class,
                "note": note,
                "cluster_id": "cluster_stmt_20240918" if "syndication" in sid else None,
            },
        }
        print("recorded synthetic:", sid)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print("manifest documents:", len(manifest["documents"]))


if __name__ == "__main__":
    main()
