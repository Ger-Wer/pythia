"""Pythia R3.0: Frozen Event Dossier Builder and Canonical Bundle Publisher.

Combines verified read-only evidence captures, deterministic fact extraction,
and explicit event descriptors into an immutable, point-in-time JSON dossier bundle.

Enforces:
1. Strict output-dir separation from raw archive directory.
2. Content-addressable dossier bundle naming (dossier_<digest>.json).
3. Idempotent publication with atomic no-clobber hard link (os.link) and collision rejection.
4. Non-predictive context_only metadata: usable_for_forecast is False.
5. Honest multi-origin grouping without fabricated independence counts.
6. Explicit EventDescriptor validation (no empty IDs/questions, contract status consistency).
7. Frozen extractor and schema versions even for zero-fact dossiers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from python.evaluation.evidence_extract import (
    EXTRACTOR_VERSION,
    FACT_SCHEMA_VERSION,
    Fact,
    RejectionRecord,
    extract_facts_from_captures,
)
from python.evaluation.evidence_reader import (
    ReadOnlyArchiveReader,
    VerifiedCapture,
)
from datetime import datetime
from python.evaluation.source_probe import CANONICAL_SOURCES

DOSSIER_VERSION = "pythia-dossier-r3/0.3"


class DossierError(Exception):
    """Base exception for dossier builder operations."""


@dataclass(frozen=True)
class EventDescriptor:
    """Explicit local event description providing context for the dossier."""

    event_id: str
    question: str
    provenance: str
    deadline: Optional[str] = None
    rules: Optional[str] = None
    contract_status: str = "incomplete"

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(self.question, str) or not self.question.strip():
            raise ValueError("question must be a non-empty string")

        # Explicit provenance required (no silent defaults)
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            raise ValueError("provenance must be an explicit non-empty string")
        if self.provenance not in ("fixture", "operator_supplied"):
            raise ValueError(
                f"provenance '{self.provenance}' is not supported; must be 'fixture' or 'operator_supplied'"
            )

        # Fixture event IDs must be explicitly synthetic
        if self.provenance == "fixture" and not self.event_id.startswith(("fixture_", "fixture-")):
            raise ValueError(
                f"Fixture event_id must be synthetic (e.g. prefix 'fixture_'), got '{self.event_id}'"
            )

        # Nullable string / ISO 8601 deadline validation
        if self.deadline is not None:
            if type(self.deadline) is not str or not self.deadline.strip():
                raise ValueError("deadline must be None or a non-empty ISO 8601 string")
            try:
                dt = datetime.fromisoformat(self.deadline.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    raise ValueError(f"deadline '{self.deadline}' must include explicit timezone offset")
            except Exception as exc:
                raise ValueError(
                    f"deadline '{self.deadline}' is not a valid ISO 8601 datetime string: {exc}"
                ) from exc

        # Nullable string rules validation
        if self.rules is not None:
            if type(self.rules) is not str or not self.rules.strip():
                raise ValueError("rules must be None or a non-empty string")

        # contract_status validation: in R3.0 only 'incomplete' and 'unverified' are supported
        if self.contract_status not in ("incomplete", "unverified"):
            raise ValueError(
                f"contract_status '{self.contract_status}' is not supported in R3.0 "
                f"(supported: 'incomplete', 'unverified')"
            )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize_article_url(url: str) -> str:
    """Conservative URL normalization: preserve path and query verbatim, lowercase scheme/host."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        netloc = parts.netloc.lower()
        return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))
    except Exception:
        return url.strip()


