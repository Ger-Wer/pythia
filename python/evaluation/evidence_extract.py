"""Pythia R3.0: Deterministic Fact Extraction from Evidence Captures.

Extracts structured, provenance-backed facts and honest quotes from verified
canonical evidence captures:
- nyfed_effr: EFFR observations with exact decimal rates (no float rounding)
  and day-precision effective dates.
- fed_monetary_rss, fed_speeches_rss: RSS 2.0 items with feed excerpts, article links,
  and validated timezone dates.

Enforces:
1. Exact raw proof spans and structural locators (JSONPointer / XPath).
2. Verbatim quote text matching exact blob bytes (including UTF-8 BOM).
3. Exact decimals parsed directly without intermediate float representation.
4. Independent transport and content identity validation (not just stored status label).
5. Context-only role: usable_for_forecast is False; no probabilities or edge claims.
6. Honest rejection records for unsupported sources, negative attempts, or malformed payloads.
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from python.evaluation.evidence_reader import VerifiedCapture
from python.evaluation.source_probe import CANONICAL_SOURCES, SourceProbe

EXTRACTOR_VERSION = "pythia-extractor-r3/0.3"
FACT_SCHEMA_VERSION = "pythia-fact-r3/1.2"
SUPPORTED_SOURCES = ("nyfed_effr", "fed_monetary_rss", "fed_speeches_rss")


class ExtractionError(Exception):
    """Raised when an unrecoverable extraction error occurs."""


@dataclass(frozen=True)
class ProofSpan:
    """Exact verbatim byte slice from a raw CAS blob."""

    byte_start: int
    byte_end: int
    quote_sha256: str
    raw_blob_sha256: str
    raw_quote_text: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def verify_against_blob(self, blob: bytes) -> bool:
        """Independently verify that the proof slice matches the CAS blob."""
        if not (0 <= self.byte_start <= self.byte_end <= len(blob)):
            return False
        slice_bytes = blob[self.byte_start : self.byte_end]
        if hashlib.sha256(slice_bytes).hexdigest() != self.quote_sha256:
            return False
        if hashlib.sha256(blob).hexdigest() != self.raw_blob_sha256:
            return False
        if self.raw_quote_text is not None:
            try:
                decoded = slice_bytes.decode("utf-8")
                if decoded != self.raw_quote_text:
                    return False
            except UnicodeDecodeError:
                return False
        return True


@dataclass(frozen=True)
class Fact:
    """A single deterministic factual claim extracted from an evidence capture."""

    fact_id: str
    source_id: str
    issuer: str
    extractor_version: str
    schema_version: str
    fact_type: str
    value: Dict[str, Any]
    unit: Optional[str]
    effective_date: Optional[str]
    effective_date_precision: Optional[str]
    published_at: Optional[str]
    published_at_reason: Optional[str]
    role: str
    claim_not_direct_evidence: bool
    receipt_ids: List[str]
    receipt_seqs: List[int]
    revision_ids: List[str]
    blob_sha256: str
    locator: Dict[str, Any]
    proof: ProofSpan

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["proof"] = self.proof.to_dict()
        return d


@dataclass(frozen=True)
class RejectionRecord:
    """Explains why a capture yielded no extracted facts."""

    receipt_id: str
    seq: int
    source_id: str
    status: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExtractionResult:
    """Aggregate result of extracting facts across a collection of captures."""

    facts: List[Fact]
    rejections: List[RejectionRecord]


def _check_duplicate_keys_hook(pairs: List[Tuple[Any, Any]]) -> Dict[str, Any]:
    """Reject JSON objects containing duplicate keys."""
    res: Dict[str, Any] = {}
    for k, v in pairs:
        if k in res:
            raise ValueError(f"Duplicate key in JSON: {k!r}")
        res[k] = v
    return res


def _reject_json_constants(val: str) -> None:
    """Reject NaN, Infinity, -Infinity in JSON."""
    raise ValueError(f"Forbidden constant in JSON: {val!r}")


def _compute_fact_id(
    fact_type: str,
    source_id: str,
    locator_path: str,
    blob_sha256: str,
    value: Dict[str, Any],
) -> str:
    """Deterministic 16-character fact ID based on content hash."""
    payload = {
        "fact_type": fact_type,
        "source_id": source_id,
        "locator_path": locator_path,
        "blob_sha256": blob_sha256,
        "value": value,
    }
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]
    return f"fact-{digest}"


def extract_nyfed_effr(capture: VerifiedCapture) -> List[Fact]:
    """Extract EFFR rate observations from a verified NYFed capture.
    
    Strict requirements:
    - Parse float tokens directly as Decimal (no intermediate Python float rounding).
    - Reject duplicate keys, NaN, Infinity, bools where numbers expected.
    - Validate calendar effectiveDate (precision='day').
    - Exact string decimal for percentRate; unit='percent'.
    - published_at=None (no publication timestamp field in NYFed rates API).
    - locator=JSONPointer.
    """
    assert capture.blob_bytes is not None
    blob_bytes = capture.blob_bytes
    blob_sha = capture.receipt.blob_sha256 or hashlib.sha256(blob_bytes).hexdigest()

    try:
        blob_text = blob_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"NYFed blob is not valid UTF-8: {exc}") from exc

    # Parse JSON with Decimal for float tokens to preserve exact precision without rounding
    data = json.loads(
        blob_text,
        parse_float=Decimal,
        parse_int=int,
        object_pairs_hook=_check_duplicate_keys_hook,
        parse_constant=_reject_json_constants,
    )

    if not isinstance(data, dict):
        raise ValueError("Root JSON is not an object")

    ref_rates = data.get("refRates")
    if not isinstance(ref_rates, list):
        raise ValueError("Missing or invalid 'refRates' array in NYFed payload")

    facts: List[Fact] = []
    proof = ProofSpan(
        byte_start=0,
        byte_end=len(blob_bytes),
        quote_sha256=blob_sha,
        raw_blob_sha256=blob_sha,
        raw_quote_text=blob_text,
    )

    for idx, item in enumerate(ref_rates):
        if not isinstance(item, dict):
            continue
        rate_type = item.get("type")
        if rate_type != "EFFR":
            # Only extract EFFR series as specified in R3.0
            continue

        eff_date_raw = item.get("effectiveDate")
        if not isinstance(eff_date_raw, str):
            raise ValueError(f"Missing effectiveDate at index {idx}")

        # Calendar date validation
        try:
            parts = eff_date_raw.split("-")
            if len(parts) != 3:
                raise ValueError("Date must have YYYY-MM-DD format")
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            parsed_date = datetime(year, month, day).date()
            eff_date_str = parsed_date.isoformat()
        except Exception as exc:
            raise ValueError(f"Invalid calendar date '{eff_date_raw}' at index {idx}: {exc}") from exc

        rate_val = item.get("percentRate")
        # Strict numeric type validation: reject bool, require int, float, or Decimal
        if isinstance(rate_val, bool) or not isinstance(rate_val, (int, float, Decimal)):
            raise ValueError(f"Invalid non-numeric percentRate at index {idx}: {rate_val!r}")
        if isinstance(rate_val, Decimal):
            if not rate_val.is_finite():
                raise ValueError(f"Forbidden non-finite percentRate at index {idx}: {rate_val!r}")
            rate_decimal_str = str(rate_val)
        else:
            if math.isnan(rate_val) or math.isinf(rate_val):
                raise ValueError(f"Forbidden non-finite percentRate at index {idx}: {rate_val!r}")
            rate_decimal_str = str(rate_val)

        value = {
            "series": "EFFR",
            "rate": rate_decimal_str,
            "effective_date": eff_date_str,
        }

        locator = {"type": "json_pointer", "path": f"/refRates/{idx}"}
        fact_id = _compute_fact_id(
            fact_type="rate_observation",
            source_id="nyfed_effr",
            locator_path=locator["path"],
            blob_sha256=blob_sha,
            value=value,
        )

        fact = Fact(
            fact_id=fact_id,
            source_id="nyfed_effr",
            issuer="Federal Reserve Bank of New York",
            extractor_version=EXTRACTOR_VERSION,
            schema_version=FACT_SCHEMA_VERSION,
            fact_type="rate_observation",
            value=value,
            unit="percent",
            effective_date=eff_date_str,
            effective_date_precision="day",
            published_at=None,
            published_at_reason="No publication timestamp field in NYFed rates API schema",
            role="background/context",
            claim_not_direct_evidence=True,
            receipt_ids=[capture.receipt.receipt_id],
            receipt_seqs=[capture.receipt.seq],
            revision_ids=[capture.revision.revision_id] if capture.revision else [],
            blob_sha256=blob_sha,
            locator=locator,
            proof=proof,
        )

        # Independent proof verification prior to emitting
        if not fact.proof.verify_against_blob(blob_bytes):
            raise ExtractionError(f"Generated proof span failed verification against blob for {fact_id}")

        facts.append(fact)

    return facts


def extract_fed_rss(capture: VerifiedCapture) -> List[Fact]:
    """Extract feed item facts from verified Fed Monetary or Fed Speeches RSS captures.
    
    Strict requirements:
    - Real RSS 2.0 structure (rss version='2.0' -> channel -> item).
    - Preserves exact raw quote text including UTF-8 BOM if present in raw blob.
    - Description/CDATA stored as feed_excerpt; linked article marked referenced_not_fetched.
    - pubDate normalized to UTC ISO-8601 only if timezone is explicitly proven.
    - Never substitute receipt time or HTTP Last-Modified.
    """
    assert capture.blob_bytes is not None
    blob_bytes = capture.blob_bytes
    blob_sha = capture.receipt.blob_sha256 or hashlib.sha256(blob_bytes).hexdigest()

    # Strict UTF-8 decode preserving BOM character if present
    try:
        blob_text = blob_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"RSS blob is not valid UTF-8: {exc}") from exc

    # Parse XML (strip leading BOM character only for XML parser, raw_quote_text retains exact blob_text)
    xml_text = blob_text[1:] if blob_text.startswith("\ufeff") else blob_text
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"Malformed XML in RSS feed: {exc}") from exc

    if root.tag != "rss":
        raise ValueError(f"Root XML tag is '{root.tag}', expected 'rss'")

    rss_version = root.attrib.get("version")
    if rss_version != "2.0":
        raise ValueError(f"Unsupported RSS version '{rss_version}', expected '2.0'")

    channel = root.find("channel")
    if channel is None:
        raise ValueError("Missing 'channel' element in RSS feed")

    facts: List[Fact] = []
    proof = ProofSpan(
        byte_start=0,
        byte_end=len(blob_bytes),
        quote_sha256=blob_sha,
        raw_blob_sha256=blob_sha,
        raw_quote_text=blob_text,
    )

    items = channel.findall("item")
    for idx, item in enumerate(items):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip() or None
        description = item.findtext("description")
        if description is not None:
            description = description.strip()

        pub_date_raw = item.findtext("pubDate")
        published_at: Optional[str] = None
        published_at_reason: Optional[str] = None

        if pub_date_raw:
            pub_date_raw = pub_date_raw.strip()
            try:
                parsed_dt = email.utils.parsedate_to_datetime(pub_date_raw)
                if parsed_dt.tzinfo is not None:
                    published_at = parsed_dt.astimezone(timezone.utc).isoformat()
                else:
                    published_at = None
                    published_at_reason = "pubDate lacks timezone information"
            except Exception as exc:
                published_at = None
                published_at_reason = f"pubDate failed RFC 2822 parsing: {exc}"
        else:
            published_at = None
            published_at_reason = "Missing pubDate in RSS item"

        value: Dict[str, Any] = {
            "title": title,
            "referenced_article_url": link,
            "article_status": "referenced_not_fetched",
            "feed_excerpt": description,
            "guid": guid,
            "raw_pub_date": pub_date_raw,
        }

        locator = {"type": "xml_xpath", "path": f"/rss/channel/item[{idx + 1}]"}
        fact_id = _compute_fact_id(
            fact_type="feed_item",
            source_id=capture.receipt.source_id,
            locator_path=locator["path"],
            blob_sha256=blob_sha,
            value=value,
        )

        fact = Fact(
            fact_id=fact_id,
            source_id=capture.receipt.source_id,
            issuer="Federal Reserve Board",
            extractor_version=EXTRACTOR_VERSION,
            schema_version=FACT_SCHEMA_VERSION,
            fact_type="feed_item",
            value=value,
            unit=None,
            effective_date=None,
            effective_date_precision=None,
            published_at=published_at,
            published_at_reason=published_at_reason,
            role="background/context",
            claim_not_direct_evidence=True,
            receipt_ids=[capture.receipt.receipt_id],
            receipt_seqs=[capture.receipt.seq],
            revision_ids=[capture.revision.revision_id] if capture.revision else [],
            blob_sha256=blob_sha,
            locator=locator,
            proof=proof,
        )

        # Independent proof verification prior to emitting
        if not fact.proof.verify_against_blob(blob_bytes):
            raise ExtractionError(f"Generated proof span failed verification against blob for {fact_id}")

        facts.append(fact)

    return facts


def extract_facts_from_captures(captures: List[VerifiedCapture]) -> ExtractionResult:
    """Extract facts and honest rejection records from verified captures.
    
    Enforces independent transport and content validation rather than blindly trusting
    the stored status label.
    """
    facts: List[Fact] = []
    rejections: List[RejectionRecord] = []

    for cap in captures:
        rec = cap.receipt

        # 1. Check negative capture
        if cap.is_negative:
            rejections.append(
                RejectionRecord(
                    receipt_id=rec.receipt_id,
                    seq=rec.seq,
                    source_id=rec.source_id,
                    status=rec.status,
                    reason=f"Capture was negative/failed (status={rec.status}, error={rec.error_msg or rec.reason})",
                )
            )
            continue

        # 2. Check stored diagnostic status (MUST be 'valid' - cannot promote non-valid frozen receipts)
        if rec.status != "valid":
            rejections.append(
                RejectionRecord(
                    receipt_id=rec.receipt_id,
                    seq=rec.seq,
                    source_id=rec.source_id,
                    status=rec.status,
                    reason=f"Stored diagnostic status is '{rec.status}' (expected 'valid')",
                )
            )
            continue

        # 3. Check clean transport gate (HTTP status MUST be 200)
        if rec.http_status != 200:
            rejections.append(
                RejectionRecord(
                    receipt_id=rec.receipt_id,
                    seq=rec.seq,
                    source_id=rec.source_id,
                    status=rec.status if rec.status != "valid" else "transport_error",
                    reason=f"HTTP status is {rec.http_status} (expected 200 OK)",
                )
            )
            continue

        # 3. Check supported source
        if rec.source_id not in SUPPORTED_SOURCES:
            rejections.append(
                RejectionRecord(
                    receipt_id=rec.receipt_id,
                    seq=rec.seq,
                    source_id=rec.source_id,
                    status=rec.status,
                    reason=f"Source '{rec.source_id}' is not supported for extraction in R3.0 (supported: {SUPPORTED_SOURCES})",
                )
            )
            continue

        # 4. Check completeness and blob presence
        if not rec.complete or cap.blob_bytes is None or rec.limit_exceeded or rec.error_msg:
            rejections.append(
                RejectionRecord(
                    receipt_id=rec.receipt_id,
                    seq=rec.seq,
                    source_id=rec.source_id,
                    status=rec.status,
                    reason=f"Capture is not valid/complete (complete={rec.complete}, error={rec.error_msg})",
                )
            )
            continue

        # 5. Check canonical origin URL identity
        expected_canonical = CANONICAL_SOURCES.get(rec.source_id)
        if rec.initial_url != expected_canonical:
            rejections.append(
                RejectionRecord(
                    receipt_id=rec.receipt_id,
                    seq=rec.seq,
                    source_id=rec.source_id,
                    status="invalid_content",
                    reason=f"Source URL '{rec.initial_url}' does not match canonical URL '{expected_canonical}'",
                )
            )
            continue

        # 6. Independent content identity validation via reference validator (do not trust stored label)
        with SourceProbe() as probe:
            content_status, _, content_reason, _ = probe.validate_content(
                source_id=rec.source_id,
                http_status=rec.http_status,
                content_type=rec.headers.get("content-type", ""),
                body=cap.blob_bytes,
                limit_exceeded=rec.limit_exceeded,
                error_msg=rec.error_msg,
            )

        if content_status != "valid":
            rejections.append(
                RejectionRecord(
                    receipt_id=rec.receipt_id,
                    seq=rec.seq,
                    source_id=rec.source_id,
                    status=content_status,
                    reason=f"Content identity validation failed: {content_reason}",
                )
            )
            continue

        # 7. Dispatch extractor
        try:
            if rec.source_id == "nyfed_effr":
                extracted = extract_nyfed_effr(cap)
            elif rec.source_id in ("fed_monetary_rss", "fed_speeches_rss"):
                extracted = extract_fed_rss(cap)
            else:
                extracted = []

            facts.extend(extracted)
        except Exception as exc:
            rejections.append(
                RejectionRecord(
                    receipt_id=rec.receipt_id,
                    seq=rec.seq,
                    source_id=rec.source_id,
                    status="extraction_failed",
                    reason=f"Payload extraction failed: {exc}",
                )
            )

    return ExtractionResult(facts=facts, rejections=rejections)
