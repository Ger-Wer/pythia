"""Pythia R3.1.2: Pure Offline Parser and Extractor for Official FOMC Statements.

Extracts structured FOMC statement content from raw HTML fixtures with exact ProofSpan linkages:
- Document profile: fomc_press_release_v1
- Mandatory typed manifest and provenance validation
- Heading extraction: document title, proven page date, release time
- Statement body: ordered paragraphs with clean parsed text and exact raw byte ProofSpans
- Unambiguous hierarchical structural CSS locators
- Single-entity HTML text decoding without double unescaping
- Deterministic canonical bundle publication with atomic no-clobber guarantees
- Strictly zero network: pure offline processing
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse

from bs4 import BeautifulSoup

from python.evaluation.evidence_extract import ProofSpan

STATEMENT_EXTRACTOR_VERSION = "pythia-statement-extractor-r3/0.1.4"
STATEMENT_SCHEMA_VERSION = "pythia-statement-schema-r3/1.2"
STATEMENT_BUNDLE_VERSION = "pythia-statement-bundle-r3/0.1.4"
SUPPORTED_PROFILE = "fomc_press_release_v1"

OFFICIAL_DOMAINS = {"federalreserve.gov", "www.federalreserve.gov"}

US_TIMEZONE_OFFSETS = {
    "EDT": "-04:00",
    "EST": "-05:00",
    "CDT": "-05:00",
    "CST": "-06:00",
    "MDT": "-06:00",
    "MST": "-07:00",
    "PDT": "-07:00",
    "PST": "-08:00",
    "UTC": "+00:00",
    "GMT": "+00:00",
}


class StatementError(Exception):
    """Base exception for statement extraction operations."""


class StatementIntegrityError(StatementError):
    """Raised when proof verification or linkage fails."""


class StatementPublishError(StatementError):
    """Raised when bundle publication encounters a collision or conflict."""


@dataclass(frozen=True)
class StatementParagraph:
    """An individual paragraph within the FOMC statement with exact raw HTML proof."""

    paragraph_index: int
    display_text: str
    role: str
    locator: Dict[str, Any]
    proof: ProofSpan

    def to_dict(self) -> Dict[str, Any]:
        return {
            "paragraph_index": self.paragraph_index,
            "display_text": self.display_text,
            "role": self.role,
            "locator": self.locator,
            "proof": self.proof.to_dict(),
        }


@dataclass(frozen=True)
class StatementDocument:
    """Complete extracted FOMC statement with structural proofs and provenance."""

    statement_id: str
    profile_id: str
    source_url: str
    issuer: str
    title: str
    title_locator: Dict[str, Any]
    title_proof: ProofSpan
    page_date_raw: str
    published_date: Optional[str]
    published_date_precision: Optional[str]
    published_at: Optional[str]
    published_at_provenance: Optional[str]
    date_locator: Dict[str, Any]
    date_proof: ProofSpan
    release_time_raw: Optional[str]
    release_time_locator: Optional[Dict[str, Any]]
    release_time_proof: Optional[ProofSpan]
    container_locator: Dict[str, Any]
    container_proof: ProofSpan
    paragraphs: List[StatementParagraph]
    substantive_statement_text: str
    raw_blob_sha256: str
    parent_feed_reference: Optional[Dict[str, Any]]
    date_diagnostics: Optional[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statement_id": self.statement_id,
            "profile_id": self.profile_id,
            "source_url": self.source_url,
            "issuer": self.issuer,
            "title": self.title,
            "title_locator": self.title_locator,
            "title_proof": self.title_proof.to_dict(),
            "page_date_raw": self.page_date_raw,
            "published_date": self.published_date,
            "published_date_precision": self.published_date_precision,
            "published_at": self.published_at,
            "published_at_provenance": self.published_at_provenance,
            "date_locator": self.date_locator,
            "date_proof": self.date_proof.to_dict(),
            "release_time_raw": self.release_time_raw,
            "release_time_locator": self.release_time_locator,
            "release_time_proof": self.release_time_proof.to_dict() if self.release_time_proof else None,
            "container_locator": self.container_locator,
            "container_proof": self.container_proof.to_dict(),
            "paragraphs": [p.to_dict() for p in self.paragraphs],
            "substantive_statement_text": self.substantive_statement_text,
            "raw_blob_sha256": self.raw_blob_sha256,
            "parent_feed_reference": self.parent_feed_reference,
            "date_diagnostics": self.date_diagnostics,
        }


@dataclass(frozen=True)
class RejectionRecord:
    """Record explaining why a raw HTML fixture was rejected without publishing a positive bundle."""

    status: str
    reason: str
    source_url: Optional[str]
    raw_blob_sha256: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExtractionResult:
    """Aggregate result of statement extraction."""

    document: Optional[StatementDocument]
    rejections: List[RejectionRecord]
    extractor_version: str
    schema_version: str

    @property
    def is_success(self) -> bool:
        return self.document is not None and len(self.rejections) == 0


def _clean_display_text(raw_html_fragment: bytes) -> str:
    """Clean and normalize HTML fragment into human-readable display text.

    BeautifulSoup get_text() handles HTML entities decoding exactly once.
    Do NOT apply secondary html.unescape() which would corrupt literal escaped entities.
    """
    soup = BeautifulSoup(raw_html_fragment, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text()
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned


def _classify_paragraph_role(display_text: str) -> str:
    """Deterministic role classifier for statement paragraphs."""
    lower = display_text.lower()
    if "approved the following statement" in lower:
        return "vote_approval"
    if "decided to raise" in lower or "decided to maintain" in lower or "decided to lower" in lower:
        return "policy_decision"
    if "voting against" in lower:
        return "dissenting_votes"
    if "for media inquiries" in lower:
        return "media_inquiries"
    if "implementation note" in lower:
        return "implementation_note"
    if "economic activity" in lower or "labor market" in lower:
        return "economic_assessment"
    if "inflation" in lower or "dual mandate" in lower or "price stability" in lower:
        return "inflation_mandate"
    return "statement_body"


def _parse_page_date(date_str: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse calendar date string into ISO 8601 date (precision='day')."""
    cleaned = date_str.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.strftime("%Y-%m-%d"), "day"
        except ValueError:
            continue
    return None, None


