"""Pythia R3.0: Comprehensive Offline Unit & Integration Tests for Evidence Extraction and Dossiers.

Enforces zero-network policy via test-only socket guard, and covers:
1. EFFR parsing: exact decimals, calendar dates, units, duplicate keys, NaN/Infinity/bool rejection.
2. RSS parsing: CDATA, entities, Unicode, timezone validation, raw proof spans and locators.
3. Negative & error paths: blocked, partial, corrupt/missing blobs, linkage errors, cutoff violations.
4. Lineage & provenance: A->B->A, deduplication, shared article URLs, cutoff stability.
5. Idempotency & contracts: bundle digest stability, permutation invariance, contract gaps, untouched raw archive.
6. R3.0.1 fixes: exact decimals without float rounding, BOM quotes, content gates, read-only URIs, descriptor validation, no claimed independence, atomic publication.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import sqlite3
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

import pytest

from python.evaluation.evidence_archive import EvidenceArchive
from python.evaluation.evidence_extract import (
    EXTRACTOR_VERSION,
    FACT_SCHEMA_VERSION,
    Fact,
    ProofSpan,
    extract_facts_from_captures,
    extract_fed_rss,
    extract_nyfed_effr,
)
from python.evaluation.evidence_reader import (
    ArchiveNotFoundError,
    CutoffViolationError,
    ReadOnlyReceipt,
    ReaderError,
    ReaderIntegrityError,
    ReadOnlyArchiveReader,
    VerifiedCapture,
)
from python.evaluation.event_dossier import (
    DOSSIER_VERSION,
    DossierError,
    EventDescriptor,
    build_event_dossier,
    build_origin_groups,
)
from python.evaluation.source_probe import CANONICAL_SOURCES, CaptureResult


# -------------------------------------------------------------------------
# Test-only Network Isolation Guard
# -------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def guard_no_network(monkeypatch: pytest.MonkeyPatch):
    """Enforce zero-network policy during all test executions."""
    def forbidden_connect(*args, **kwargs):
        raise RuntimeError("TEST NETWORK ACCESS FORBIDDEN: attempted network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_connect)


# -------------------------------------------------------------------------
# Helper Fixture to build test captures
# -------------------------------------------------------------------------

def create_capture(
    source_id: str,
    body: bytes,
    complete: bool = True,
    status_code: int = 200,
    headers: Dict[str, str] = None,
    error_msg: str = None,
    final_url: str = None,
) -> CaptureResult:
    headers = headers or {"content-type": "application/json"}
    return CaptureResult(
        source_id=source_id,
        initial_url=CANONICAL_SOURCES.get(source_id, f"https://example.com/{source_id}"),
        final_url=final_url or CANONICAL_SOURCES.get(source_id, f"https://example.com/{source_id}"),
        status_code=status_code,
        headers=headers,
        body=body,
        bytes_received=len(body),
        bytes_stored=len(body),
        duration_ms=42.0,
        complete=complete,
        limit_exceeded=False,
        error_msg=error_msg,
        collector_version="test-collector",
        fetch_started_at="2026-10-18T12:00:00Z",
        fetch_completed_at="2026-10-18T12:00:01Z",
    )


# -------------------------------------------------------------------------
# 1. NYFed EFFR Extraction Tests
# -------------------------------------------------------------------------

def test_nyfed_effr_multiple_dates_and_exact_decimals(tmp_path: Path):
    """EFFR extractor handles multiple dates, exact string decimals, and unit='percent'."""
    payload = {
        "refRates": [
            {
                "effectiveDate": "2026-10-15",
                "type": "EFFR",
                "percentRate": 3.88,
                "targetRateFrom": 3.75,
                "targetRateTo": 4.0,
            },
            {
                "effectiveDate": "2026-10-14",
                "type": "EFFR",
                "percentRate": 3.875,
            },
        ]
    }
    body = json.dumps(payload).encode("utf-8")
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)
    cap = create_capture("nyfed_effr", body)
    receipt = archive.record_capture(cap, receipt_id="effr-pos-001")

    reader = ReadOnlyArchiveReader(archive_dir)
    verified = reader.read_receipts([receipt.receipt_id], as_of_seq=1)
    res = extract_facts_from_captures(verified)

    assert len(res.facts) == 2
    assert len(res.rejections) == 0

    f1, f2 = res.facts
    assert f1.fact_type == "rate_observation"
    assert f1.source_id == "nyfed_effr"
    assert f1.issuer == "Federal Reserve Bank of New York"
    assert f1.unit == "percent"
    assert f1.effective_date == "2026-10-15"
    assert f1.effective_date_precision == "day"
    assert f1.published_at is None
    assert "No publication timestamp field" in f1.published_at_reason
    assert f1.value["rate"] == "3.88"
    assert f1.value["series"] == "EFFR"
    assert f1.locator["path"] == "/refRates/0"

    assert f2.effective_date == "2026-10-14"
    assert f2.value["rate"] == "3.875"
    assert f2.locator["path"] == "/refRates/1"

    # Verify proof span
    assert f1.proof.verify_against_blob(body) is True
    assert f2.proof.verify_against_blob(body) is True


def test_nyfed_effr_rejection_of_duplicate_keys(tmp_path: Path):
    """EFFR extractor rejects JSON payloads containing duplicate keys."""
    raw_json = b'{"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-15", "percentRate": 3.88, "percentRate": 4.0}]}'
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)
    cap = create_capture("nyfed_effr", raw_json)
    receipt = archive.record_capture(cap, receipt_id="effr-dup-001")

    reader = ReadOnlyArchiveReader(archive_dir)
    verified = reader.read_receipts([receipt.receipt_id], as_of_seq=1)
    res = extract_facts_from_captures(verified)

    assert len(res.facts) == 0
    assert len(res.rejections) == 1
    assert "Duplicate key in JSON" in res.rejections[0].reason or "invalid_content" in res.rejections[0].reason


def test_nyfed_effr_rejection_of_bool_nan_infinity_and_bad_calendar_dates(tmp_path: Path):
    """EFFR extractor rejects bools, NaNs, infinities, and invalid calendar dates both at extraction and probe."""
    dummy_rec = ReadOnlyReceipt(
        receipt_id="dummy",
        seq=1,
        source_id="nyfed_effr",
        initial_url=CANONICAL_SOURCES["nyfed_effr"],
        final_url=CANONICAL_SOURCES["nyfed_effr"],
        http_status=200,
        headers={"content-type": "application/json"},
        fetch_started_at="2026-10-18T12:00:00Z",
        fetch_completed_at="2026-10-18T12:00:01Z",
        recorded_at="2026-10-18T12:00:01Z",
        duration_ms=10.0,
        bytes_received=100,
        bytes_stored=100,
        complete=True,
        limit_exceeded=False,
        error_msg=None,
        collector_version="v1",
        status="valid",
        validation_level="content",
        reason="ok",
        checker_version="v1",
        usable_for_forecast=False,
        payload_fingerprint="fp",
        blob_sha256="sha",
        revision_id="rev",
    )

    # Test bool rejection in extractor
    raw_bool = b'{"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-15", "percentRate": true}]}'
    with pytest.raises(ValueError, match="Invalid non-numeric percentRate"):
        extract_nyfed_effr(VerifiedCapture(receipt=dummy_rec, revision=None, blob_bytes=raw_bool))

    # Test bad calendar date rejection (Feb 30)
    raw_bad_date = b'{"refRates": [{"type": "EFFR", "effectiveDate": "2026-02-30", "percentRate": 3.88}]}'
    with pytest.raises(ValueError, match="Invalid calendar date"):
        extract_nyfed_effr(VerifiedCapture(receipt=dummy_rec, revision=None, blob_bytes=raw_bad_date))

    # Test NaN constant rejection
    raw_nan = b'{"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-15", "percentRate": NaN}]}'
    with pytest.raises(ValueError, match="Forbidden constant in JSON|JSONDecodeError"):
        extract_nyfed_effr(VerifiedCapture(receipt=dummy_rec, revision=None, blob_bytes=raw_nan))

    # Test wrong series ignored when parsing
    raw_wrong_series = b'{"refRates": [{"type": "OBFR", "effectiveDate": "2026-10-15", "percentRate": 3.88}]}'
    facts = extract_nyfed_effr(VerifiedCapture(receipt=dummy_rec, revision=None, blob_bytes=raw_wrong_series))
    assert len(facts) == 0


# -------------------------------------------------------------------------
# 2. Fed RSS Extraction Tests
# -------------------------------------------------------------------------

def test_fed_rss_cdata_entities_and_proven_timezone_pubdate(tmp_path: Path):
    """RSS extractor parses CDATA, entities, and normalizes proven timezone RFC 2822 dates."""
    rss_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
    <channel>
        <title>FRB: Press Release - Monetary Policy</title>
        <link><![CDATA[https://www.federalreserve.gov/feeds/monetary.htm]]></link>
        <description><![CDATA[Press releases &amp; statements]]></description>
        <item>
            <title>Minutes of the Board&#39;s discount rate meetings on monetary policy</title>
            <link><![CDATA[https://www.federalreserve.gov/newsevents/pressreleases/monetary20261013a.htm]]></link>
            <guid><![CDATA[monetary20261013a]]></guid>
            <pubDate>Tue, 13 Oct 2026 18:00:00 GMT</pubDate>
            <description><![CDATA[Discount rate minutes for October.]]></description>
        </item>
    </channel>
</rss>"""
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)
    cap = create_capture("fed_monetary_rss", rss_xml, headers={"content-type": "application/rss+xml"})
    receipt = archive.record_capture(cap, receipt_id="rss-pos-001")

    reader = ReadOnlyArchiveReader(archive_dir)
    verified = reader.read_receipts([receipt.receipt_id], as_of_seq=1)
    res = extract_facts_from_captures(verified)

    assert len(res.facts) == 1
    assert len(res.rejections) == 0
    f = res.facts[0]

    assert f.fact_type == "feed_item"
    assert f.source_id == "fed_monetary_rss"
    assert f.issuer == "Federal Reserve Board"
    assert f.value["title"] == "Minutes of the Board's discount rate meetings on monetary policy"
    assert f.value["referenced_article_url"] == "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261013a.htm"
    assert f.value["article_status"] == "referenced_not_fetched"
    assert f.value["feed_excerpt"] == "Discount rate minutes for October."
    assert f.value["raw_pub_date"] == "Tue, 13 Oct 2026 18:00:00 GMT"
    assert f.published_at == "2026-10-13T18:00:00+00:00"
    assert f.published_at_reason is None

    # Proof verification
    assert f.proof.verify_against_blob(rss_xml) is True
    assert f.locator["path"] == "/rss/channel/item[1]"


