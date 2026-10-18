"""Pythia R3.0: Frozen Event Dossier Builder and Canonical Bundle Publisher.

Combines verified read-only evidence captures, deterministic fact extraction,
and explicit event descriptors into an immutable, point-in-time JSON dossier bundle.

Enforces:
1. Strict output-dir separation from raw archive directory.
2. Content-addressable dossier bundle naming (dossier_<digest>.json).
3. Idempotent publication with atomic replacement and no-clobber verification.
4. Non-predictive context_only metadata: usable_for_forecast is False.
5. Multi-origin grouping preserving honest lineage across feeds and snapshots.
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
    Fact,
    RejectionRecord,
    extract_facts_from_captures,
)
from python.evaluation.evidence_reader import (
    ReadOnlyArchiveReader,
    VerifiedCapture,
)

DOSSIER_VERSION = "pythia-dossier-r3/0.1"


class DossierError(Exception):
    """Base exception for dossier builder operations."""


@dataclass(frozen=True)
class EventDescriptor:
    """Explicit local event description providing context for the dossier."""

    event_id: str
    question: str
    deadline: Optional[str] = None
    rules: Optional[str] = None
    provenance: str = "fixture"
    contract_status: str = "incomplete"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize_article_url(url: str) -> str:
    """Conservative URL normalization: preserve path and query, lowercase host."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        netloc = parts.netloc.lower()
        path = parts.path
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))
    except Exception:
        return url.strip()


def build_origin_groups(facts: List[Fact], captures: List[VerifiedCapture]) -> List[Dict[str, Any]]:
    """Group facts and observations by honest origin entity to avoid artificial evidence inflation."""
    # Mapping from origin_id -> group dict
    groups: Dict[str, Dict[str, Any]] = {}

    # Map receipt_id -> capture for quick lookup
    cap_map = {c.receipt.receipt_id: c for c in captures}

    for fact in facts:
        if fact.source_id == "nyfed_effr":
            origin_id = "origin:nyfed:effr_rates"
            if origin_id not in groups:
                groups[origin_id] = {
                    "origin_id": origin_id,
                    "publisher": "Federal Reserve Bank of New York",
                    "entity_type": "rate_series",
                    "canonical_url": "https://markets.newyorkfed.org/api/rates/all/latest.json",
                    "independent_evidence_count": 1,
                    "notes": "Official Federal Reserve Bank of New York reference rate publication.",
                    "observations": [],
                }
            # Record observation
            obs = {
                "fact_id": fact.fact_id,
                "effective_date": fact.effective_date,
                "rate": fact.value.get("rate"),
                "receipt_ids": fact.receipt_ids,
                "revision_ids": fact.revision_ids,
                "blob_sha256": fact.blob_sha256,
            }
            # Avoid duplicate observation records in origin group
            if obs not in groups[origin_id]["observations"]:
                groups[origin_id]["observations"].append(obs)

        elif fact.source_id in ("fed_monetary_rss", "fed_speeches_rss"):
            article_url = fact.value.get("referenced_article_url", "")
            norm_url = _normalize_article_url(article_url)
            origin_id = f"origin:fed:article:{norm_url}" if norm_url else f"origin:fed:feed_item:{fact.fact_id}"

            if origin_id not in groups:
                groups[origin_id] = {
                    "origin_id": origin_id,
                    "publisher": "Federal Reserve Board",
                    "entity_type": "referenced_article",
                    "canonical_article_url": norm_url or article_url,
                    "article_fetched": False,
                    "independent_evidence_count": 1,
                    "notes": (
                        "Multiple mentions across feeds/snapshots represent the same underlying "
                        "official article; article body has not been fetched or crawled."
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
        # Sort observations inside each group
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
        event_descriptor: Explicit event descriptor.
        output_dir: Output directory for published dossier bundles (must be separate from archive_dir).
        
    Returns:
        Tuple of (published_bundle_path, bundle_dict).
        
    Raises:
        ValueError: On parameter validation failure.
        DossierError: On directory collision or storage failure.
    """
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
        "requested_receipt_ids": unique_rids,
        "total_requested": len(unique_rids),
        "total_verified": len(verified_captures),
        "total_facts_extracted": len(facts_sorted),
        "total_rejected": len(rejections),
        "receipt_summaries": receipt_summaries,
        "rejected_receipts": [rej.to_dict() for rej in rejections],
    }

    metadata = {
        "dossier_type": "context_only",
        "evidence_status": "insufficient_direct_evidence",
        "usable_for_forecast": False,
        "coverage_limitations": [
            "Only 3 canonical sources supported in R3.0 (nyfed_effr, fed_monetary_rss, fed_speeches_rss).",
            "Background and contextual evidence only; does not prove future FOMC decisions or rate targets.",
            "Articles referenced in RSS feeds are not fetched or crawled in R3.0.",
            "No resolution text or Polymarket contract rules loaded; contract_status is incomplete.",
            "No probabilities, betting recommendations, or edge estimates provided.",
        ],
    }

    # 5. Assemble payload for content digest (without self-referential bundle_digest, wall clocks, or paths)
    dossier_payload = {
        "dossier_version": DOSSIER_VERSION,
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

    # Idempotent write
    if bundle_path.exists():
        existing_bytes = bundle_path.read_bytes()
        if existing_bytes == bundle_bytes:
            return bundle_path, bundle_data
        raise DossierError(
            f"Dossier bundle digest collision with different content at {bundle_path}"
        )

    # Atomic publication
    fd, tmp_file = tempfile.mkstemp(prefix="dossier_", suffix=".tmp", dir=str(tmp_out))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(bundle_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, bundle_path)
    except Exception:
        if os.path.exists(tmp_file):
            os.unlink(tmp_file)
        raise

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
        help="Path to JSON file containing EventDescriptor or inline JSON string",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory to store published dossier bundles",
    )

    args = parser.parse_args()

    # Parse descriptor
    if args.event_descriptor_json:
        desc_path = Path(args.event_descriptor_json)
        if desc_path.is_file():
            desc_dict = json.loads(desc_path.read_text(encoding="utf-8"))
        else:
            desc_dict = json.loads(args.event_descriptor_json)
        descriptor = EventDescriptor(**desc_dict)
    else:
        descriptor = EventDescriptor(
            event_id="fixture_fomc_default",
            question="Will the Federal Reserve adjust the target federal funds rate?",
            contract_status="incomplete",
        )

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
