"""Pythia R4: offline measurement of collection quality against the labeled
corpus (docs/research/2026-10-22/r4/corpus_manifest.json + the corpus
archive). Deterministic; no network. Produces per-document outcomes and an
error taxonomy split by the frozen tuning/control split.

Error types (system faults, counted against the pipeline):
  accepted_blocked_page     a blocked/failed capture stored as a document
  accepted_without_lineage  statement extracted without feed lineage
  extraction_rejected_valid lineaged valid statement rejected
  wrong_date / wrong_number extracted critical fact differs from the label
  missing_facts             extractor emitted fewer facts than labeled
  wrong_type_accepted       non-statement extracted as a statement (critical)
  duplicate_missed          syndicated copies not clustered with the original
  integrity_error           archived blob hash differs from the manifest

Oracle checks (contract section 4 reference checks; gaps are reported as
classification_gap, i.e. no pipeline component enforces the rule yet):
  challenge_page_stored, js_shell_unflagged, translation_substitution,
  absurd_number_unflagged
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from python.evaluation.evidence_extract import extract_fed_rss, extract_nyfed_effr
from python.evaluation.evidence_reader import ReadOnlyArchiveReader
from python.evaluation.fed_statement_extract import extract_fed_statement

MEASURE_VERSION = "pythia-corpus-measure-r4/1.0"

RANGE_RE = re.compile(
    r"target range for the federal funds rate[^.]*?(?:to|at) ([\d][\d\-–\u2011/ ]*?) to ([\d][\d\-–\u2011/ ]*?) percent"
)
PERCENT_RE = re.compile(r"(\d{2,4}(?:\.\d+)?)\s*(?:percent|%)")
CHALLENGE_MARKERS = ("cf-turnstile", "hcaptcha", "just a moment", "verify you are human",
                     "attention required! | cloudflare", "are you a robot")


def statement_manifest(entry: Dict[str, Any], body: bytes, lineage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    man: Dict[str, Any] = {
        "source_url": entry["url"],
        "final_url": entry["url"],
        "blob_sha256": hashlib.sha256(body).hexdigest(),
        "bytes_stored": len(body),
        "transport_outcome": "success",
        "http_status": 200,
        "issuer": "Federal Reserve Board",
    }
    if lineage is not None:
        man["research_provenance"] = "research_fixture"
        man["parent_feed_reference"] = lineage
    else:
        man["research_provenance"] = "synthetic_fixture"
        man["synthetic_fixture"] = True
    return man


def range_from_text(text: str) -> Optional[List[str]]:
    m = RANGE_RE.search(text)
    if not m:
        return None
    return [m.group(1).strip().replace("\u2011", "-"), m.group(2).strip().replace("\u2011", "-")]


def visible_text(body: bytes) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def language_shares(text: str) -> Dict[str, float]:
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04FF")
    cjk = sum(1 for ch in text if "\u4E00" <= ch <= "\u9FFF")
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    total = max(1, cyr + cjk + latin)
    return {"cyrillic": cyr / total, "cjk": cjk / total, "latin": latin / total}


class Outcome:
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.checks: List[str] = []
        self.errors: List[str] = []
        self.gaps: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {"source_id": self.source_id, "checks": self.checks,
                "errors": self.errors, "gaps": self.gaps}


def measure(manifest_path: Path, archive_dir: Path) -> Dict[str, Any]:
    manifest = json.loads(manifest_path.read_text())
    reader = ReadOnlyArchiveReader(archive_dir)
    outcomes: Dict[str, Outcome] = {}
    fingerprints: Dict[str, Any] = {}

    def out(sid: str) -> Outcome:
        return outcomes.setdefault(sid, Outcome(sid))

    for sid, entry in sorted(manifest["documents"].items()):
        label = entry["label"]
        o = out(sid)
        # Blocked transports: the pipeline must not hold a body.
        if not entry["complete"]:
            o.checks.append("blocked_transport")
            if label["expected_class"] == "blocked":
                pass  # correctly refused at capture time
            else:
                o.errors.append("accepted_blocked_page")
            continue
        vc = reader.read_receipts([entry["receipt_id"]], entry["seq"])[0]
        body = vc.blob_bytes or b""
        if hashlib.sha256(body).hexdigest() != entry["blob_sha256"]:
            o.errors.append("integrity_error")
            continue
        o.checks.append("integrity")
        fam = entry["family"]
        coverage = label.get("coverage")

        if coverage == "statement_extractor":
            o.checks.append("statement_extraction")
            res = extract_fed_statement(body, statement_manifest(entry, body, label.get("lineage")))
            if not res.is_success:
                o.errors.append("extraction_rejected_valid")
            else:
                doc = res.document
                seg = lambda p: body[p.byte_start:p.byte_end].decode("utf-8", "replace")
                date_text = seg(doc.date_proof)
                if label["facts"].get("release_date_text") and label["facts"]["release_date_text"] not in date_text:
                    o.errors.append("wrong_date")
                rng = range_from_text(" ".join(p.display_text for p in doc.paragraphs))
                if rng != label["facts"].get("target_range"):
                    o.errors.append("wrong_number")
                fingerprints[sid] = (label["facts"].get("release_date_text"), label["facts"].get("target_range"))
        elif fam == "fomc_statement":
            # No lineage: the profile must refuse (R3.1 F1 design).
            o.checks.append("lineage_gate")
            res = extract_fed_statement(body, statement_manifest(entry, body, None) | {"research_provenance": "research_fixture", "synthetic_fixture": False})
            if res.is_success:
                o.errors.append("accepted_without_lineage")
            fingerprints[sid] = (label["facts"].get("release_date_text"), label["facts"].get("target_range"))
        elif coverage == "statement_profile_control" or sid.startswith("neg_syn_"):
            o.checks.append("statement_profile_rejection")
            src_url = entry["url"]
            if "syndication" in sid or sid in ("neg_syn_truncated_stmt", "neg_syn_undated_stmt"):
                src_url = manifest["documents"]["fed_stmt_20240918"]["url"]
            elif sid == "neg_syn_absurd_number":
                src_url = manifest["documents"]["fed_speech_bowman20261014a"]["url"]
            elif sid == "neg_syn_translation_fake":
                src_url = manifest["documents"]["nbs_en_1965466"]["url"]
            syn_entry = dict(entry, url=src_url)
            res = extract_fed_statement(body, statement_manifest(syn_entry, body, None))
            if "syndication" in sid:
                if not res.is_success:
                    o.errors.append("extraction_rejected_valid")
                else:
                    doc = res.document
                    fingerprints[sid] = (
                        label.get("facts", {}).get("release_date_text") or manifest["documents"]["fed_stmt_20240918"]["label"]["facts"]["release_date_text"],
                        range_from_text(" ".join(p.display_text for p in doc.paragraphs)),
                    )
            elif res.is_success:
                o.errors.append("wrong_type_accepted")
        elif coverage == "rss_extractor":
            o.checks.append("rss_extraction")
            facts = extract_fed_rss(vc)
            if len(facts) != label["facts"].get("item_count"):
                o.errors.append("missing_facts")
            with_dates = sum(1 for f in facts if f.published_at)
            if with_dates != label["facts"].get("items_with_pubdate"):
                o.errors.append("wrong_date")
        elif coverage == "effr_extractor":
            o.checks.append("effr_extraction")
            facts = extract_nyfed_effr(vc)
            if len(facts) != label["facts"].get("record_count"):
                o.errors.append("missing_facts")
            else:
                vals = [f.value for f in facts if isinstance(f.value, dict)]
                first = vals[0] if vals else {}
                rate = first.get("percent_rate") or first.get("percentRate")
                if rate is not None and label["facts"].get("first_percent_rate") is not None:
                    if float(rate) != float(label["facts"]["first_percent_rate"]):
                        o.errors.append("wrong_number")
        elif coverage == "collection_only":
            o.checks.append("collection_facts")
            if fam in ("reference_rates", "ai_models_api", "ai_releases_api"):
                try:
                    data = json.loads(body)
                    count = len(data.get("refRates", [])) if isinstance(data, dict) else len(data)
                    if count != label["facts"].get("record_count"):
                        o.errors.append("missing_facts")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    o.errors.append("missing_facts")
            elif fam == "rss_feed":
                import xml.etree.ElementTree as ET

                try:
                    items = ET.fromstring(body).findall(".//item")
                    if len(items) != label["facts"].get("item_count"):
                        o.errors.append("missing_facts")
                except ET.ParseError:
                    o.errors.append("missing_facts")

        # ---- Contract section 4 oracle checks (all complete documents) ----
        text = visible_text(body)
        low = body.decode("utf-8", "replace").lower()
        if any(marker in low for marker in CHALLENGE_MARKERS):
            o.gaps.append("challenge_page_stored")
        if label.get("expected_class") == "unverified":
            o.checks.append("oracle_shell")
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(body, "html.parser")
            longest_p = max((len(p.get_text(" ", strip=True)) for p in soup.find_all("p")), default=0)
            if longest_p >= 200 or len(text) >= 3000:
                o.errors.append("integrity_error")  # label said shell, body carries real content
            else:
                o.gaps.append("js_shell_unflagged")
        if sid == "neg_syn_absurd_number":
            o.checks.append("oracle_bounds")
            big = [float(v) for v in PERCENT_RE.findall(text) if float(v) > 300]
            if big:
                o.gaps.append("absurd_number_unflagged")
        if entry["language"] in ("ru", "zh"):
            shares = language_shares(text)
            key = "cyrillic" if entry["language"] == "ru" else "cjk"
            o.checks.append("oracle_language")
            if shares[key] < 0.3:
                o.gaps.append("translation_substitution")

    # Cluster: syndicated copies must fingerprint-match the original.
    cluster = ["fed_stmt_20240918", "neg_syn_syndication_a", "neg_syn_syndication_b"]
    fps = {sid: fingerprints.get(sid) for sid in cluster}
    if len({json.dumps(v) for v in fps.values()}) != 1:
        for sid in cluster[1:]:
            out(sid).errors.append("duplicate_missed")

    # Aggregate by split.
    report: Dict[str, Any] = {"measure_version": MEASURE_VERSION, "documents": {}, "splits": {}}
    for split in ("tuning", "control"):
        agg: Dict[str, Any] = {"documents": 0, "errors": {}, "gaps": {}, "clean": 0}
        for sid, entry in manifest["documents"].items():
            if entry["label"]["split"] != split:
                continue
            o = outcomes[sid]
            agg["documents"] += 1
            if not o.errors:
                agg["clean"] += 1
            for e in o.errors:
                agg["errors"][e] = agg["errors"].get(e, 0) + 1
            for g in o.gaps:
                agg["gaps"][g] = agg["gaps"].get(g, 0) + 1
        report["splits"][split] = agg
    report["documents"] = {sid: o.to_dict() for sid, o in sorted(outcomes.items())}
    return report


def main() -> None:
    base = Path(__file__).resolve().parents[2] / "docs/research/2026-10-22/r4"
    report = measure(base / "corpus_manifest.json", Path("/home/pythia/pythia-evidence-corpus"))
    out_path = base / "corpus_measurement.json"
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    for split, agg in report["splits"].items():
        print(f"[{split}] documents={agg['documents']} clean={agg['clean']} errors={agg['errors']} gaps={agg['gaps']}")
    print("wrote", out_path)


if __name__ == "__main__":
    main()
