"""Pythia R3.1: Pure Offline Parser and Extractor for Official FOMC Statements.

Extracts structured FOMC statement content from raw HTML fixtures with exact ProofSpan linkages:
- Document profile: fomc_press_release_v1
- Heading extraction: document title, proven page date, release time
- Statement body: ordered paragraphs with clean parsed text and exact raw byte ProofSpans
- Deterministic canonical bundle publication with atomic no-clobber guarantees
- Strictly zero network: pure offline processing
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import html
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse

from bs4 import BeautifulSoup

from python.evaluation.evidence_extract import ProofSpan

STATEMENT_EXTRACTOR_VERSION = "pythia-statement-extractor-r3/0.1"
STATEMENT_SCHEMA_VERSION = "pythia-statement-schema-r3/1.0"
STATEMENT_BUNDLE_VERSION = "pythia-statement-bundle-r3/0.1"
SUPPORTED_PROFILE = "fomc_press_release_v1"

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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statement_id": self.statement_id,
            "profile_id": self.profile_id,
            "source_url": self.source_url,
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
    """Clean and normalize HTML fragment into human-readable display text."""
    soup = BeautifulSoup(raw_html_fragment, "html.parser")
    # Remove script and style tags if any
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text()
    text = html.unescape(text)
    # Normalize internal whitespace
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
    # Try standard Fed formats, e.g. "September 16, 2026" or "July 29, 2026"
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
) -> Tuple[Optional[str], Optional[str]]:
    """Parse release time string if it contains explicit time and timezone."""
    if not release_str or not published_date:
        return None, None

    # Pattern: "For release at 2:00 p.m. EDT" or "2:00 p.m. EST"
    pattern = re.compile(
        r"(\d{1,2}):(\d{2})\s*(a\.m\.|p\.m\.|am|pm)\s*([A-Z]{3,4})",
        re.IGNORECASE,
    )
    match = pattern.search(release_str)
    if not match:
        return None, None

    hour = int(match.group(1))
    minute = int(match.group(2))
    ampm = match.group(3).lower().replace(".", "")
    tz_abbr = match.group(4).upper()

    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0

    tz_offset = US_TIMEZONE_OFFSETS.get(tz_abbr)
    if not tz_offset:
        # Timezone abbreviation not recognized; cannot prove timezone offset
        return None, None

    iso_published_at = f"{published_date}T{hour:02d}:{minute:02d}:00{tz_offset}"
    return iso_published_at, "header_release_time"


def _find_matching_closing_div(blob: bytes, open_pos: int) -> int:
    """Find the exact closing </div> matching opening <div at open_pos in blob."""
    # Find end of opening <div ...> tag
    tag_end = blob.find(b">", open_pos)
    if tag_end == -1:
        return -1

    depth = 1
    pos = tag_end + 1
    div_pattern = re.compile(rb"</?div(?:\s[^>]*)?>", re.IGNORECASE)

    while depth > 0:
        match = div_pattern.search(blob, pos)
        if not match:
            return -1
        tag = match.group(0).lower()
        if tag.startswith(b"</div"):
            depth -= 1
            pos = match.end()
        else:
            depth += 1
            pos = match.end()

    return pos


def extract_fed_statement(
    raw_bytes: bytes,
    manifest: Dict[str, Any],
) -> ExtractionResult:
    """Pure offline extraction of official FOMC statement from raw HTML and manifest.
    
    Enforces exact ProofSpan byte offsets, independent proof verification, strict
    document profile matching, and explicit provenance.
    """
    rejections: List[RejectionRecord] = []
    source_url = manifest.get("source_url", "")
    manifest_sha = manifest.get("blob_sha256", "")
    actual_sha = hashlib.sha256(raw_bytes).hexdigest()

    # 1. Verify blob integrity against manifest
    if manifest_sha and actual_sha != manifest_sha:
        rejections.append(
            RejectionRecord(
                status="digest_mismatch",
                reason=f"Raw blob SHA-256 ({actual_sha}) does not match manifest blob_sha256 ({manifest_sha})",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    expected_bytes = manifest.get("bytes_stored")
    if expected_bytes is not None and len(raw_bytes) != expected_bytes:
        rejections.append(
            RejectionRecord(
                status="size_mismatch",
                reason=f"Raw blob size ({len(raw_bytes)}) does not match manifest bytes_stored ({expected_bytes})",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # 2. Check UTF-8 decodability (accounting for optional UTF-8 BOM)
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        rejections.append(
            RejectionRecord(
                status="encoding_error",
                reason=f"Raw blob is not valid UTF-8: {exc}",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # 3. Detect bot challenges / JS-only walls
    lower_raw = raw_text.lower()
    if (
        "challenge-running" in lower_raw
        or "cf-browser-verification" in lower_raw
        or "<title>just a moment...</title>" in lower_raw
        or ("enable javascript" in lower_raw and "article" not in lower_raw)
    ):
        rejections.append(
            RejectionRecord(
                status="blocked_or_challenge",
                reason="Raw HTML represents a challenge or JavaScript-only barrier page",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # 4. Check DOM structural containers using BeautifulSoup
    soup = BeautifulSoup(raw_text, "html.parser")
    article_div = soup.find("div", id="article")
    if not article_div:
        rejections.append(
            RejectionRecord(
                status="unsupported_structure",
                reason="Missing required #article container in HTML",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    heading_div = article_div.find("div", class_=lambda c: c and "heading" in c.split())
    if not heading_div:
        rejections.append(
            RejectionRecord(
                status="unsupported_structure",
                reason="Missing required .heading container within #article",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    title_elem = heading_div.find(["h1", "h2", "h3"], class_="title")
    if not title_elem:
        rejections.append(
            RejectionRecord(
                status="unsupported_structure",
                reason="Missing required title element (.title) within heading",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    title_text = title_elem.get_text(strip=True)
    lower_title = title_text.lower()

    # Reject non-statement document types (minutes, projections, speeches, discount rate)
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
            rejections.append(
                RejectionRecord(
                    status="unsupported_document_type",
                    reason=f"Document title '{title_text}' indicates unsupported document type ({marker!r})",
                    source_url=source_url,
                    raw_blob_sha256=actual_sha,
                )
            )
            return ExtractionResult(
                document=None,
                rejections=rejections,
                extractor_version=STATEMENT_EXTRACTOR_VERSION,
                schema_version=STATEMENT_SCHEMA_VERSION,
            )

    if "fomc statement" not in lower_title and "monetary policy statement" not in lower_title:
        rejections.append(
            RejectionRecord(
                status="unsupported_document_type",
                reason=f"Document title '{title_text}' does not match supported FOMC statement profile",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # 5. Extract date and release time
    date_elem = heading_div.find("p", class_="article__time")
    if not date_elem:
        rejections.append(
            RejectionRecord(
                status="unsupported_structure",
                reason="Missing required date element (p.article__time) in heading",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    page_date_raw = date_elem.get_text(strip=True)
    published_date, published_date_precision = _parse_page_date(page_date_raw)
    if not published_date:
        rejections.append(
            RejectionRecord(
                status="invalid_date",
                reason=f"Could not parse calendar date from page text '{page_date_raw}'",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    release_time_elem = heading_div.find("p", class_="releaseTime")
    release_time_raw = None
    if release_time_elem:
        # Get immediate text node before any share lists
        release_text = ""
        for item in release_time_elem.children:
            if isinstance(item, str):
                release_text += item
        release_time_raw = re.sub(r"\s+", " ", release_text).strip() or None

    published_at, published_at_provenance = _parse_release_time(release_time_raw or "", published_date)

    # 6. Locate Statement Body Container in DOM and in raw bytes
    # Find sibling div with class col-xs-12 col-sm-8 col-md-8 without heading
    body_div = None
    for child in article_div.find_all("div", recursive=False):
        classes = child.get("class", [])
        if "col-xs-12" in classes and "col-sm-8" in classes and "col-md-8" in classes and "heading" not in classes:
            body_div = child
            break

    if not body_div:
        rejections.append(
            RejectionRecord(
                status="unsupported_structure",
                reason="Missing statement body container div.col-xs-12.col-sm-8.col-md-8 in #article",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # 7. Exact byte locating in raw_bytes
    # Find pos of #article in raw bytes
    article_attr_match = re.search(rb'<div\s+[^>]*id=["\']article["\'][^>]*>', raw_bytes, re.IGNORECASE)
    if not article_attr_match:
        rejections.append(
            RejectionRecord(
                status="unsupported_structure",
                reason="Could not locate id='article' tag in raw bytes",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )
    article_byte_pos = article_attr_match.start()

    # Locate date_proof
    date_regex = re.compile(
        rb'<p\s+class=["\']article__time["\']>.*?</p>',
        re.DOTALL | re.IGNORECASE,
    )
    date_match = date_regex.search(raw_bytes, article_byte_pos)
    if not date_match:
        raise StatementIntegrityError("Failed to locate exact date tag in raw bytes")
    date_proof = ProofSpan(
        byte_start=date_match.start(),
        byte_end=date_match.end(),
        quote_sha256=hashlib.sha256(date_match.group(0)).hexdigest(),
        raw_blob_sha256=actual_sha,
        raw_quote_text=date_match.group(0).decode("utf-8"),
    )
    date_locator = {"type": "html_css", "selector": "#article div.heading p.article__time"}

    # Locate title_proof
    title_regex = re.compile(
        rb'<h3\s+class=["\']title["\']>.*?</h3>',
        re.DOTALL | re.IGNORECASE,
    )
    title_match = title_regex.search(raw_bytes, article_byte_pos)
    if not title_match:
        raise StatementIntegrityError("Failed to locate exact title tag in raw bytes")
    title_proof = ProofSpan(
        byte_start=title_match.start(),
        byte_end=title_match.end(),
        quote_sha256=hashlib.sha256(title_match.group(0)).hexdigest(),
        raw_blob_sha256=actual_sha,
        raw_quote_text=title_match.group(0).decode("utf-8"),
    )
    title_locator = {"type": "html_css", "selector": "#article div.heading h3.title"}

    # Locate release_time_proof if present
    release_time_proof = None
    release_time_locator = None
    if release_time_elem:
        rel_regex = re.compile(
            rb'<p\s+class=["\']releaseTime["\']>.*?</p>',
            re.DOTALL | re.IGNORECASE,
        )
        rel_match = rel_regex.search(raw_bytes, article_byte_pos)
        if rel_match:
            release_time_proof = ProofSpan(
                byte_start=rel_match.start(),
                byte_end=rel_match.end(),
                quote_sha256=hashlib.sha256(rel_match.group(0)).hexdigest(),
                raw_blob_sha256=actual_sha,
                raw_quote_text=rel_match.group(0).decode("utf-8"),
            )
            release_time_locator = {"type": "html_css", "selector": "#article div.heading p.releaseTime"}

    # Locate statement container in raw bytes
    # Find opening tag of non-heading col-xs-12 col-sm-8 col-md-8 following date/title
    container_regex = re.compile(
        rb'<div\s+(?![^>]*\bheading\b)[^>]*class=["\'][^"\']*\bcol-xs-12\b[^"\']*\bcol-sm-8\b[^"\']*\bcol-md-8\b[^"\']*["\'][^>]*>',
        re.IGNORECASE,
    )
    cont_match = container_regex.search(raw_bytes, title_match.end())
    if not cont_match:
        rejections.append(
            RejectionRecord(
                status="unsupported_structure",
                reason="Could not locate statement container in raw HTML bytes",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    cont_start = cont_match.start()
    cont_end = _find_matching_closing_div(raw_bytes, cont_start)
    if cont_end == -1:
        rejections.append(
            RejectionRecord(
                status="unsupported_structure",
                reason="Unbalanced closing </div> for statement container",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
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
    container_locator = {"type": "html_css", "selector": "#article div.col-xs-12.col-sm-8.col-md-8"}

    # 8. Extract paragraph items within container slice
    p_regex = re.compile(rb'<p(?:\s[^>]*)?>.*?</p>', re.DOTALL | re.IGNORECASE)
    paragraphs: List[StatementParagraph] = []
    substantive_paras: List[str] = []

    for idx, p_m in enumerate(p_regex.finditer(container_slice)):
        abs_p_start = cont_start + p_m.start()
        abs_p_end = cont_start + p_m.end()
        p_raw = raw_bytes[abs_p_start:abs_p_end]
        display_text = _clean_display_text(p_raw)

        # Skip empty whitespace paragraphs (e.g. <p>&nbsp;</p>)
        if not display_text:
            continue

        p_proof = ProofSpan(
            byte_start=abs_p_start,
            byte_end=abs_p_end,
            quote_sha256=hashlib.sha256(p_raw).hexdigest(),
            raw_blob_sha256=actual_sha,
            raw_quote_text=p_raw.decode("utf-8"),
        )
        p_locator = {
            "type": "html_css",
            "selector": f"#article div.col-xs-12.col-sm-8.col-md-8 p:nth-of-type({idx + 1})",
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

    if len(paragraphs) == 0:
        rejections.append(
            RejectionRecord(
                status="empty_content",
                reason="Statement container contains zero non-empty paragraphs",
                source_url=source_url,
                raw_blob_sha256=actual_sha,
            )
        )
        return ExtractionResult(
            document=None,
            rejections=rejections,
            extractor_version=STATEMENT_EXTRACTOR_VERSION,
            schema_version=STATEMENT_SCHEMA_VERSION,
        )

    # 9. Verify ALL ProofSpans independently before assembling document
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

    # 10. Compute deterministic Statement ID
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
        parent_feed_reference=manifest.get("parent_feed_reference"),
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
    - Deterministic bundle_digest based on canonical JSON content.
    - Atomic publication via os.link (FileExistsError handled safely).
    - Idempotent replay for identical content; collision rejection without clobbering.
    - Frozen versions preserved even in rejected/zero-block outcomes.
    """
    fpath = Path(fixture_path).resolve()
    mpath = Path(manifest_path).resolve()
    out_dir = Path(output_dir).resolve()

    if not fpath.is_file():
        raise FileNotFoundError(f"Fixture file not found: {fpath}")
    if not mpath.is_file():
        raise FileNotFoundError(f"Manifest file not found: {mpath}")

    out_dir.mkdir(parents=True, exist_ok=True)

    raw_bytes = fpath.read_bytes()
    manifest_data = json.loads(mpath.read_text(encoding="utf-8"))

    extraction = extract_fed_statement(raw_bytes, manifest_data)

    extraction_summary = {
        "status": "extracted" if extraction.is_success else "rejected",
        "total_paragraphs": len(extraction.document.paragraphs) if extraction.document else 0,
        "rejection_count": len(extraction.rejections),
    }

    bundle_payload = {
        "bundle_version": STATEMENT_BUNDLE_VERSION,
        "extractor_version": STATEMENT_EXTRACTOR_VERSION,
        "schema_version": STATEMENT_SCHEMA_VERSION,
        "manifest": {
            **manifest_data,
            "extraction_summary": extraction_summary,
        },
        "document": extraction.document.to_dict() if extraction.document else None,
        "rejections": [r.to_dict() for r in extraction.rejections],
        "research_metadata": {
            "provenance_class": "research_fixture",
            "usable_for_forecast": False,
            "statement_profile": SUPPORTED_PROFILE,
            "article_collection_status": "research_sample_only_not_runtime_collected",
            "notes": (
                "Research extraction bundle for official FOMC statement text. "
                "Acquired via bounded research fixture acquisition, not runtime R2 collector. "
                "Does not constitute verified production evidence or market outcome proof."
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

    # Atomic publication via temporary file and os.link
    tmp_path = out_dir / f".tmp_publish_{os.getpid()}_{hashlib.sha256(final_json_bytes).hexdigest()[:8]}.json"
    try:
        tmp_path.write_bytes(final_json_bytes)
        try:
            os.link(tmp_path, target_path)
        except FileExistsError:
            # Check if competing file has identical content
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
        if tmp_path.exists():
            tmp_path.unlink()

    return target_path, bundle_data


def generate_statement_preview(bundle_data: Dict[str, Any]) -> str:
    """Generate human-readable preview from bundle data without model hallucination."""
    bundle = bundle_data.get("statement_bundle", {})
    doc = bundle.get("document")
    manifest = bundle.get("manifest", {})
    rejections = bundle.get("rejections", [])
    digest = bundle_data.get("bundle_digest", "")

    lines = []
    lines.append(f"# FOMC Statement Research Preview")
    lines.append(f"")
    lines.append(f"- **Bundle Digest:** `{digest}`")
    lines.append(f"- **Extractor Version:** `{bundle.get('extractor_version')}`")
    lines.append(f"- **Provenance Class:** `{bundle.get('research_metadata', {}).get('provenance_class')}`")
    lines.append(f"- **Usable For Forecast:** `{bundle.get('research_metadata', {}).get('usable_for_forecast')}`")
    lines.append(f"")

    if not doc:
        lines.append(f"## Extraction Status: REJECTED")
        lines.append(f"")
        for rej in rejections:
            lines.append(f"- **Status:** `{rej.get('status')}`")
            lines.append(f"- **Reason:** {rej.get('reason')}")
        return "\n".join(lines)

    parent = doc.get("parent_feed_reference") or {}
    lines.append(f"## Document Header")
    lines.append(f"- **Statement ID:** `{doc.get('statement_id')}`")
    lines.append(f"- **Title:** {doc.get('title')}")
    lines.append(f"- **Source URL:** {doc.get('source_url')}")
    lines.append(f"- **Parent Feed Fact ID:** `{parent.get('feed_fact_id', 'N/A')}`")
    lines.append(f"- **Raw Blob SHA-256:** `{doc.get('raw_blob_sha256')}`")
    lines.append(f"- **Page Date:** `{doc.get('published_date')}` (raw: `{doc.get('page_date_raw')}`, precision: `{doc.get('published_date_precision')}`)")
    lines.append(f"- **Published At:** `{doc.get('published_at')}` (provenance: `{doc.get('published_at_provenance')}`)")
    lines.append(f"- **Release Time Text:** `{doc.get('release_time_raw')}`")
    lines.append(f"")

    lines.append(f"## Statement Content ({len(doc.get('paragraphs', []))} paragraphs)")
    lines.append(f"")
    for p in doc.get("paragraphs", []):
        lines.append(f"### Paragraph {p.get('paragraph_index')} [`{p.get('role')}`]")
        lines.append(f"> {p.get('display_text')}")
        lines.append(f"")
        proof = p.get("proof", {})
        lines.append(f"- *Locator:* `{p.get('locator', {}).get('selector')}`")
        lines.append(f"- *Raw Byte Slice:* `[{proof.get('byte_start')}:{proof.get('byte_end')}]` ({proof.get('byte_end', 0) - proof.get('byte_start', 0)} bytes, SHA: `{proof.get('quote_sha256')[:12]}...`)")
        lines.append(f"")

    lines.append(f"## Verifications")
    lines.append(f"- Container Proof: bytes `[{doc.get('container_proof', {}).get('byte_start')}:{doc.get('container_proof', {}).get('byte_end')}]`")
    lines.append(f"- Title Proof: bytes `[{doc.get('title_proof', {}).get('byte_start')}:{doc.get('title_proof', {}).get('byte_end')}]`")
    lines.append(f"- Date Proof: bytes `[{doc.get('date_proof', {}).get('byte_start')}:{doc.get('date_proof', {}).get('byte_end')}]`")
    lines.append(f"- All proof spans independently verified against raw bytes: **VALID**")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pythia R3.1: Pure Offline FOMC Statement Extractor")
    parser.add_argument("--fixture-path", type=Path, required=True, help="Path to raw HTML fixture file")
    parser.add_argument("--manifest-path", type=Path, required=True, help="Path to fixture JSON manifest")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to store published statement bundle")
    parser.add_argument("--preview-path", type=Path, required=False, help="Optional path to output markdown preview")

    args = parser.parse_args()

    bundle_path, bundle_data = build_fed_statement_bundle(
        fixture_path=args.fixture_path,
        manifest_path=args.manifest_path,
        output_dir=args.output_dir,
    )

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
        preview = generate_statement_preview(bundle_data)
        args.preview_path.write_text(preview, encoding="utf-8")
        print(f"Preview written to:        {args.preview_path}")


if __name__ == "__main__":
    main()