def test_fed_rss_zoneless_or_missing_date_yields_null_published_at(tmp_path: Path):
    """RSS extractor sets published_at=None with reason when pubDate lacks timezone or is missing."""
    rss_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
    <channel>
        <title>Federal Reserve Board Speeches</title>
        <link>https://www.federalreserve.gov/feeds/speeches.htm</link>
        <description>Federal Reserve speeches</description>
        <item>
            <title>Speech 1 on Economic Remarks</title>
            <link>https://www.federalreserve.gov/newsevents/speech/speech1.htm</link>
            <pubDate>13 Oct 2026 18:00:00</pubDate> <!-- No timezone -->
        </item>
        <item>
            <title>Speech 2 on Economic Remarks</title>
            <link>https://www.federalreserve.gov/newsevents/speech/speech2.htm</link>
            <!-- Missing pubDate -->
        </item>
    </channel>
</rss>"""
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)
    cap = create_capture("fed_speeches_rss", rss_xml)
    receipt = archive.record_capture(cap, receipt_id="rss-dates-001")

    reader = ReadOnlyArchiveReader(archive_dir)
    verified = reader.read_receipts([receipt.receipt_id], as_of_seq=1)
    res = extract_facts_from_captures(verified)

    assert len(res.facts) == 2
    f1, f2 = res.facts
    assert f1.published_at is None
    assert "lacks timezone" in f1.published_at_reason
    assert f2.published_at is None
    assert "Missing pubDate" in f2.published_at_reason


# -------------------------------------------------------------------------
# 3. Negative & Error Paths Tests
# -------------------------------------------------------------------------

def test_blocked_partial_and_unsupported_sources_recorded_in_rejections(tmp_path: Path):
    """HTTP 403, partial/unsupported captures produce honest rejections in manifest without facts."""
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)

    # 1. HTTP 403 blocked (BLS)
    r_bls = archive.record_capture(
        create_capture("bls_cpi_rss", b"Access Denied", status_code=403),
        receipt_id="rec-bls-403",
    )
    # 2. Unsupported source (GitHub releases)
    r_gh = archive.record_capture(
        create_capture("github_deepseek_v3_releases", b"[]"),
        receipt_id="rec-gh-releases",
    )
    # 3. Legitimate negative capture (timeout)
    r_neg = archive.record_capture(
        create_capture("nyfed_effr", b"", complete=False, error_msg="Timeout"),
        receipt_id="rec-effr-timeout",
    )

    reader = ReadOnlyArchiveReader(archive_dir)
    verified = reader.read_receipts(
        [r_bls.receipt_id, r_gh.receipt_id, r_neg.receipt_id],
        as_of_seq=3,
    )
    res = extract_facts_from_captures(verified)

    assert len(res.facts) == 0
    assert len(res.rejections) == 3
    reasons = [r.reason for r in res.rejections]
    assert any("not supported for extraction in R3.0" in r for r in reasons)
    assert any("Capture was negative/failed" in r for r in reasons)


def test_missing_or_corrupt_blob_and_linkage_error_aborts_dossier(tmp_path: Path):
    """Missing or corrupted CAS blob raises ReaderIntegrityError and prevents dossier creation."""
    payload = {"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-15", "percentRate": 3.88}]}
    body = json.dumps(payload).encode("utf-8")
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)
    cap = create_capture("nyfed_effr", body)
    receipt = archive.record_capture(cap, receipt_id="rec-corrupt-001")

    # Corrupt the CAS blob on disk
    blob_file = archive._blob_path(receipt.blob_sha256)
    blob_file.write_bytes(b"corrupted_bytes")

    reader = ReadOnlyArchiveReader(archive_dir)
    with pytest.raises(ReaderIntegrityError, match="CAS blob hash corruption"):
        reader.read_receipts([receipt.receipt_id], as_of_seq=1)

    # Missing blob file entirely
    blob_file.unlink()
    with pytest.raises(ReaderIntegrityError, match="references missing CAS blob"):
        reader.read_receipts([receipt.receipt_id], as_of_seq=1)


def test_receipt_after_cutoff_aborts_entire_input(tmp_path: Path):
    """Requesting a receipt whose sequence exceeds as_of_seq raises CutoffViolationError."""
    payload = {"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-15", "percentRate": 3.88}]}
    body = json.dumps(payload).encode("utf-8")
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)
    r1 = archive.record_capture(create_capture("nyfed_effr", body), receipt_id="rec-001")
    r2 = archive.record_capture(create_capture("nyfed_effr", body), receipt_id="rec-002")

    reader = ReadOnlyArchiveReader(archive_dir)
    with pytest.raises(CutoffViolationError, match="exceeds cutoff as_of_seq=1"):
        reader.read_receipts([r1.receipt_id, r2.receipt_id], as_of_seq=1)


def test_missing_archive_directory_does_not_create_files(tmp_path: Path):
    """ReadOnlyArchiveReader raises ArchiveNotFoundError on non-existent directory without creating files."""
    missing_dir = tmp_path / "does_not_exist"
    with pytest.raises(ArchiveNotFoundError):
        ReadOnlyArchiveReader(missing_dir)
    assert not missing_dir.exists()


# -------------------------------------------------------------------------
# 4. Lineage & Provenance Tests (A -> B -> A, Multi-feed articles)
# -------------------------------------------------------------------------

def test_origin_lineage_same_article_in_multiple_feeds_groups_together(tmp_path: Path):
    """Same official article URL referenced across multiple captures maps to a single origin."""
    article_url = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261013a.htm"
    feed1 = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<title>FRB: Press Release - Monetary Policy</title>
<link>https://www.federalreserve.gov/feeds/monetary.htm</link>
<description>Monetary policy releases</description>
<item>
<title>FOMC meeting on monetary policy</title>
<link>{article_url}</link>
<pubDate>Tue, 13 Oct 2026 18:00:00 GMT</pubDate>
<description>Statement on monetary policy</description>
</item></channel></rss>""".encode("utf-8")

    feed2 = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<title>FRB: Press Release - Monetary Policy</title>