def build_origin_groups(facts: List[Fact], captures: List[VerifiedCapture]) -> List[Dict[str, Any]]:
    """Group facts and observations by origin entity.
    
    Strictly avoids claiming that count of URLs equals count of independent evidence.
    Does not emit independent_evidence_count field.
    """
    groups: Dict[str, Dict[str, Any]] = {}

    for fact in facts:
        if fact.source_id == "nyfed_effr":
            origin_id = "origin:nyfed:effr_rates"
            if origin_id not in groups:
                groups[origin_id] = {
                    "origin_id": origin_id,
                    "publisher": "Federal Reserve Bank of New York",
                    "entity_type": "rate_series",
                    "canonical_url": CANONICAL_SOURCES.get("nyfed_effr", "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json"),
                    "notes": "Official Federal Reserve Bank of New York reference rate publication.",
                    "observations": [],
                }
            obs = {
                "fact_id": fact.fact_id,
                "effective_date": fact.effective_date,
                "rate": fact.value.get("rate"),
                "receipt_ids": fact.receipt_ids,
                "revision_ids": fact.revision_ids,
                "blob_sha256": fact.blob_sha256,
            }
            if obs not in groups[origin_id]["observations"]:
                groups[origin_id]["observations"].append(obs)

        elif fact.source_id in ("fed_monetary_rss", "fed_speeches_rss"):
            article_url = fact.value.get("referenced_article_url", "")
            norm_url = _normalize_article_url(article_url) if article_url else ""
            origin_id = f"origin:fed:article:{norm_url}" if norm_url else "origin:fed:article:unknown"

            if origin_id not in groups:
                groups[origin_id] = {
                    "origin_id": origin_id,
                    "publisher": "Federal Reserve Board",
                    "entity_type": "referenced_article",
                    "canonical_article_url": norm_url or "unknown",
                    "article_fetched": False,
                    "notes": (
                        "Multiple mentions across feeds/snapshots represent the same underlying "
                        "official article; article body has not been fetched or crawled. "
                        "Statistical independence is not claimed or assessed."
                    ),
                    "observations": [],
                }

            obs = {
                "fact_id": fact.fact_id,
                "feed_source_id": fact.source_id,
                "feed_item_title": fact.value.get("title"),
                "feed_guid": fact.value.get("guid"),
                "raw_pub_date": fact.value.get("raw_pub_date"),
                "normalized_published_at": fact.published_at,
                "receipt_ids": fact.receipt_ids,
                "revision_ids": fact.revision_ids,
                "blob_sha256": fact.blob_sha256,
            }
            if obs not in groups[origin_id]["observations"]:
                groups[origin_id]["observations"].append(obs)

    # Sort origin groups deterministically by origin_id
    sorted_groups = []
    for oid in sorted(groups.keys()):
        g = groups[oid]
        g["observations"] = sorted(
            g["observations"],
            key=lambda x: (x.get("fact_id", ""), json.dumps(x, sort_keys=True)),
        )
        sorted_groups.append(g)

    return sorted_groups