def _parse_release_time(
    release_str: str,
    published_date: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse release time string if it contains explicit time and timezone.

    Returns (iso_published_at, provenance, diagnostic_reason).
    """
    if not release_str or not published_date:
        return None, None, None

    pattern = re.compile(
        r"(\d{1,2}):(\d{2})\s*(a\.m\.|p\.m\.|am|pm)\s*([A-Z]{3,4})",
        re.IGNORECASE,
    )
    match = pattern.search(release_str)
    if not match:
        return None, None, "no_valid_time_pattern_found"

    hour = int(match.group(1))
    minute = int(match.group(2))
    ampm = match.group(3).lower().replace(".", "")
    tz_abbr = match.group(4).upper()

    if hour < 1 or hour > 12 or minute < 0 or minute > 59:
        return None, None, f"invalid_time_digits: hour={hour}, minute={minute}"

    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0

    tz_offset = US_TIMEZONE_OFFSETS.get(tz_abbr)
    if not tz_offset:
        return None, None, f"unsupported_or_unknown_timezone: {tz_abbr}"

    iso_published_at = f"{published_date}T{hour:02d}:{minute:02d}:00{tz_offset}"
    try:
        datetime.fromisoformat(iso_published_at)
    except ValueError as exc:
        return None, None, f"invalid_iso_timestamp: {exc}"

    return iso_published_at, "header_release_time", None


def _find_closing_tag(blob: bytes, open_pos: int, tag_name: bytes) -> int:
    """Find the exact closing </tag_name> matching opening <tag_name at open_pos in blob.

    Correctly ignores any tag occurrences inside HTML comments (<!-- ... -->).
    Returns the byte offset immediately following </tag_name>, or -1 if not found.
    """
    tag_end = blob.find(b">", open_pos)
    if tag_end == -1:
        return -1

    pos = tag_end + 1
    depth = 1
    pattern = re.compile(rb"</?" + tag_name + rb"(?:\s[^>]*)?>", re.IGNORECASE)

    while depth > 0:
        match = pattern.search(blob, pos)
        if not match:
            return -1

        # Check for HTML comments starting before this match
        c_start = blob.find(b"<!--", pos)
        if c_start != -1 and c_start < match.start():
            c_end = blob.find(b"-->", c_start + 4)
            if c_end == -1:
                return -1  # Malformed unclosed comment
            pos = c_end + 3
            continue

        tag_bytes = match.group(0).lower()
        if tag_bytes.startswith(b"</" + tag_name.lower()):
            depth -= 1
        elif not tag_bytes.endswith(b"/>"):
            depth += 1
        pos = match.end()

    return pos


def _find_matching_closing_div(blob: bytes, open_pos: int) -> int:
    """Find the exact closing </div> matching opening <div at open_pos in blob."""
    return _find_closing_tag(blob, open_pos, b"div")


def _node_byte_start(node: Any, lines: List[str], line_offsets: List[int]) -> int:
    """Calculate exact UTF-8 byte start offset of a BeautifulSoup Tag in raw bytes."""
    line_idx = getattr(node, "sourceline", None)
    col_idx = getattr(node, "sourcepos", None)
    if line_idx is None or col_idx is None or line_idx < 1 or line_idx > len(lines):
        return -1
    return line_offsets[line_idx - 1] + len(lines[line_idx - 1][:col_idx].encode("utf-8"))


def _build_element_selector(el: Any, root_selector: str, root_elem: Any) -> str:
    """Build an unambiguous, hierarchical CSS selector from root_elem down to el."""
    parts = []
    curr = el
    while curr is not None and curr != root_elem:
        parent = curr.parent
        if parent is None:
            break
        siblings = [c for c in parent.children if getattr(c, "name", None) == curr.name]
        # Use object identity 'is' rather than structural equality '==' to avoid duplicate identical siblings sharing index
        idx = next(i for i, c in enumerate(siblings) if c is curr) + 1
        parts.append(f"{curr.name}:nth-of-type({idx})")
        curr = parent
    parts.reverse()
    if parts:
        return f"{root_selector} > {' > '.join(parts)}"
    return root_selector


def extract_fed_statement(
    raw_bytes: bytes,
    manifest: Dict[str, Any],
) -> ExtractionResult:
    """Pure offline extraction of official FOMC statement from raw HTML and manifest.

    Enforces exact ProofSpan byte offsets, independent proof verification, strict
    document profile matching, unambiguous locators, and explicit provenance.
    """
    actual_sha = hashlib.sha256(raw_bytes).hexdigest()

    # 1. Mandatory Manifest Validation
    if not manifest or not isinstance(manifest, dict):
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="invalid_manifest",
                    reason="Manifest is missing, empty, or not a dictionary",
                    source_url=None,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    required_fields = [
        "source_url",
        "final_url",
        "blob_sha256",
        "bytes_stored",
        "transport_outcome",
        "http_status",
        "research_provenance",
    ]
    missing_fields = [f for f in required_fields if f not in manifest or manifest[f] is None]
    if missing_fields:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="invalid_manifest",
                    reason=f"Manifest missing mandatory fields: {missing_fields}",
                    source_url=manifest.get("source_url") if isinstance(manifest.get("source_url"), str) else None,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    source_url = manifest["source_url"]
    final_url = manifest["final_url"]

    # Type validation: URLs must be string
    if not isinstance(source_url, str) or not isinstance(final_url, str):
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="invalid_manifest",
                    reason=f"URL fields must be strings, got source_url={type(source_url).__name__}, final_url={type(final_url).__name__}",
                    source_url=None,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # Type validation: bytes_stored must be integer
    bytes_stored = manifest["bytes_stored"]
    if type(bytes_stored) is not int:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="invalid_manifest",
                    reason=f"Manifest 'bytes_stored' must be integer, got {type(bytes_stored).__name__}",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # Type validation: http_status must be int and 200
    http_status = manifest["http_status"]
    if type(http_status) is not int or http_status != 200:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="transport_failure",
                    reason=f"Transport failed or incomplete: http_status must be 200, got {http_status!r}",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # Check transport outcome
    transport_outcome = manifest["transport_outcome"]
    if transport_outcome != "success":
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="transport_failure",
                    reason=f"Incomplete/failed acquisition: transport_outcome='{transport_outcome}'",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # Explicit transport completeness flags
    if manifest.get("complete") is False or manifest.get("eof_complete") is False:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="transport_failure",
                    reason="Manifest explicitly indicates incomplete transport (complete=False or eof_complete=False)",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # Validate provenance and reject contradictory metadata
    usable_for_forecast = manifest.get("usable_for_forecast")
    if usable_for_forecast is True:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="invalid_manifest",
                    reason="usable_for_forecast cannot be True for offline research fixture",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    research_provenance = manifest["research_provenance"]
    if not isinstance(research_provenance, str) or research_provenance not in (
        "downloaded_research_fixture",
        "synthetic_fixture",
        "research_fixture",
    ):
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="invalid_manifest",
                    reason=f"Invalid or contradictory research_provenance: {research_provenance!r}",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # URL scheme and official domain validation
    parsed_source = urllib.parse.urlsplit(source_url)
    parsed_final = urllib.parse.urlsplit(final_url)
    if parsed_source.scheme.lower() != "https" or parsed_final.scheme.lower() != "https":
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="unsupported_source_url",
                    reason=f"URL scheme must be 'https', got source scheme='{parsed_source.scheme}', final scheme='{parsed_final.scheme}'",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    if (
        parsed_source.netloc.lower() not in OFFICIAL_DOMAINS
        or parsed_final.netloc.lower() not in OFFICIAL_DOMAINS
    ):
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="unsupported_source_url",
                    reason=f"Source ({source_url}) or final ({final_url}) domain is not official Federal Reserve domain",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    for url_label, url_obj in [("source_url", parsed_source), ("final_url", parsed_final)]:
        if not (
            url_obj.path.startswith("/newsevents/pressreleases/monetary")
            or "/pressreleases/monetary" in url_obj.path
        ):
            return ExtractionResult(
                document=None,
                rejections=[
                    RejectionRecord(
                        status="unsupported_source_url",
                        reason=f"{url_label} path '{url_obj.path}' does not match official FOMC press release statement endpoint",
                        source_url=source_url,
                        raw_blob_sha256=actual_sha,
                    )
                ],
                extractor_version=STATEMENT_EXTRACTOR_VERSION,
                schema_version=STATEMENT_SCHEMA_VERSION,
            )

    research_prov = manifest.get("research_provenance")
    synth_flag = manifest.get("synthetic_fixture")

    # Contradiction checks on manifest provenance flags
    if synth_flag is True and research_prov is not None and research_prov != "synthetic_fixture":
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="invalid_manifest",
                    reason=f"Contradictory manifest provenance: synthetic_fixture is True but research_provenance is {research_prov!r}",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    if synth_flag is False and research_prov == "synthetic_fixture":
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="invalid_manifest",
                    reason="Contradictory manifest provenance: synthetic_fixture is False but research_provenance is 'synthetic_fixture'",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    is_synthetic = (
        research_prov == "synthetic_fixture"
        or (synth_flag is True and research_prov is None)
    )

    issuer_val = manifest.get("issuer")
    if issuer_val is None or not isinstance(issuer_val, str) or isinstance(issuer_val, bool) or not issuer_val.strip():
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="invalid_manifest",
                    reason="Manifest must contain non-empty string 'issuer' (inventing default issuer from missing data is forbidden)",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    if issuer_val not in ("Federal Reserve Board", "Federal Open Market Committee"):
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="invalid_manifest",
                    reason=f"Contradictory or unsupported issuer in manifest: {issuer_val!r}",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    parent_feed_val = manifest.get("parent_feed_reference")
    if is_synthetic:
        if parent_feed_val is not None:
            if not isinstance(parent_feed_val, dict) or isinstance(parent_feed_val, bool) or len(parent_feed_val) == 0:
                return ExtractionResult(
                    document=None,
                    rejections=[
                        RejectionRecord(
                            status="invalid_manifest",
                            reason="parent_feed_reference must be a non-empty dictionary when provided",
                            source_url=source_url,
                            raw_blob_sha256=actual_sha,
                        )
                    ],
                    extractor_version=STATEMENT_EXTRACTOR_VERSION,
                    schema_version=STATEMENT_SCHEMA_VERSION,
                )
            for k, v in parent_feed_val.items():
                if isinstance(v, bool) or isinstance(v, (int, float, list, tuple)):
                    return ExtractionResult(
                        document=None,
                        rejections=[
                            RejectionRecord(
                                status="invalid_manifest",
                                reason=f"Synthetic parent_feed_reference field {k!r} has invalid type: {type(v).__name__}",
                                source_url=source_url,
                                raw_blob_sha256=actual_sha,
                            )
                        ],
                        extractor_version=STATEMENT_EXTRACTOR_VERSION,
                        schema_version=STATEMENT_SCHEMA_VERSION,
                    )
                if isinstance(v, str) and not v.strip():
                    return ExtractionResult(
                        document=None,
                        rejections=[
                            RejectionRecord(
                                status="invalid_manifest",
                                reason=f"Synthetic parent_feed_reference field {k!r} cannot be empty string",
                                source_url=source_url,
                                raw_blob_sha256=actual_sha,
                            )
                        ],
                        extractor_version=STATEMENT_EXTRACTOR_VERSION,
                        schema_version=STATEMENT_SCHEMA_VERSION,
                    )
                if k == "feed_item_locator" and isinstance(v, dict):
                    if "type" not in v or "path" not in v:
                        return ExtractionResult(
                            document=None,
                            rejections=[
                                RejectionRecord(
                                    status="invalid_manifest",
                                    reason="Synthetic feed_item_locator must contain 'type' and 'path'",
                                    source_url=source_url,
                                    raw_blob_sha256=actual_sha,
                                )
                            ],
                            extractor_version=STATEMENT_EXTRACTOR_VERSION,
                            schema_version=STATEMENT_SCHEMA_VERSION,
                        )
        else:
            parent_feed_val = {"fixture_kind": "synthetic_fixture"}
    else:
        if parent_feed_val is None or not isinstance(parent_feed_val, dict) or isinstance(parent_feed_val, bool) or len(parent_feed_val) == 0:
            return ExtractionResult(
                document=None,
                rejections=[
                    RejectionRecord(
                        status="invalid_manifest",
                        reason="Real research manifest missing required 'parent_feed_reference' provenance linkage",
                        source_url=source_url,
                        raw_blob_sha256=actual_sha,
                    )
                ],
                extractor_version=STATEMENT_EXTRACTOR_VERSION,
                schema_version=STATEMENT_SCHEMA_VERSION,
            )

        # 1. Validate common mandatory lineage fields
        common_required = ["feed_fact_id", "feed_blob_sha256", "feed_item_locator"]
        for field in common_required:
            if field not in parent_feed_val or parent_feed_val[field] is None:
                return ExtractionResult(
                    document=None,
                    rejections=[
                        RejectionRecord(
                            status="invalid_manifest",
                            reason=f"parent_feed_reference missing mandatory lineage field: {field!r}",
                            source_url=source_url,
                            raw_blob_sha256=actual_sha,
                        )
                    ],
                    extractor_version=STATEMENT_EXTRACTOR_VERSION,
                    schema_version=STATEMENT_SCHEMA_VERSION,
                )

        fact_id = parent_feed_val["feed_fact_id"]
        if not isinstance(fact_id, str) or isinstance(fact_id, bool) or not fact_id.strip():
            return ExtractionResult(
                document=None,
                rejections=[
                    RejectionRecord(
                        status="invalid_manifest",
                        reason=f"parent_feed_reference field 'feed_fact_id' must be a non-empty string, got {type(fact_id).__name__}",
                        source_url=source_url,
                        raw_blob_sha256=actual_sha,
                    )
                ],
                extractor_version=STATEMENT_EXTRACTOR_VERSION,
                schema_version=STATEMENT_SCHEMA_VERSION,
            )

        feed_blob_sha = parent_feed_val["feed_blob_sha256"]
        if (
            not isinstance(feed_blob_sha, str)
            or isinstance(feed_blob_sha, bool)
            or len(feed_blob_sha) != 64
            or not all(c in "0123456789abcdefABCDEF" for c in feed_blob_sha)
        ):
            return ExtractionResult(
                document=None,
                rejections=[
                    RejectionRecord(
                        status="invalid_manifest",
                        reason=f"parent_feed_reference field 'feed_blob_sha256' must be a valid 64-char hex SHA-256 string, got {type(feed_blob_sha).__name__}",
                        source_url=source_url,
                        raw_blob_sha256=actual_sha,
                    )
                ],
                extractor_version=STATEMENT_EXTRACTOR_VERSION,
                schema_version=STATEMENT_SCHEMA_VERSION,
            )

        locator = parent_feed_val["feed_item_locator"]
        if not isinstance(locator, dict) or isinstance(locator, bool) or len(locator) == 0:
            return ExtractionResult(
                document=None,
                rejections=[
                    RejectionRecord(
                        status="invalid_manifest",
                        reason=f"parent_feed_reference field 'feed_item_locator' must be a structural dictionary, got {type(locator).__name__}",
                        source_url=source_url,
                        raw_blob_sha256=actual_sha,
                    )
                ],
                extractor_version=STATEMENT_EXTRACTOR_VERSION,
                schema_version=STATEMENT_SCHEMA_VERSION,
            )
        loc_type = locator.get("type")
        loc_path = locator.get("path")
        if not isinstance(loc_type, str) or isinstance(loc_type, bool) or not loc_type.strip():
            return ExtractionResult(
                document=None,
                rejections=[
                    RejectionRecord(
                        status="invalid_manifest",
                        reason=f"feed_item_locator 'type' must be non-empty string, got {type(loc_type).__name__}",
                        source_url=source_url,
                        raw_blob_sha256=actual_sha,
                    )
                ],
                extractor_version=STATEMENT_EXTRACTOR_VERSION,
                schema_version=STATEMENT_SCHEMA_VERSION,
            )
        if not isinstance(loc_path, str) or isinstance(loc_path, bool) or not loc_path.strip():
            return ExtractionResult(
                document=None,
                rejections=[
                    RejectionRecord(
                        status="invalid_manifest",
                        reason=f"feed_item_locator 'path' must be non-empty string, got {type(loc_path).__name__}",
                        source_url=source_url,
                        raw_blob_sha256=actual_sha,
                    )
                ],
                extractor_version=STATEMENT_EXTRACTOR_VERSION,
                schema_version=STATEMENT_SCHEMA_VERSION,
            )

        # 2. Check Context lineage: Form A (archive_name + parent_bundle_digest) OR Form B (receipt_id / feed_receipt_id + revision_id / feed_revision_id)
        has_form_a = "archive_name" in parent_feed_val and "parent_bundle_digest" in parent_feed_val
        has_receipt = "receipt_id" in parent_feed_val or "feed_receipt_id" in parent_feed_val
        has_revision = "revision_id" in parent_feed_val or "feed_revision_id" in parent_feed_val
        has_form_b = has_receipt and has_revision

        if not has_form_a and not has_form_b:
            return ExtractionResult(
                document=None,
                rejections=[
                    RejectionRecord(
                        status="invalid_manifest",
                        reason="parent_feed_reference must satisfy either archive/bundle lineage (archive_name, parent_bundle_digest) or receipt/revision lineage (feed_receipt_id/receipt_id, feed_revision_id/revision_id)",
                        source_url=source_url,
                        raw_blob_sha256=actual_sha,
                    )
                ],
                extractor_version=STATEMENT_EXTRACTOR_VERSION,
                schema_version=STATEMENT_SCHEMA_VERSION,
            )

        # Validate Form A if present or partially present
        if "archive_name" in parent_feed_val or "parent_bundle_digest" in parent_feed_val:
            arc_name = parent_feed_val.get("archive_name")
            if not isinstance(arc_name, str) or isinstance(arc_name, bool) or not arc_name.strip():
                return ExtractionResult(
                    document=None,
                    rejections=[
                        RejectionRecord(
                            status="invalid_manifest",
                            reason=f"parent_feed_reference field 'archive_name' must be non-empty string, got {type(arc_name).__name__}",
                            source_url=source_url,
                            raw_blob_sha256=actual_sha,
                        )
                    ],
                    extractor_version=STATEMENT_EXTRACTOR_VERSION,
                    schema_version=STATEMENT_SCHEMA_VERSION,
                )
            bundle_digest = parent_feed_val.get("parent_bundle_digest")
            if (
                not isinstance(bundle_digest, str)
                or isinstance(bundle_digest, bool)
                or len(bundle_digest) != 64
                or not all(c in "0123456789abcdefABCDEF" for c in bundle_digest)
            ):
                return ExtractionResult(
                    document=None,
                    rejections=[
                        RejectionRecord(
                            status="invalid_manifest",
                            reason=f"parent_feed_reference field 'parent_bundle_digest' must be valid 64-char hex SHA-256 string, got {type(bundle_digest).__name__}",
                            source_url=source_url,
                            raw_blob_sha256=actual_sha,
                        )
                    ],
                    extractor_version=STATEMENT_EXTRACTOR_VERSION,
                    schema_version=STATEMENT_SCHEMA_VERSION,
                )

        # Validate Form B if present or partially present
        if has_receipt or has_revision or "source_id" in parent_feed_val:
            rec_id = parent_feed_val.get("receipt_id")
            feed_rec_id = parent_feed_val.get("feed_receipt_id")
            if rec_id is not None and feed_rec_id is not None and rec_id != feed_rec_id:
                return ExtractionResult(
                    document=None,
                    rejections=[
                        RejectionRecord(
                            status="invalid_manifest",
                            reason=f"Contradictory receipt identifier aliases: receipt_id={rec_id!r} vs feed_receipt_id={feed_rec_id!r}",
                            source_url=source_url,
                            raw_blob_sha256=actual_sha,
                        )
                    ],
                    extractor_version=STATEMENT_EXTRACTOR_VERSION,
                    schema_version=STATEMENT_SCHEMA_VERSION,
                )
            receipt_val = rec_id if rec_id is not None else feed_rec_id
            if has_form_b and (not isinstance(receipt_val, str) or isinstance(receipt_val, bool) or not receipt_val.strip()):
                return ExtractionResult(
                    document=None,
                    rejections=[
                        RejectionRecord(
                            status="invalid_manifest",
                            reason=f"Receipt identifier must be non-empty string, got {type(receipt_val).__name__}",
                            source_url=source_url,
                            raw_blob_sha256=actual_sha,
                        )
                    ],
                    extractor_version=STATEMENT_EXTRACTOR_VERSION,
                    schema_version=STATEMENT_SCHEMA_VERSION,
                )

            rev_id = parent_feed_val.get("revision_id")
            feed_rev_id = parent_feed_val.get("feed_revision_id")
            if rev_id is not None and feed_rev_id is not None and rev_id != feed_rev_id:
                return ExtractionResult(
                    document=None,
                    rejections=[
                        RejectionRecord(
                            status="invalid_manifest",
                            reason=f"Contradictory revision identifier aliases: revision_id={rev_id!r} vs feed_revision_id={feed_rev_id!r}",
                            source_url=source_url,
                            raw_blob_sha256=actual_sha,
                        )
                    ],
                    extractor_version=STATEMENT_EXTRACTOR_VERSION,
                    schema_version=STATEMENT_SCHEMA_VERSION,
                )
            revision_val = rev_id if rev_id is not None else feed_rev_id
            if has_form_b and (not isinstance(revision_val, str) or isinstance(revision_val, bool) or not revision_val.strip()):
                return ExtractionResult(
                    document=None,
                    rejections=[
                        RejectionRecord(
                            status="invalid_manifest",
                            reason=f"Revision identifier must be non-empty string, got {type(revision_val).__name__}",
                            source_url=source_url,
                            raw_blob_sha256=actual_sha,
                        )
                    ],
                    extractor_version=STATEMENT_EXTRACTOR_VERSION,
                    schema_version=STATEMENT_SCHEMA_VERSION,
                )

            source_id = parent_feed_val.get("source_id")
            if source_id is not None and (not isinstance(source_id, str) or isinstance(source_id, bool) or not source_id.strip()):
                return ExtractionResult(
                    document=None,
                    rejections=[
                        RejectionRecord(
                            status="invalid_manifest",
                            reason=f"parent_feed_reference field 'source_id' must be non-empty string when present, got {type(source_id).__name__}",
                            source_url=source_url,
                            raw_blob_sha256=actual_sha,
                        )
                    ],
                    extractor_version=STATEMENT_EXTRACTOR_VERSION,
                    schema_version=STATEMENT_SCHEMA_VERSION,
                )

    # Verify blob integrity against manifest
    manifest_sha = manifest["blob_sha256"]
    if actual_sha != manifest_sha:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="digest_mismatch",
                    reason=f"Raw blob SHA-256 ({actual_sha}) does not match manifest blob_sha256 ({manifest_sha})",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    expected_bytes = manifest["bytes_stored"]
    if len(raw_bytes) != expected_bytes:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="size_mismatch",
                    reason=f"Raw blob size ({len(raw_bytes)}) does not match manifest bytes_stored ({expected_bytes})",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # 2. Check UTF-8 decodability (accounting for optional UTF-8 BOM)
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="encoding_error",
                    reason=f"Raw blob is not valid UTF-8: {exc}",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    raw_lines = raw_text.splitlines(keepends=True)
    line_byte_offsets = [0]
    for line in raw_lines:
        line_byte_offsets.append(line_byte_offsets[-1] + len(line.encode("utf-8")))

    # 3. Detect bot challenges / JS-only walls
    lower_raw = raw_text.lower()
    if (
        "challenge-running" in lower_raw
        or "cf-browser-verification" in lower_raw
        or "<title>just a moment...</title>" in lower_raw
        or "validating browser" in lower_raw
    ):
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="blocked_or_challenge",
                    reason="Raw HTML represents a challenge or verification barrier page",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # 4. Check DOM structural containers using BeautifulSoup
    soup = BeautifulSoup(raw_text, "html.parser")
    article_div = soup.find("div", id="article")
    if not article_div:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="unsupported_structure",
                    reason="Missing required #article container in HTML",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    heading_div = article_div.find("div", class_=lambda c: c and "heading" in c.split())
    if not heading_div:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="unsupported_structure",
                    reason="Missing required .heading container within #article",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    title_elem = heading_div.find(["h1", "h2", "h3"], class_="title")
    if not title_elem:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="unsupported_structure",
                    reason="Missing required title element (.title) within heading",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    title_text = title_elem.get_text(strip=True)
    lower_title = title_text.lower()

    # Reject non-statement document types
    unsupported_markers = [
        "minutes",
        "projections",
        "discount rate",
        "speech",
        "testimony",
        "leadership",
        "task force",
        "advisory council",
        "financial stability report",
    ]
    for marker in unsupported_markers:
        if marker in lower_title:
            return ExtractionResult(
                document=None,
                rejections=[
                    RejectionRecord(
                        status="unsupported_document_type",
                        reason=f"Document title '{title_text}' indicates unsupported document type ({marker!r})",
                        source_url=source_url,
                        raw_blob_sha256=actual_sha,
                    )
                ],
                extractor_version=STATEMENT_EXTRACTOR_VERSION,
                schema_version=STATEMENT_SCHEMA_VERSION,
            )

    if "fomc statement" not in lower_title and "monetary policy statement" not in lower_title:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="unsupported_document_type",
                    reason=f"Document title '{title_text}' does not match supported FOMC statement profile",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # 5. Extract date and release time
    date_elem = heading_div.find("p", class_="article__time")
    if not date_elem:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="unsupported_structure",
                    reason="Missing required date element (p.article__time) in heading",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    page_date_raw = date_elem.get_text(strip=True)
    published_date, published_date_precision = _parse_page_date(page_date_raw)
    if not published_date:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="invalid_date",
                    reason=f"Could not parse calendar date from page text '{page_date_raw}'",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    release_time_elem = heading_div.find("p", class_="releaseTime")
    release_time_raw = None
    if release_time_elem:
        release_text = ""
        for item in release_time_elem.children:
            if isinstance(item, str):
                release_text += item
        release_time_raw = re.sub(r"\s+", " ", release_text).strip() or None

    published_at, published_at_provenance, time_diag = _parse_release_time(release_time_raw or "", published_date)

    # Parent feed date comparison diagnostic
    parent_feed = manifest.get("parent_feed_reference") or {}
    feed_pub_at = parent_feed.get("feed_published_at")
    feed_raw_pub = parent_feed.get("feed_raw_pub_date")
    date_diagnostics = None

    if feed_pub_at and published_date:
        mismatch_detected = False
        mismatch_reason = None
        feed_date_str = feed_pub_at[:10]

        if published_at:
            try:
                page_dt = datetime.fromisoformat(published_at).astimezone(timezone.utc)
                feed_dt = datetime.fromisoformat(feed_pub_at).astimezone(timezone.utc)
                if page_dt.date() != feed_dt.date():
                    mismatch_detected = True
                    mismatch_reason = (
                        f"Page UTC date ({page_dt.date().isoformat()}) differs from "
                        f"feed UTC date ({feed_dt.date().isoformat()})"
                    )
            except Exception:
                if feed_date_str != published_date:
                    mismatch_detected = True
                    mismatch_reason = f"Page calendar date ({published_date}) differs from feed date ({feed_date_str})"
        else:
            feed_dt_utc_date = None
            try:
                feed_dt_utc_date = datetime.fromisoformat(feed_pub_at).astimezone(timezone.utc).date().isoformat()
            except Exception:
                pass
            if published_date != feed_date_str and published_date != feed_dt_utc_date:
                mismatch_detected = True
                mismatch_reason = f"Page calendar date ({published_date}) differs from parent feed date ({feed_date_str})"

        if mismatch_detected:
            date_diagnostics = {
                "date_mismatch_detected": True,
                "page_published_date": published_date,
                "feed_date": feed_date_str,
                "feed_published_at": feed_pub_at,
                "feed_raw_pub_date": feed_raw_pub,
                "time_diagnostic": time_diag,
                "reason": mismatch_reason,
            }
        else:
            date_diagnostics = {
                "date_mismatch_detected": False,
                "page_published_date": published_date,
                "feed_date": feed_date_str,
                "feed_published_at": feed_pub_at,
                "time_diagnostic": time_diag,
            }
    elif time_diag:
        date_diagnostics = {
            "date_mismatch_detected": False,
            "page_published_date": published_date,
            "time_diagnostic": time_diag,
        }

    # 6. Locate Statement Body Container in DOM
    body_div = None
    for child in article_div.find_all("div", recursive=False):
        classes = child.get("class", [])
        if "col-xs-12" in classes and "col-sm-8" in classes and "col-md-8" in classes and "heading" not in classes:
            body_div = child
            break

    if not body_div:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="unsupported_structure",
                    reason="Missing statement body container div.col-xs-12.col-sm-8.col-md-8 in #article",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # 7. Exact byte locating in raw_bytes using DOM sourceline/sourcepos
    date_start = _node_byte_start(date_elem, raw_lines, line_byte_offsets)
    if date_start == -1:
        raise StatementIntegrityError("Failed to calculate date element byte start position")
    date_end = _find_closing_tag(raw_bytes, date_start, b"p")
    if date_end == -1:
        raise StatementIntegrityError("Failed to locate closing </p> for date element in raw bytes")
    date_slice = raw_bytes[date_start:date_end]
    date_proof = ProofSpan(
        byte_start=date_start,
        byte_end=date_end,
        quote_sha256=hashlib.sha256(date_slice).hexdigest(),
        raw_blob_sha256=actual_sha,
        raw_quote_text=date_slice.decode("utf-8"),
    )
    date_locator = {"type": "css_selector", "selector": "#article > div.heading p.article__time"}

    # Locate title_proof directly at title_elem
    title_start = _node_byte_start(title_elem, raw_lines, line_byte_offsets)
    if title_start == -1:
        raise StatementIntegrityError("Failed to calculate title element byte start position")
    title_end = _find_closing_tag(raw_bytes, title_start, title_elem.name.encode("utf-8"))
    if title_end == -1:
        raise StatementIntegrityError(f"Failed to locate closing </{title_elem.name}> for title element in raw bytes")
    title_slice = raw_bytes[title_start:title_end]
    title_proof = ProofSpan(
        byte_start=title_start,
        byte_end=title_end,
        quote_sha256=hashlib.sha256(title_slice).hexdigest(),
        raw_blob_sha256=actual_sha,
        raw_quote_text=title_slice.decode("utf-8"),
    )
    title_locator = {
        "type": "css_selector",
        "selector": f"#article > div.heading {title_elem.name}.title",
    }

    # Locate release_time_proof if present directly at release_time_elem
    release_time_proof = None
    release_time_locator = None
    if release_time_elem:
        rel_start = _node_byte_start(release_time_elem, raw_lines, line_byte_offsets)
        if rel_start != -1:
            rel_end = _find_closing_tag(raw_bytes, rel_start, b"p")
            if rel_end == -1:
                rel_fallback = raw_bytes.find(b"</p>", rel_start)
                if rel_fallback != -1:
                    rel_end = rel_fallback + 4
            if rel_end != -1:
                rel_slice = raw_bytes[rel_start:rel_end]
                release_time_proof = ProofSpan(
                    byte_start=rel_start,
                    byte_end=rel_end,
                    quote_sha256=hashlib.sha256(rel_slice).hexdigest(),
                    raw_blob_sha256=actual_sha,
                    raw_quote_text=rel_slice.decode("utf-8"),
                )
                release_time_locator = {
                    "type": "css_selector",
                    "selector": "#article > div.heading p.releaseTime",
                }

    # Locate statement container in raw bytes
    cont_start = _node_byte_start(body_div, raw_lines, line_byte_offsets)
    if cont_start == -1:
        raise StatementIntegrityError("Failed to calculate statement container byte start position")
    cont_end = _find_matching_closing_div(raw_bytes, cont_start)
    if cont_end == -1:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="unsupported_structure",
                    reason="Unbalanced closing </div> for statement container",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    container_slice = raw_bytes[cont_start:cont_end]
    container_proof = ProofSpan(
        byte_start=cont_start,
        byte_end=cont_end,
        quote_sha256=hashlib.sha256(container_slice).hexdigest(),
        raw_blob_sha256=actual_sha,
        raw_quote_text=container_slice.decode("utf-8"),
    )
    container_locator = {
        "type": "css_selector",
        "selector": "#article > div.col-xs-12.col-sm-8.col-md-8:not(.heading)",
    }

    # 8. Extract paragraph items within container slice with unambiguous locators
    dom_p_tags = [p for p in body_div.find_all("p") if _clean_display_text(p.encode("utf-8"))]
    paragraphs: List[StatementParagraph] = []
    substantive_paras: List[str] = []

    for idx, p_dom in enumerate(dom_p_tags):
        p_start = _node_byte_start(p_dom, raw_lines, line_byte_offsets)
        if p_start == -1:
            raise StatementIntegrityError(f"Failed to calculate paragraph {idx} byte start position")
        p_end = _find_closing_tag(raw_bytes, p_start, b"p")
        if p_end == -1:
            raise StatementIntegrityError(f"Failed to locate closing </p> for paragraph {idx}")
        p_raw = raw_bytes[p_start:p_end]
        display_text = _clean_display_text(p_raw)

        # Verify paragraph display text matches DOM node text completely
        dom_display = _clean_display_text(str(p_dom).encode("utf-8"))
        if display_text != dom_display:
            raise StatementIntegrityError(
                f"Paragraph {idx} display text does not match DOM node text: {display_text!r} vs {dom_display!r}"
            )

        p_proof = ProofSpan(
            byte_start=p_start,
            byte_end=p_end,
            quote_sha256=hashlib.sha256(p_raw).hexdigest(),
            raw_blob_sha256=actual_sha,
            raw_quote_text=p_raw.decode("utf-8"),
        )

        p_selector = _build_element_selector(
            p_dom,
            root_selector="#article > div.col-xs-12.col-sm-8.col-md-8:not(.heading)",
            root_elem=body_div,
        )

        matched_nodes = soup.select(p_selector)
        if len(matched_nodes) != 1 or matched_nodes[0] is not p_dom:
            raise StatementIntegrityError(
                f"Paragraph selector '{p_selector}' does not resolve uniquely to the target DOM node (matches {len(matched_nodes)})"
            )

        p_locator = {
            "type": "css_selector",
            "selector": p_selector,
        }
        p_role = _classify_paragraph_role(display_text)
        paragraphs.append(
            StatementParagraph(
                paragraph_index=len(paragraphs),
                display_text=display_text,
                role=p_role,
                locator=p_locator,
                proof=p_proof,
            )
        )

        if p_role not in ("media_inquiries", "implementation_note"):
            substantive_paras.append(display_text)

    # Verify all paragraph spans are fully contained within container span
    for p in paragraphs:
        if not (container_proof.byte_start <= p.proof.byte_start < p.proof.byte_end <= container_proof.byte_end):
            raise StatementIntegrityError(
                f"Paragraph span [{p.proof.byte_start}, {p.proof.byte_end}] is not fully contained within container span [{container_proof.byte_start}, {container_proof.byte_end}]"
            )

    # 9. Negative Body Validation: Check for JS-only barriers and unsupported document types
    body_text_lower = " ".join(p.display_text.lower() for p in paragraphs)
    if (
        "enable javascript to view this article" in body_text_lower
        or "enable javascript and cookies to continue" in body_text_lower
        or "please turn javascript on" in body_text_lower
        or "validating browser" in body_text_lower
    ):
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="blocked_or_challenge",
                    reason="Article body contains a JavaScript-only barrier or bot challenge",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # Reject unsupported document bodies (e.g. task force, supervision, minutes, projections, speeches)
    unsupported_body_markers = [
        "task force",
        "taskforce",
        "task-force",
        "bank supervision",
        "supervision on bank",
        "vice chair for supervision",
        "announced the formation",
        "formation of a task force",
        "announced the appointment",
        "appointed",
        "enforcement action",
        "regulatory enforcement",
        "discount rate meetings",
        "discount rate minutes",
        "minutes of the board of governors",
        "minutes of the federal open market committee",
        "summary of economic projections",
        "advisory council meeting",
        "community advisory council",
        "testimony of",
        "statement before the",
        "joint press release",
        "joint agency",
    ]
    for marker in unsupported_body_markers:
        if marker in body_text_lower:
            return ExtractionResult(
                document=None,
                rejections=[
                    RejectionRecord(
                        status="unsupported_document_type",
                        reason=f"Article body indicates unsupported document type ({marker!r})",
                        source_url=source_url,
                        raw_blob_sha256=actual_sha,
                    )
                ],
                extractor_version=STATEMENT_EXTRACTOR_VERSION,
                schema_version=STATEMENT_SCHEMA_VERSION,
            )

    if len(paragraphs) == 0 or len(substantive_paras) == 0:
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="empty_content",
                    reason="Statement container contains zero substantive paragraphs",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # Positive FOMC statement profile validation:
    # An official FOMC statement body must contain:
    # 1. Committee institutional actor reference ("federal open market committee", "the committee", or "fomc")
    # 2. Monetary policy stance / decision / mandate language ("target range", "federal funds rate",
    #    "monetary policy", "policy action", "decided to maintain", "decided to raise", "decided to lower",
    #    "decided to reduce", "decided to keep", "dual mandate", "price stability", "maximum employment",
    #    "approved the following statement")
    has_committee_actor = (
        "the committee" in body_text_lower
        or "federal open market committee" in body_text_lower
        or "fomc" in body_text_lower
    )

    policy_decision_markers = [
        "target range",
        "federal funds rate",
        "monetary policy",
        "policy action",
        "decided to maintain",
        "decided to raise",
        "decided to lower",
        "decided to reduce",
        "decided to keep",
        "dual mandate",
        "price stability",
        "maximum employment",
        "approved the statement",
        "approved the following statement",
        "ample reserves",
        "rates maintained",
    ]
    has_policy_action = any(m in body_text_lower for m in policy_decision_markers)

    total_body_chars = sum(len(p.display_text.strip()) for p in paragraphs)
    if total_body_chars < 30 or not (has_committee_actor and has_policy_action):
        return ExtractionResult(
            document=None,
            rejections=[
                RejectionRecord(
                    status="unsupported_document_type",
                    reason=f"Article body does not match supported FOMC statement profile ({SUPPORTED_PROFILE!r}): missing Committee policy decision or stance terms",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            ],
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # 10. Verify ALL ProofSpans independently before assembling document
    for p in paragraphs:
        if not p.proof.verify_against_blob(raw_bytes):
            raise StatementIntegrityError(f"Paragraph proof failed verification at index {p.paragraph_index}")
    if not container_proof.verify_against_blob(raw_bytes):
        raise StatementIntegrityError("Container proof failed verification")
    if not date_proof.verify_against_blob(raw_bytes):
        raise StatementIntegrityError("Date proof failed verification")
    if not title_proof.verify_against_blob(raw_bytes):
        raise StatementIntegrityError("Title proof failed verification")
    if release_time_proof and not release_time_proof.verify_against_blob(raw_bytes):
        raise StatementIntegrityError("Release time proof failed verification")

    # 11. Compute deterministic Statement ID
    substantive_text = "\n\n".join(substantive_paras)
    id_payload = {
        "source_url": source_url,
        "blob_sha256": actual_sha,
        "published_date": published_date,
        "title": title_text,
        "substantive_text_sha256": hashlib.sha256(substantive_text.encode("utf-8")).hexdigest(),
    }
    canon_id = json.dumps(id_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    statement_id = f"stmt-{hashlib.sha256(canon_id).hexdigest()[:16]}"

    document = StatementDocument(
        statement_id=statement_id,
        profile_id=SUPPORTED_PROFILE,
        source_url=source_url,
        issuer=issuer_val,
        title=title_text,
        title_locator=title_locator,
        title_proof=title_proof,
        page_date_raw=page_date_raw,
        published_date=published_date,
        published_date_precision=published_date_precision,
        published_at=published_at,
        published_at_provenance=published_at_provenance,
        date_locator=date_locator,
        date_proof=date_proof,
        release_time_raw=release_time_raw,
        release_time_locator=release_time_locator,
        release_time_proof=release_time_proof,
        container_locator=container_locator,
        container_proof=container_proof,
        paragraphs=paragraphs,
        substantive_statement_text=substantive_text,
        raw_blob_sha256=actual_sha,
        parent_feed_reference=parent_feed_val,
        date_diagnostics=date_diagnostics,
    )

    return ExtractionResult(
        document=document,
        rejections=[],
        extractor_version=STATEMENT_EXTRACTOR_VERSION,
        schema_version=STATEMENT_SCHEMA_VERSION,
    )


def build_fed_statement_bundle(
    fixture_path: Path | str,
    manifest_path: Path | str,
    output_dir: Path | str,
) -> Tuple[Path, Dict[str, Any]]:
    """Build and publish an immutable, deterministic research statement bundle.

    Guarantees:
    - Zero network execution.
    - Output directory validation: cannot publish inside or equal to fixture dir, manifest dir, or live archive dirs.
    - Deterministic bundle_digest based on canonical JSON content.
    - Safe atomic publication via O_CREAT | O_EXCL and os.link.
    - Idempotent replay for identical content; collision rejection without clobbering.
    - Frozen versions preserved even in rejected/zero-block outcomes.
    """
    fpath = Path(fixture_path).resolve()
    mpath = Path(manifest_path).resolve()
    out_dir = Path(output_dir).resolve()
    fixture_dir = fpath.parent.resolve()
    manifest_dir = mpath.parent.resolve()

    if not fpath.is_file():
        raise FileNotFoundError(f"Fixture file not found: {fpath}")
    if not mpath.is_file():
        raise FileNotFoundError(f"Manifest file not found: {mpath}")

    # Check forbidden output destinations (both fixture and manifest directories and ancestors/children)
    if (
        out_dir == fixture_dir
        or out_dir.is_relative_to(fixture_dir)
        or out_dir == manifest_dir
        or out_dir.is_relative_to(manifest_dir)
    ):
        raise StatementPublishError(
            f"Forbidden output_dir '{out_dir}': cannot publish inside or equal to input directories ('{fixture_dir}', '{manifest_dir}')"
        )

    live_data_base = Path("/home/pythia/pythia-evidence-r2-data").resolve()
    if live_data_base.exists() and (out_dir == live_data_base or out_dir.is_relative_to(live_data_base)):
        raise StatementPublishError(
            f"Forbidden output_dir '{out_dir}': cannot publish inside live archive directory '{live_data_base}'"
        )

    out_dir.mkdir(parents=True, exist_ok=True)

    raw_bytes = fpath.read_bytes()
    manifest_data = json.loads(mpath.read_text(encoding="utf-8"))

    extraction = extract_fed_statement(raw_bytes, manifest_data)

    extraction_summary = {
        "status": "extracted" if extraction.is_success else "rejected",
        "total_paragraphs": len(extraction.document.paragraphs) if extraction.document else 0,
        "rejection_count": len(extraction.rejections),
    }

    acquisition_verification_status = manifest_data.get("acquisition_verification_status", "PARTIAL")

    res_prov = manifest_data.get("research_provenance")
    syn_flag = manifest_data.get("synthetic_fixture")
    is_synth_bundle = (
        res_prov == "synthetic_fixture"
        or (syn_flag is True and res_prov is None)
    )

    bundle_payload = {
        "bundle_version": STATEMENT_BUNDLE_VERSION,
        "extractor_version": STATEMENT_EXTRACTOR_VERSION,
        "schema_version": STATEMENT_SCHEMA_VERSION,
        "manifest": {
            **manifest_data,
            "acquisition_verification_status": (
                "NOT_APPLICABLE_SYNTHETIC"
                if is_synth_bundle
                else acquisition_verification_status
            ),
            "extraction_summary": extraction_summary,
        },
        "document": extraction.document.to_dict() if extraction.document else None,
        "rejections": [r.to_dict() for r in extraction.rejections],
        "research_metadata": {
            "provenance_class": (
                "synthetic_fixture"
                if is_synth_bundle
                else manifest_data.get("research_provenance", "research_fixture")
            ),
            "usable_for_forecast": False,
            "statement_profile": SUPPORTED_PROFILE,
            "acquisition_verification_status": (
                "NOT_APPLICABLE_SYNTHETIC"
                if is_synth_bundle
                else acquisition_verification_status
            ),
            "historical_acquisition_status": (
                "NOT_APPLICABLE_SYNTHETIC"
                if is_synth_bundle
                else "PARTIAL"
            ),
            "article_collection_status": (
                "synthetic_fixture_test_only"
                if is_synth_bundle
                else "research_sample_only_not_runtime_collected"
            ),
            "notes": (
                "Synthetic research fixture for offline test and parser verification. "
                "Not acquired from live web or runtime collector; no live acquisition claims."
                if is_synth_bundle
                else (
                    "Research extraction bundle for official FOMC statement text. "
                    "Acquired via bounded research fixture acquisition, not runtime R2 collector. "
                    "Historical acquisition status is PARTIAL; does not constitute verified production evidence or market outcome proof."
                )
            ),
        },
    }

    canonical_bytes = json.dumps(
        bundle_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    bundle_digest = hashlib.sha256(canonical_bytes).hexdigest()

    bundle_data = {
        "bundle_digest": bundle_digest,
        "statement_bundle": bundle_payload,
    }

    final_json_bytes = json.dumps(bundle_data, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    target_path = out_dir / f"statement_bundle_{bundle_digest}.json"

    # Safe atomic publication via O_CREAT | O_EXCL and os.link
    tmp_path = out_dir / f".tmp_publish_{os.getpid()}_{hashlib.sha256(final_json_bytes).hexdigest()[:8]}.json"
    created = False
    try:
        try:
            fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            raise StatementPublishError(
                f"Temporary publication target '{tmp_path.name}' already exists (symlink or collision). Operation aborted."
            )
        with os.fdopen(fd, "wb") as f_tmp:
            f_tmp.write(final_json_bytes)

        try:
            os.link(tmp_path, target_path)
        except FileExistsError:
            existing_bytes = target_path.read_bytes()
            if existing_bytes == final_json_bytes:
                # Idempotent replay
                pass
            else:
                raise StatementPublishError(
                    f"Atomic publication conflict: target '{target_path.name}' already exists "
                    f"with differing content! Competing bytes preserved without clobber."
                )
    finally:
        if created and (tmp_path.is_symlink() or tmp_path.exists()):
            try:
                tmp_path.unlink()
            except OSError:
                pass

    return target_path, bundle_data


def publish_statement_preview(
    preview_text: str,
    preview_path: Path | str,
    fixture_path: Path | str,
    manifest_path: Path | str,
) -> Path:
    """Safely publish markdown preview with collision and destination checks."""
    fpath = Path(fixture_path).resolve()
    mpath = Path(manifest_path).resolve()
    target = Path(preview_path).resolve()
    fixture_dir = fpath.parent.resolve()
    manifest_dir = mpath.parent.resolve()

    if target == fpath or target == mpath:
        raise StatementPublishError(
            f"Forbidden preview_path '{target}': resolves directly to input file"
        )
    if (
        target == fixture_dir
        or target.is_relative_to(fixture_dir)
        or target == manifest_dir
        or target.is_relative_to(manifest_dir)
    ):
        raise StatementPublishError(
            f"Forbidden preview_path '{target}': resolves inside input directory ('{fixture_dir}', '{manifest_dir}')"
        )
    live_data_base = Path("/home/pythia/pythia-evidence-r2-data").resolve()
    if live_data_base.exists() and (target == live_data_base or target.is_relative_to(live_data_base)):
        raise StatementPublishError(
            f"Forbidden preview_path '{target}': resolves inside live archive directory '{live_data_base}'"
        )

    preview_bytes = preview_text.encode("utf-8")
    if target.is_file():
        existing_bytes = target.read_bytes()
        if existing_bytes == preview_bytes:
            return target
        else:
            raise StatementPublishError(
                f"Preview conflict: target preview '{target}' already exists with different content"
            )

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_name(f".tmp_prev_{os.getpid()}_{hashlib.sha256(preview_bytes).hexdigest()[:8]}.md")
    created = False
    try:
        try:
            fd = os.open(str(temp_target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            raise StatementPublishError(
                f"Temporary preview target '{temp_target.name}' already exists (symlink or collision). Preview publication aborted."
            )
        with os.fdopen(fd, "wb") as f_tmp:
            f_tmp.write(preview_bytes)

        try:
            os.link(temp_target, target)
        except FileExistsError:
            if target.read_bytes() == preview_bytes:
                pass
            else:
                raise StatementPublishError(
                    f"Preview conflict: target preview '{target}' already exists with different content"
                )
    finally:
        if created and (temp_target.is_symlink() or temp_target.exists()):
            try:
                temp_target.unlink()
            except OSError:
                pass

    return target


def generate_statement_preview(bundle_data: Dict[str, Any]) -> str:
    """Generate human-readable preview from bundle data without model hallucination."""
    bundle = bundle_data.get("statement_bundle", {})
    doc = bundle.get("document")
    rejections = bundle.get("rejections", [])
    digest = bundle_data.get("bundle_digest", "")

    lines = []
    lines.append("# FOMC Statement Research Preview")
    lines.append("")
    lines.append(f"- **Bundle Digest:** `{digest}`")
    lines.append(f"- **Extractor Version:** `{bundle.get('extractor_version')}`")
    lines.append(f"- **Provenance Class:** `{bundle.get('research_metadata', {}).get('provenance_class')}`")
    acq_ver = bundle.get('research_metadata', {}).get('acquisition_verification_status')
    if acq_ver:
        lines.append(f"- **Acquisition Verification Status:** `{acq_ver}`")
    lines.append(f"- **Usable For Forecast:** `{bundle.get('research_metadata', {}).get('usable_for_forecast')}`")
    lines.append("")

    if not doc:
        lines.append("## Extraction Status: REJECTED")
        lines.append("")
        for rej in rejections:
            lines.append(f"- **Status:** `{rej.get('status')}`")
            lines.append(f"- **Reason:** {rej.get('reason')}")
        return "\n".join(lines)

    parent = doc.get("parent_feed_reference") or {}
    lines.append("## Document Header")
    lines.append(f"- **Statement ID:** `{doc.get('statement_id')}`")
    lines.append(f"- **Issuer:** {doc.get('issuer')}")
    lines.append(f"- **Title:** {doc.get('title')}")
    lines.append(f"- **Source URL:** {doc.get('source_url')}")
    lines.append(f"- **Parent Feed Fact ID:** `{parent.get('feed_fact_id', 'N/A')}`")
    lines.append(f"- **Raw Blob SHA-256:** `{doc.get('raw_blob_sha256')}`")
    lines.append(f"- **Page Date:** `{doc.get('published_date')}` (raw: `{doc.get('page_date_raw')}`, precision: `{doc.get('published_date_precision')}`)")
    lines.append(f"- **Published At:** `{doc.get('published_at')}` (provenance: `{doc.get('published_at_provenance')}`)")
    lines.append(f"- **Release Time Text:** `{doc.get('release_time_raw')}`")
    if doc.get("date_diagnostics"):
        diag = doc["date_diagnostics"]
        if diag.get("date_mismatch_detected"):
            lines.append(f"- **Date Diagnostic:** `WARNING: {diag.get('reason')}`")
    lines.append("")

    lines.append(f"## Statement Content ({len(doc.get('paragraphs', []))} paragraphs)")
    lines.append("")
    for p in doc.get("paragraphs", []):
        lines.append(f"### Paragraph {p.get('paragraph_index')} [`{p.get('role')}`]")
        lines.append(f"> {p.get('display_text')}")
        lines.append("")
        proof = p.get("proof", {})
        lines.append(f"- *Locator:* `{p.get('locator', {}).get('selector')}`")
        lines.append(f"- *Raw Byte Slice:* `[{proof.get('byte_start')}:{proof.get('byte_end')}]` ({proof.get('byte_end', 0) - proof.get('byte_start', 0)} bytes, SHA: `{proof.get('quote_sha256')[:12]}...`)")
        lines.append("")

    lines.append("## Verifications")
    lines.append(f"- Container Proof: bytes `[{doc.get('container_proof', {}).get('byte_start')}:{doc.get('container_proof', {}).get('byte_end')}]`")
    lines.append(f"- Title Proof: bytes `[{doc.get('title_proof', {}).get('byte_start')}:{doc.get('title_proof', {}).get('byte_end')}]`")
    lines.append(f"- Date Proof: bytes `[{doc.get('date_proof', {}).get('byte_start')}:{doc.get('date_proof', {}).get('byte_end')}]`")
    lines.append("- All proof spans independently verified against raw bytes: **VALID**")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pythia R3.1.2: Pure Offline FOMC Statement Extractor")
    parser.add_argument("--fixture-path", type=Path, required=True, help="Path to raw HTML fixture file")
    parser.add_argument("--manifest-path", type=Path, required=True, help="Path to fixture JSON manifest")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to store published statement bundle")
    parser.add_argument("--preview-path", type=Path, required=False, help="Optional path to output markdown preview")

    args = parser.parse_args()

    try:
        bundle_path, bundle_data = build_fed_statement_bundle(
            fixture_path=args.fixture_path,
            manifest_path=args.manifest_path,
            output_dir=args.output_dir,
        )
    except Exception as exc:
        print(f"Error publishing bundle: {exc}", file=sys.stderr)
        sys.exit(1)

    doc = bundle_data["statement_bundle"]["document"]
    rejections = bundle_data["statement_bundle"]["rejections"]

    print(f"Published Statement Bundle: {bundle_path}")
    print(f"Bundle Digest:             {bundle_data['bundle_digest']}")
    if doc:
        print(f"Statement ID:              {doc['statement_id']}")
        print(f"Title:                     {doc['title']}")
        print(f"Published Date:            {doc['published_date']}")
        print(f"Paragraphs:                {len(doc['paragraphs'])}")
    else:
        print(f"Status:                    REJECTED ({len(rejections)} rejections)")
        for r in rejections:
            print(f"  - {r['status']}: {r['reason']}")

    if args.preview_path:
        try:
            preview = generate_statement_preview(bundle_data)
            published_prev = publish_statement_preview(
                preview_text=preview,
                preview_path=args.preview_path,
                fixture_path=args.fixture_path,
                manifest_path=args.manifest_path,
            )
            print(f"Preview written to:        {published_prev}")
        except Exception as exc:
            print(f"Error publishing preview: {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