<link>https://www.federalreserve.gov/feeds/monetary.htm</link>
<description>Monetary policy releases</description>
<item>
<title>Updated FOMC announcement on monetary policy</title>
<link>{article_url}</link>
<pubDate>Tue, 13 Oct 2026 18:00:00 GMT</pubDate>
<description>Statement on monetary policy update</description>
</item></channel></rss>""".encode("utf-8")

    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)
    r1 = archive.record_capture(create_capture("fed_monetary_rss", feed1), receipt_id="rec-feed-1")
    r2 = archive.record_capture(create_capture("fed_monetary_rss", feed2), receipt_id="rec-feed-2")

    out_dir = tmp_path / "dossier_out"
    desc = EventDescriptor(event_id="fomc_origin_test", question="Test?")
    bundle_path, bundle = build_event_dossier(
        archive_dir=archive_dir,
        as_of_seq=2,
        receipt_ids=[r1.receipt_id, r2.receipt_id],
        event_descriptor=desc,
        output_dir=out_dir,
    )

    origin_groups = bundle["dossier"]["origin_groups"]
    assert len(origin_groups) == 1
    og = origin_groups[0]
    assert og["publisher"] == "Federal Reserve Board"
    assert og["canonical_article_url"] == article_url
    assert og["article_fetched"] is False
    assert "independent_evidence_count" not in og
    assert len(og["observations"]) == 2


def test_a_b_a_revisions_preserve_provenance(tmp_path: Path):
    """A -> B -> A captures retain distinct revision IDs while correctly linking to CAS blobs."""
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)

    payload_a = b'{"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-14", "percentRate": 3.85}]}'
    payload_b = b'{"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-15", "percentRate": 3.90}]}'

    r_a1 = archive.record_capture(create_capture("nyfed_effr", payload_a), receipt_id="rec-a1")
    r_b = archive.record_capture(create_capture("nyfed_effr", payload_b), receipt_id="rec-b")
    r_a2 = archive.record_capture(create_capture("nyfed_effr", payload_a), receipt_id="rec-a2")

    assert r_a1.blob_sha256 == r_a2.blob_sha256
    assert r_a1.revision_id != r_a2.revision_id  # A -> B -> A creates a new revision for a2

    reader = ReadOnlyArchiveReader(archive_dir)
    verified = reader.read_receipts([r_a1.receipt_id, r_a2.receipt_id], as_of_seq=3)
    res = extract_facts_from_captures(verified)

    assert len(res.facts) == 2
    f1, f2 = res.facts
    assert f1.revision_ids == [r_a1.revision_id]
    assert f2.revision_ids == [r_a2.revision_id]
    assert f1.blob_sha256 == f2.blob_sha256


# -------------------------------------------------------------------------
# 5. Idempotency, Contract & Output Bundle Tests
# -------------------------------------------------------------------------

def test_idempotent_bundle_digest_and_permutation_invariance(tmp_path: Path):
    """Permuting receipt IDs returns identical canonical bundle digest and preserves raw archive."""
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)

    effr_body = b'{"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-15", "percentRate": 3.88}]}'
    rss_body = b"""<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>FRB: Press Release - Monetary Policy</title><link>https://www.federalreserve.gov/feeds/monetary.htm</link><description>Monetary policy releases</description><item><title>FOMC monetary statement</title><link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20261013a.htm</link><description>Monetary statement</description></item></channel></rss>"""

    r1 = archive.record_capture(create_capture("nyfed_effr", effr_body), receipt_id="rec-1")
    r2 = archive.record_capture(create_capture("fed_monetary_rss", rss_body), receipt_id="rec-2")

    out_dir = tmp_path / "dossier_out"
    desc = EventDescriptor(
        event_id="fixture_fomc_synth",
        question="Will EFFR stay within 3.75 - 4.00?",
        contract_status="incomplete",
    )

    # Run 1: order [r1, r2]
    p1, b1 = build_event_dossier(
        archive_dir=archive_dir,
        as_of_seq=2,
        receipt_ids=[r1.receipt_id, r2.receipt_id],
        event_descriptor=desc,
        output_dir=out_dir,
    )

    # Run 2: order [r2, r1, r2] (duplicates and permuted)
    p2, b2 = build_event_dossier(
        archive_dir=archive_dir,
        as_of_seq=2,
        receipt_ids=[r2.receipt_id, r1.receipt_id, r2.receipt_id],
        event_descriptor=desc,
        output_dir=out_dir,
    )

    assert p1 == p2
    assert b1["bundle_digest"] == b2["bundle_digest"]
    assert b1["dossier"] == b2["dossier"]
    assert b1["dossier"]["metadata"]["usable_for_forecast"] is False
    assert b1["dossier"]["metadata"]["dossier_type"] == "context_only"
    assert b1["dossier"]["event_descriptor"]["contract_status"] == "incomplete"

    # Verify that raw archive was not modified
    integ = archive.verify_integrity()
    assert integ["healthy"] is True


