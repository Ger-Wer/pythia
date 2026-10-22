"""R4 labeling v2 for FOMC statements: independent ground truth from the raw
HTML (BeautifulSoup parse, not the extractor), plus genuine feed lineage for
the two statements present in the corpus monetary RSS capture — for those,
a real R3.0 dossier bundle is built over the corpus feed and the accepted
extractor runs end-to-end. Other statements are labeled as direct captures:
the accepted profile requires feed lineage by design, and the measurement
records that coverage fact instead of fabricating lineage.
Preserves kind/split assigned by label_corpus.py.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from python.evaluation.evidence_extract import extract_fed_rss
from python.evaluation.evidence_reader import ReadOnlyArchiveReader
from python.evaluation.event_dossier import EventDescriptor, build_event_dossier
from python.evaluation.fed_statement_extract import extract_fed_statement

ARCHIVE_DIR = Path("/home/pythia/pythia-evidence-corpus")
OUT_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = OUT_DIR / "corpus_manifest.json"

RANGE_RE = re.compile(
    r"target range for the federal funds rate[^.]*?(?:to|at) ([\d][\d\-–\u2011/ ]*?) to ([\d][\d\-–\u2011/ ]*?) percent"
)
DATE_TEXT_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}\b"
)
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

LINEAGED = {"fed_stmt_20260916": 3, "fed_stmt_20260729": 7}
SPLIT_OVERRIDE = {"fed_stmt_20260916": "control", "fed_stmt_20260729": "tuning"}


def parse_statement(body: bytes, url: str = "") -> Dict[str, Any]:
    soup = BeautifulSoup(body, "html.parser")
    container = soup.find(id="article") or soup.find("div", class_=re.compile(r"col-xs-12"))
    facts: Dict[str, Any] = {}
    facts["page_title"] = soup.title.get_text(strip=True) if soup.title else ""
    paragraphs: List[str] = []
    if container is not None:
        for p in container.find_all("p"):
            t = p.get_text(" ", strip=True)
            if t:
                paragraphs.append(t)
    facts["n_paragraphs"] = len(paragraphs)
    facts["paragraph_texts_sha"] = [hashlib.sha256(t.encode()).hexdigest() for t in paragraphs]
    joined = " ".join(paragraphs)
    rm = RANGE_RE.search(joined)
    if rm:
        facts["target_range"] = [
            rm.group(1).strip().replace("\u2011", "-"), rm.group(2).strip().replace("\u2011", "-")
        ]
    else:
        facts["target_range"] = None
    # Meeting date: the document must carry its own date; the URL date only
    # selects which in-document date is the release date.
    container_text = container.get_text(" ", strip=True) if container is not None else ""
    found = DATE_TEXT_RE.findall(container_text)
    found_full = [m.group(0) for m in DATE_TEXT_RE.finditer(container_text)]
    expected = None
    dm = re.search(r"monetary(\d{4})(\d{2})(\d{2})", url)
    if dm:
        expected = f"{MONTHS[int(dm.group(2)) - 1]} {int(dm.group(3))}, {dm.group(1)}"
    facts["release_date_text"] = expected if expected in found_full else (found_full[0] if found_full else None)
    facts["date_matches_url"] = expected is not None and expected in found_full
    return facts


def fact_value(fact: Any, key: str) -> Optional[str]:
    value = getattr(fact, "value", {}) or {}
    val = value.get(key)
    return val if isinstance(val, str) else None


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    reader = ReadOnlyArchiveReader(ARCHIVE_DIR)
    feed_entry = manifest["documents"]["fed_rss_monetary"]
    feed_vc = reader.read_receipts([feed_entry["receipt_id"]], feed_entry["seq"])[0]

    # Genuine fact ids from the accepted R3.0 RSS extractor.
    feed_facts = extract_fed_rss(feed_vc)
    url_to_fact: Dict[str, Any] = {}
    for fact in feed_facts:
        url = fact_value(fact, "referenced_article_url")
        if url:
            url_to_fact[url.strip()] = fact
    print("feed facts with article urls:", len(url_to_fact))

    # One genuine parent dossier bundle over the corpus feed.
    descriptor = EventDescriptor(
        event_id="fixture_corpus_r4_feed_context",
        question="Corpus R4 parent context for FOMC statement lineage.",
        deadline=None,
        rules=None,
        provenance="fixture",
        contract_status="incomplete",
    )
    _path, bundle_data = build_event_dossier(
        archive_dir=ARCHIVE_DIR,
        as_of_seq=feed_entry["seq"],
        receipt_ids=[feed_entry["receipt_id"]],
        event_descriptor=descriptor,
        output_dir=OUT_DIR / "parent_bundle",
    )
    parent_digest = bundle_data["bundle_digest"]
    print("parent bundle digest:", parent_digest[:16])

    root = ET.fromstring(feed_vc.blob_bytes or b"")
    items = root.findall(".//item")

    for sid, entry in sorted(manifest["documents"].items()):
        if entry["family"] != "fomc_statement":
            continue
        vc = reader.read_receipts([entry["receipt_id"]], entry["seq"])[0]
        body = vc.blob_bytes or b""
        old = entry.get("label") or {}
        label: Dict[str, Any] = {
            "expected_class": "valid",
            "coverage": "statement_extractor" if sid in LINEAGED else "collection_only_no_lineage",
            "kind": old.get("kind", "positive"),
            "synthetic": False,
            "split": old.get("split") or SPLIT_OVERRIDE.get(sid, "tuning"),
            "facts": parse_statement(body, entry["url"]),
        }
        if sid in LINEAGED:
            fact = url_to_fact.get(entry["url"])
            assert fact is not None, f"no feed fact for {sid}"
            lineage = {
                "archive_name": "pythia-evidence-corpus",
                "receipt_id": feed_entry["receipt_id"],
                "revision_id": feed_entry["revision_id"],
                "seq": feed_entry["seq"],
                "feed_fact_id": fact.fact_id,
                "feed_blob_sha256": feed_entry["blob_sha256"],
                "feed_item_locator": fact.locator,
                "feed_published_at": fact.published_at,
                "feed_raw_pub_date": fact_value(fact, "raw_pub_date"),
                "parent_bundle_digest": parent_digest,
                "parent_bundle_as_of_seq": feed_entry["seq"],
            }
            label["lineage"] = lineage
            man = {
                "source_url": entry["url"],
                "final_url": entry["url"],
                "blob_sha256": hashlib.sha256(body).hexdigest(),
                "bytes_stored": len(body),
                "transport_outcome": "success",
                "http_status": 200,
                "research_provenance": "research_fixture",
                "issuer": "Federal Reserve Board",
                "parent_feed_reference": lineage,
            }
            result = extract_fed_statement(body, man)
            label["extraction_at_labeling"] = "success" if result.is_success else "rejected"
            if not result.is_success:
                label["labeling_rejections"] = [r.reason for r in result.rejections][:3]
        else:
            label["extraction_at_labeling"] = "not_run_no_lineage"
            label["note"] = (
                "Direct corpus capture without feed lineage: the accepted statement "
                "profile requires parent_feed_reference by design (R3.1 F1); the "
                "measurement verifies refusal without lineage and collection-level facts."
            )
        entry["label"] = label
        print("relabeled:", sid, label["coverage"], label.get("extraction_at_labeling"),
              "| date:", label["facts"].get("release_date_text"), "| range:", label["facts"].get("target_range"))
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print("manifest written")


if __name__ == "__main__":
    main()
