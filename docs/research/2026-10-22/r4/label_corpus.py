"""R4 labeling: derive ground-truth labels for every corpus document from its
archived blob (proof segments and deterministic parses, never from extractor
claims alone), assign kind (positive/negative) and the frozen tuning/control
split. Idempotent: documents that already carry a label are left untouched.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from python.evaluation.evidence_reader import ReadOnlyArchiveReader
from python.evaluation.fed_statement_extract import extract_fed_statement

ARCHIVE_DIR = Path("/home/pythia/pythia-evidence-corpus")
OUT_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = OUT_DIR / "corpus_manifest.json"

NEGATIVE_KIND = {
    "neg_bls_cpi_page", "neg_bls_cpi_rss", "neg_arena", "neg_fed_404",
    "cbr_en_ruonia", "cbr_en_mrrf", "cbr_press_shell",
    "fomc_minutes_20260128", "fomc_minutes_20251210",
    "ecb_art_mg261008", "fed_other_enforcement20261016a",
    "fed_other_orders20261005a", "fed_speech_bowman20261014a",
}

RATE_RE = re.compile(r"target range for the federal funds rate (?:by [^.]*? )?to ([\d\-–/ ]+) to ([\d\-–/ ]+) percent")
DATE_RE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}\b")


def receipt_status(source_id: str) -> Dict[str, Any]:
    conn = sqlite3.connect(f"file:{ARCHIVE_DIR}/archive.db?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT http_status, complete FROM receipts WHERE source_id = ? ORDER BY seq DESC LIMIT 1",
            (source_id,),
        ).fetchone()
    finally:
        conn.close()
    return {"http_status": row[0], "complete": bool(row[1])}


def visible_text_stats(body: bytes) -> Dict[str, Any]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04FF")
    cjk = sum(1 for ch in text if "\u4E00" <= ch <= "\u9FFF")
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    total = max(1, cyr + cjk + latin)
    return {
        "visible_text_chars": len(text),
        "cyrillic_share": round(cyr / total, 3),
        "cjk_share": round(cjk / total, 3),
        "latin_share": round(latin / total, 3),
        "title": (soup.title.get_text(strip=True) if soup.title else None),
    }


def label_statement(entry: Dict[str, Any], body: bytes) -> Dict[str, Any]:
    manifest = {
        "source_url": entry["url"],
        "final_url": entry["url"],
        "blob_sha256": hashlib.sha256(body).hexdigest(),
        "bytes_stored": len(body),
        "transport_outcome": "success",
        "http_status": 200,
        "research_provenance": "research_fixture",
        "issuer": "Federal Reserve Board",
    }
    result = extract_fed_statement(body, manifest)
    label: Dict[str, Any] = {"expected_class": "valid", "coverage": "statement_extractor"}
    if result.is_success and result.document is not None:
        doc = result.document
        label["extraction_at_labeling"] = "success"
        seg = lambda p: body[p.byte_start:p.byte_end].decode("utf-8", "replace")
        label["facts"] = {
            "meeting_date_text": seg(doc.date_proof),
            "title_text": seg(doc.title_proof),
            "n_paragraphs": len(doc.paragraphs),
            "paragraph_quote_shas": [p.proof.quote_sha256 for p in doc.paragraphs],
        }
        full = " ".join(seg(p.proof) for p in doc.paragraphs)
        m = RATE_RE.search(full)
        if m:
            label["facts"]["target_range"] = [m.group(1).strip(), m.group(2).strip()]
    else:
        label["extraction_at_labeling"] = "rejected"
        label["labeling_rejections"] = [r.reason for r in result.rejections][:3]
    return label


def label_feed(entry: Dict[str, Any], body: bytes) -> Dict[str, Any]:
    import xml.etree.ElementTree as ET

    label: Dict[str, Any] = {"expected_class": "valid", "coverage": "collection_only"}
    try:
        root = ET.fromstring(body)
        items = root.findall(".//item")
        label["facts"] = {"item_count": len(items)}
        if items:
            first = items[0]
            label["facts"]["first_item_title"] = (first.findtext("title") or "").strip()[:120]
            label["facts"]["items_with_pubdate"] = sum(1 for i in items if i.findtext("pubDate"))
        if entry["source_id"].startswith("fed_rss"):
            label["coverage"] = "rss_extractor"
    except ET.ParseError:
        label["facts"] = {"item_count": None, "parse_error": True}
    return label


def label_json_api(entry: Dict[str, Any], body: bytes) -> Dict[str, Any]:
    label: Dict[str, Any] = {"expected_class": "valid", "coverage": "collection_only"}
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        label["facts"] = {"parse_error": True}
        return label
    if isinstance(data, dict) and "refRates" in data:
        rates = data["refRates"]
        label["facts"] = {
            "record_count": len(rates),
            "first_date": rates[0].get("effectiveDate") if rates else None,
            "first_percent_rate": rates[0].get("percentRate") if rates else None,
        }
        if entry["source_id"].startswith("nyfed_effr"):
            label["coverage"] = "effr_extractor"
    elif isinstance(data, list):
        label["facts"] = {"record_count": len(data)}
        if data and isinstance(data[0], dict):
            label["facts"]["first_name"] = data[0].get("name") or data[0].get("tag_name") or data[0].get("id")
            label["facts"]["first_date"] = (
                data[0].get("published_at") or data[0].get("created_at") or data[0].get("lastModified")
            )
    return label


def label_page(entry: Dict[str, Any], body: bytes) -> Dict[str, Any]:
    stats = visible_text_stats(body)
    label: Dict[str, Any] = {"expected_class": "valid", "coverage": "collection_only", "facts": stats}
    text_dates = DATE_RE.findall(body.decode("utf-8", "replace"))
    label["facts"]["en_dates_found"] = len(text_dates)
    return label


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    reader = ReadOnlyArchiveReader(ARCHIVE_DIR)
    labeled = 0
    for sid, entry in sorted(manifest["documents"].items()):
        if entry.get("label"):
            continue
        status = receipt_status(sid)
        label: Dict[str, Any] = {}
        if not entry["complete"]:
            label = {
                "expected_class": "blocked",
                "coverage": "transport",
                "facts": {"http_status": status["http_status"]},
            }
        else:
            vc = reader.read_receipts([entry["receipt_id"]], entry["seq"])[0]
            body = vc.blob_bytes or b""
            fam = entry["family"]
            if fam == "fomc_statement":
                label = label_statement(entry, body)
            elif fam == "rss_feed":
                label = label_feed(entry, body)
            elif fam in ("reference_rates", "ai_models_api", "ai_releases_api"):
                label = label_json_api(entry, body)
            elif sid == "cbr_press_shell":
                stats = visible_text_stats(body)
                label = {
                    "expected_class": "unverified",
                    "coverage": "collection_only",
                    "facts": stats,
                    "note": "JS-rendered press release: served HTML carries no release text (contract 4.7).",
                }
            elif fam in ("fomc_minutes", "fed_other_release", "ecb_article", "fed_speech") and sid in NEGATIVE_KIND:
                stats = visible_text_stats(body)
                label = {
                    "expected_class": "valid",
                    "coverage": "statement_profile_control",
                    "facts": stats,
                    "note": "Valid document of another type: the FOMC statement profile must reject it (typed), never extract it as a statement.",
                }
            else:
                label = label_page(entry, body)
        label["kind"] = "negative" if sid in NEGATIVE_KIND else "positive"
        label.setdefault("synthetic", False)
        entry["label"] = label
        labeled += 1
        print("labeled:", sid, label.get("expected_class"), label.get("coverage"))
    # Frozen stratified split: within (family, kind), sorted by source_id,
    # positions 3 and 4 of every 5 go to control (~40%).
    groups: Dict[str, List[str]] = {}
    for sid, entry in manifest["documents"].items():
        groups.setdefault(f"{entry['family']}|{entry['label']['kind']}", []).append(sid)
    for key, ids in groups.items():
        for i, sid in enumerate(sorted(ids)):
            manifest["documents"][sid]["label"]["split"] = "control" if i % 5 in (3, 4) else "tuning"
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    docs = manifest["documents"]
    pos = sum(1 for d in docs.values() if d["label"]["kind"] == "positive")
    neg = sum(1 for d in docs.values() if d["label"]["kind"] == "negative")
    ctrl = sum(1 for d in docs.values() if d["label"]["split"] == "control")
    print(f"total {len(docs)} | positive {pos} | negative {neg} | control {ctrl} | newly labeled {labeled}")


if __name__ == "__main__":
    main()