def test_output_dir_cannot_be_inside_archive_dir(tmp_path: Path):
    """Dossier builder raises ValueError if output directory is inside archive directory."""
    payload = {"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-15", "percentRate": 3.88}]}
    body = json.dumps(payload).encode("utf-8")
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)
    r1 = archive.record_capture(create_capture("nyfed_effr", body), receipt_id="rec-1")

    nested_out = archive_dir / "nested_dossiers"
    desc = EventDescriptor(event_id="test", question="Test?")
    with pytest.raises(ValueError, match="must not be located inside archive_dir"):
        build_event_dossier(
            archive_dir=archive_dir,
            as_of_seq=1,
            receipt_ids=[r1.receipt_id],
            event_descriptor=desc,
            output_dir=nested_out,
        )


# -------------------------------------------------------------------------
# 6. R3.0.1 Regression Tests (F1 - F7)
# -------------------------------------------------------------------------

def test_f1_exact_decimal_without_float_rounding(tmp_path: Path):
    """F1: High-precision decimals and small exponents are parsed directly without float truncation."""
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)

    raw_json = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-15","percentRate":3.8800000000000001},{"type":"EFFR","effectiveDate":"2026-10-14","percentRate":1e-400}]}'
    r = archive.record_capture(create_capture("nyfed_effr", raw_json), receipt_id="rec-f1")

    reader = ReadOnlyArchiveReader(archive_dir)
    verified = reader.read_receipts([r.receipt_id], as_of_seq=1)
    res = extract_facts_from_captures(verified)

    assert len(res.facts) == 2
    assert res.facts[0].value["rate"] == "3.8800000000000001"
    assert res.facts[1].value["rate"] == "1E-400"