def build_event_dossier(
    archive_dir: Path | str,
    as_of_seq: int,
    receipt_ids: List[str],
    event_descriptor: EventDescriptor,
    output_dir: Path | str,
) -> Tuple[Path, Dict[str, Any]]:
    """Build a deterministic frozen event dossier from archive receipts and publish it.
    
    Args:
        archive_dir: Path to existing archive state directory.
        as_of_seq: Sequence cutoff (integer >= 0).
        receipt_ids: Explicit list of receipt IDs to extract.
        event_descriptor: Explicit validated event descriptor.
        output_dir: Output directory for published dossier bundles (must be separate from archive_dir).
        
    Returns:
        Tuple of (published_bundle_path, bundle_dict).
        
    Raises:
        ValueError: On parameter validation failure.
        DossierError: On directory collision, publication race, or storage failure.
    """
    if not isinstance(event_descriptor, EventDescriptor):
        raise ValueError(f"event_descriptor must be an instance of EventDescriptor, got {type(event_descriptor)}")

    resolved_archive = Path(archive_dir).resolve()
    resolved_out = Path(output_dir).resolve()

    # Strict directory separation check (including symlinks)
    if resolved_out == resolved_archive:
        raise ValueError(f"output_dir ({resolved_out}) must not be the same as archive_dir ({resolved_archive})")
    if resolved_archive in resolved_out.parents:
        raise ValueError(f"output_dir ({resolved_out}) must not be located inside archive_dir ({resolved_archive})")
    if resolved_out in resolved_archive.parents:
        raise ValueError(f"archive_dir ({resolved_archive}) must not be located inside output_dir ({resolved_out})")

    resolved_out.mkdir(parents=True, exist_ok=True)
    tmp_out = resolved_out / ".tmp"
    tmp_out.mkdir(parents=True, exist_ok=True)

    # 1. Read and verify captures via read-only reader
    reader = ReadOnlyArchiveReader(resolved_archive)
    verified_captures = reader.read_receipts(receipt_ids=receipt_ids, as_of_seq=as_of_seq)

    # 2. Extract facts and rejections
    extraction_result = extract_facts_from_captures(verified_captures)
    facts = extraction_result.facts
    rejections = extraction_result.rejections

    # Deterministic sorting of facts
    facts_sorted = sorted(
        facts,
        key=lambda f: (
            f.fact_type,
            f.effective_date or "",
            f.source_id,
            f.locator.get("path", ""),
            f.fact_id,
        ),
    )

    # 3. Build origin groups
    origin_groups = build_origin_groups(facts_sorted, verified_captures)

    # 4. Assemble manifest
    unique_rids = sorted(set(receipt_ids))
    receipt_summaries = []
    for cap in verified_captures:
        r = cap.receipt
        receipt_summaries.append(
            {
                "receipt_id": r.receipt_id,
                "seq": r.seq,
                "source_id": r.source_id,
                "initial_url": r.initial_url,
                "final_url": r.final_url,
                "status": r.status,
                "complete": r.complete,
                "http_status": r.http_status,
                "error_msg": r.error_msg,
                "blob_sha256": r.blob_sha256,
                "revision_id": r.revision_id,
                "fetch_started_at": r.fetch_started_at,
                "fetch_completed_at": r.fetch_completed_at,
                "recorded_at": r.recorded_at,
            }
        )

    manifest = {
        "as_of_seq": as_of_seq,
        "dossier_version": DOSSIER_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "fact_schema_version": FACT_SCHEMA_VERSION,
        "requested_receipt_ids": unique_rids,
        "total_requested": len(unique_rids),
        "total_verified": len(verified_captures),
        "total_facts_extracted": len(facts_sorted),
        "total_rejected": len(rejections),
        "receipt_summaries": receipt_summaries,
        "rejected_receipts": [rej.to_dict() for rej in rejections],
    }

    # Coverage limitations tailored to descriptor
    coverage_limitations = [
        "Only 3 canonical sources supported in R3.0 (nyfed_effr, fed_monetary_rss, fed_speeches_rss).",
        "Background and contextual evidence only; does not prove future FOMC decisions or rate targets.",
        "Articles referenced in RSS feeds are not fetched or crawled in R3.0.",
        "No probabilities, betting recommendations, or edge estimates provided.",
    ]
    if event_descriptor.rules is None:
        coverage_limitations.append("No resolution text or market rules provided; contract_status is incomplete.")
    if event_descriptor.deadline is None:
        coverage_limitations.append("No event deadline specified in descriptor.")

    metadata = {
        "dossier_type": "context_only",
        "evidence_status": "insufficient_direct_evidence",
        "usable_for_forecast": False,
        "coverage_limitations": coverage_limitations,
    }

    # 5. Assemble payload for content digest (frozen versions always present)
    dossier_payload = {
        "dossier_version": DOSSIER_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "fact_schema_version": FACT_SCHEMA_VERSION,
        "event_descriptor": event_descriptor.to_dict(),
        "as_of_seq": as_of_seq,
        "requested_receipt_ids": unique_rids,
        "manifest": manifest,
        "facts": [f.to_dict() for f in facts_sorted],
        "origin_groups": origin_groups,
        "metadata": metadata,
    }

    canonical_json_bytes = json.dumps(
        dossier_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    content_digest = hashlib.sha256(canonical_json_bytes).hexdigest()

    # 6. Assemble bundle
    bundle_data = {
        "bundle_digest": content_digest,
        "dossier": dossier_payload,
    }
    bundle_json_text = json.dumps(bundle_data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    bundle_bytes = bundle_json_text.encode("utf-8")

    bundle_path = resolved_out / f"dossier_{content_digest}.json"

    # 7. Atomic no-clobber publication via hard link (os.link)
    fd, tmp_file = tempfile.mkstemp(prefix="dossier_", suffix=".tmp", dir=str(tmp_out))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(bundle_bytes)
            f.flush()
            os.fsync(f.fileno())

        # Attempt atomic no-clobber link to final destination
        try:
            os.link(tmp_file, str(bundle_path))
        except FileExistsError:
            # File already exists: check if content is identical (idempotent replay)
            existing_bytes = bundle_path.read_bytes()
            if existing_bytes == bundle_bytes:
                return bundle_path, bundle_data
            raise DossierError(
                f"Atomic publication conflict: file already exists with different contents at {bundle_path}"
            )
    finally:
        if os.path.exists(tmp_file):
            try:
                os.unlink(tmp_file)
            except OSError:
                pass

    return bundle_path, bundle_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Pythia R3.0: Frozen Event Dossier Builder")
    parser.add_argument(
        "--archive-dir",
        type=Path,
        required=True,
        help="Path to existing archive state directory containing archive.db and blobs/",
    )
    parser.add_argument(
        "--as-of-seq",
        type=int,
        required=True,
        help="Sequence cutoff integer (>= 0)",
    )
    parser.add_argument(
        "--receipt-id",
        dest="receipt_ids",
        action="append",
        required=True,
        help="Receipt ID to extract (repeatable)",
    )
    parser.add_argument(
        "--event-descriptor-json",
        type=str,
        required=True,
        help="Path to JSON file containing EventDescriptor or inline JSON string (mandatory)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory to store published dossier bundles",
    )

    args = parser.parse_args()

    desc_path = Path(args.event_descriptor_json)
    if desc_path.is_file():
        desc_dict = json.loads(desc_path.read_text(encoding="utf-8"))
    else:
        desc_dict = json.loads(args.event_descriptor_json)
    descriptor = EventDescriptor(**desc_dict)

    bundle_path, bundle_data = build_event_dossier(
        archive_dir=args.archive_dir,
        as_of_seq=args.as_of_seq,
        receipt_ids=args.receipt_ids,
        event_descriptor=descriptor,
        output_dir=args.output_dir,
    )

    dossier = bundle_data["dossier"]
    manifest = dossier["manifest"]
    print(f"Published Dossier: {bundle_path}")
    print(f"Bundle Digest:    {bundle_data['bundle_digest']}")
    print(f"Facts Extracted:  {manifest['total_facts_extracted']}")
    print(f"Rejections:       {manifest['total_rejected']}")
    print(f"Origin Groups:    {len(dossier['origin_groups'])}")


if __name__ == "__main__":
    main()