def test_f2_bom_preservation_and_verbatim_proof(tmp_path: Path):
    """F2: Feeds with UTF-8 BOM preserve BOM in raw_quote_text and pass verify_against_blob."""
    rss = b'<rss version="2.0"><channel><title>Federal Reserve Monetary Policy</title><link>https://www.federalreserve.gov/feeds/monetary.htm</link><item><title>FOMC statement</title><link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20261013a.htm</link><pubDate>Tue, 13 Oct 2026 18:00:00 GMT</pubDate><description><![CDATA[Original excerpt]]></description></item></channel></rss>'
    bom_rss = b"\xef\xbb\xbf" + rss

    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)
    r = archive.record_capture(create_capture("fed_monetary_rss", bom_rss), receipt_id="rec-f2")

    reader = ReadOnlyArchiveReader(archive_dir)
    verified = reader.read_receipts([r.receipt_id], as_of_seq=1)
    res = extract_facts_from_captures(verified)

    assert len(res.facts) == 1
    f = res.facts[0]
    assert f.proof.raw_quote_text is not None
    assert f.proof.raw_quote_text.startswith("\ufeff")
    assert f.proof.verify_against_blob(bom_rss) is True


def test_f3_content_and_transport_gate_rejects_untrusted_valid_labels(tmp_path: Path):
    """F3: Rejects HTTP 403, wrong feed identity, and unsupported RSS version even if status=valid."""
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)
    valid_diag = {"status": "valid", "validation_level": "content", "reason": "untrusted fixture", "checker_version": "review/1"}

    # 1. HTTP 403 transport failure
    effr_plain = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-15","percentRate":3.88}]}'
    r_403 = archive.record_capture(create_capture("nyfed_effr", effr_plain, status_code=403), receipt_id="rec-403", probe_result=valid_diag)

    # 2. Altered feed identity
    rss = b'<rss version="2.0"><channel><title>Unrelated Banking News</title><link>https://example.invalid</link><item><title>FOMC</title><link>https://example.invalid/item</link><description>text</description></item></channel></rss>'
    r_ident = archive.record_capture(create_capture("fed_monetary_rss", rss), receipt_id="rec-ident", probe_result=valid_diag)

    # 3. Unsupported RSS version
    rss_v9 = b'<rss version="9.9"><channel><title>Federal Reserve Monetary Policy</title><link>https://www.federalreserve.gov/feeds/monetary.htm</link><item><title>FOMC</title><link>https://www.federalreserve.gov/item</link><description>text</description></item></channel></rss>'
    r_v9 = archive.record_capture(create_capture("fed_monetary_rss", rss_v9), receipt_id="rec-v9")

    reader = ReadOnlyArchiveReader(archive_dir)
    verified = reader.read_receipts([r_403.receipt_id, r_ident.receipt_id, r_v9.receipt_id], as_of_seq=3)
    res = extract_facts_from_captures(verified)

    assert len(res.facts) == 0
    assert len(res.rejections) == 3


def test_f4_complete_clean_receipt_missing_mandatory_blob(tmp_path: Path):
    """F4: Complete clean HTTP 200 receipt missing CAS blob/revision raises ReaderIntegrityError without publishing."""
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)
    body = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-15","percentRate":3.88}]}'
    r = archive.record_capture(create_capture("nyfed_effr", body), receipt_id="rec-f4-missing")

    # Corrupt receipts table: drop append-only triggers and set blob/revision to NULL
    conn = sqlite3.connect(archive.db_path)
    for (tname,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='receipts'").fetchall():
        conn.execute(f'DROP TRIGGER "{tname}"')
    conn.execute("UPDATE receipts SET blob_sha256=NULL, revision_id=NULL WHERE receipt_id=?", (r.receipt_id,))
    conn.commit()
    conn.close()

    desc = EventDescriptor(event_id="f4_test", question="Test?")
    with pytest.raises(ReaderIntegrityError, match="missing mandatory CAS blob and revision linkage"):
        build_event_dossier(archive_dir, 1, [r.receipt_id], desc, tmp_path / "out")


def test_f4_read_only_uri_escaping(tmp_path: Path):
    """F4: Read-only SQLite URI escaping safely handles ? and # characters without creating sibling files."""
    special_dir = tmp_path / "uri?fixture#special"
    special_dir.mkdir()
    archive = EvidenceArchive(special_dir)
    body = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-15","percentRate":3.88}]}'
    r = archive.record_capture(create_capture("nyfed_effr", body), receipt_id="rec-f4-uri")

    unexpected_sibling = tmp_path / "uri"
    assert not unexpected_sibling.exists()

    reader = ReadOnlyArchiveReader(special_dir)
    caps = reader.read_receipts([r.receipt_id], as_of_seq=1)
    assert len(caps) == 1
    assert not unexpected_sibling.exists()


def test_f5_descriptor_validation_and_frozen_versions_in_empty_dossier(tmp_path: Path):
    """F5: Descriptor requires non-empty fields and consistent contract status; versions are frozen in empty dossiers."""
    # 1. Empty event_id or question rejected
    with pytest.raises(ValueError, match="event_id must be a non-empty string"):
        EventDescriptor(event_id="", question="Question?")
    with pytest.raises(ValueError, match="question must be a non-empty string"):
        EventDescriptor(event_id="id", question="")

    # 2. rules=None cannot be verified
    with pytest.raises(ValueError, match="contract_status cannot be 'verified' when rules is None"):
        EventDescriptor(event_id="id", question="Q?", rules=None, contract_status="verified")

    # 3. All-rejected dossier preserves frozen versions
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)
    cap = create_capture("nyfed_effr", b"", complete=False, error_msg="Timeout")
    r = archive.record_capture(cap, receipt_id="rec-f5-neg")

    desc = EventDescriptor(event_id="f5_empty", question="Empty test?", contract_status="incomplete")
    _, bundle = build_event_dossier(archive_dir, 1, [r.receipt_id], desc, tmp_path / "out")

    dossier = bundle["dossier"]
    assert dossier["manifest"]["extractor_version"] == EXTRACTOR_VERSION
    assert dossier["manifest"]["fact_schema_version"] == FACT_SCHEMA_VERSION
    assert dossier["manifest"]["dossier_version"] == DOSSIER_VERSION
    assert dossier["extractor_version"] == EXTRACTOR_VERSION
    assert dossier["fact_schema_version"] == FACT_SCHEMA_VERSION
    assert dossier["dossier_version"] == DOSSIER_VERSION
    assert dossier["manifest"]["total_facts_extracted"] == 0
    assert dossier["manifest"]["total_rejected"] == 1


def test_f6_origin_groups_no_claimed_independence_and_correct_nyfed_url(tmp_path: Path):
    """F6: Origin groups do not claim independent_evidence_count and use correct canonical NYFed endpoint."""
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)
    body = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-15","percentRate":3.88}]}'
    r = archive.record_capture(create_capture("nyfed_effr", body), receipt_id="rec-f6")

    desc = EventDescriptor(event_id="f6_test", question="Test?", contract_status="incomplete")
    _, bundle = build_event_dossier(archive_dir, 1, [r.receipt_id], desc, tmp_path / "out")

    groups = bundle["dossier"]["origin_groups"]
    assert len(groups) == 1
    g = groups[0]
    assert "independent_evidence_count" not in g
    assert g["canonical_url"] == CANONICAL_SOURCES["nyfed_effr"]


def test_f7_atomic_no_clobber_publication_and_collision_rejection(tmp_path: Path):
    """F7: Competing files at destination are not clobbered and raise DossierError; identical bytes replay."""
    archive_dir = tmp_path / "archive"
    archive = EvidenceArchive(archive_dir)
    body = b'{"refRates":[{"type":"EFFR","effectiveDate":"2026-10-15","percentRate":3.88}]}'
    r = archive.record_capture(create_capture("nyfed_effr", body), receipt_id="rec-f7")

    out_dir = tmp_path / "out"
    desc = EventDescriptor(event_id="f7_test", question="Test?", contract_status="incomplete")

    # First publication
    p1, b1 = build_event_dossier(archive_dir, 1, [r.receipt_id], desc, out_dir)
    assert p1.is_file()

    # Idempotent replay with identical content
    p2, b2 = build_event_dossier(archive_dir, 1, [r.receipt_id], desc, out_dir)
    assert p1 == p2
    assert b1 == b2

    # Collision test: competing pre-existing bytes must NOT be clobbered
    out_dir_comp = tmp_path / "out_comp"
    out_dir_comp.mkdir()
    sentinel = b"pre-existing competing file"

    # Pre-calculate content digest
    canon = json.dumps(b1["dossier"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    digest = hashlib.sha256(canon).hexdigest()
    competing_file = out_dir_comp / f"dossier_{digest}.json"
    competing_file.write_bytes(sentinel)

    with pytest.raises(DossierError, match="Atomic publication conflict"):
        build_event_dossier(archive_dir, 1, [r.receipt_id], desc, out_dir_comp)

    # Verify competing file was preserved
    assert competing_file.read_bytes() == sentinel
