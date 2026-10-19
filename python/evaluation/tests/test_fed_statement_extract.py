"""Tests for Pythia R3.1 / R3.1.2: Offline Fed Statement Extractor and Publisher.

Enforces:
1. Real official fixtures: accurate extraction, paragraph ordering, absence of nav/footer, valid ProofSpans.
2. Synthetic variations: UTF-8 BOM, entities, inline tags, Unicode; corrupt SHA and bad encoding rejected honestly.
3. Negative / non-statement templates: discount rate minutes, projections, speeches, Cloudflare challenge, empty bodies rejected.
4. Dates: calendar-only yields published_at=None; timezone string parsed explicitly; invalid dates handled safely; separate times.
5. Idempotent publication replay, collision conflict rejection (no-clobber), versions preserved in empty/rejected bundles.
6. Zero network: socket connections strictly forbidden across tests and child CLI process.
7. F1: Mandatory typed manifest & provenance validation (empty manifest, non-fed domains, transport failures, type gates).
8. F2: Negative body remains negative (JS-only barrier and discount rate minutes body rejected).
9. F3: Structural locators unambiguous (cardinality 1); proofs match exact DOM nodes skipping comments and duplicate siblings.
10. F4: HTML entities decoded exactly once (no double unescaping of literal escaped entities).
11. F5: Invalid hour/minute yields published_at=None; comparable precision instant comparison (EDT vs JST same instant).
12. F6: Output directory, manifest directory, and preview cannot overwrite source inputs or follow temp symlinks.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import pytest
from bs4 import BeautifulSoup

from python.evaluation.evidence_extract import ProofSpan
from python.evaluation.fed_statement_extract import (
    STATEMENT_BUNDLE_VERSION,
    STATEMENT_EXTRACTOR_VERSION,
    STATEMENT_SCHEMA_VERSION,
    StatementDocument,
    StatementIntegrityError,
    StatementParagraph,
    StatementPublishError,
    build_fed_statement_bundle,
    extract_fed_statement,
    generate_statement_preview,
    publish_statement_preview,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "docs/research/2026-10-19/r3/increment1/revision2/fixtures"


@pytest.fixture(autouse=True)
def enforce_zero_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enforce strictly zero network access across all tests."""
    def forbid(*args, **kwargs):
        raise RuntimeError("Network access strictly forbidden during offline tests")

    monkeypatch.setattr(socket.socket, "connect", forbid)
    monkeypatch.setattr(socket.socket, "connect_ex", forbid)
    monkeypatch.setattr(socket, "getaddrinfo", forbid)


def _make_fixture_and_manifest(
    tmp_path: Path,
    name: str,
    raw_html: bytes,
    source_url: str = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
    corrupt_sha: bool = False,
    corrupt_size: bool = False,
) -> Tuple[Path, Path]:
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    fpath = fixtures_dir / f"{name}.html"
    fpath.write_bytes(raw_html)

    sha = hashlib.sha256(raw_html).hexdigest()
    if corrupt_sha:
        sha = "0" * 64

    stored_size = len(raw_html)
    if corrupt_size:
        stored_size += 100

    manifest = {
        "fixture_key": name,
        "source_url": source_url,
        "final_url": source_url,
        "redirect_count": 0,
        "http_status": 200,
        "bytes_stored": stored_size,
        "blob_sha256": sha,
        "fetch_started_at": "2026-10-19T10:00:00+00:00",
        "fetch_completed_at": "2026-10-19T10:00:01+00:00",
        "duration_ms": 1000.0,
        "collected_at": "2026-10-19T10:00:01+00:00",
        "research_provenance": "synthetic_fixture",
        "issuer": "Federal Reserve Board",
        "parent_feed_reference": {
            "fixture_kind": "synthetic_fixture",
            "source_id": "fed_monetary_rss",
            "feed_fact_id": "fact-synthetic-test",
            "feed_receipt_id": "receipt-synthetic-test",
        },
        "transport_outcome": "success",
        "usable_for_forecast": False,
    }
    mpath = fixtures_dir / f"{name}_manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return fpath, mpath


def _create_minimal_valid_html(
    title: str = "Federal Reserve issues FOMC statement",
    date_text: str = "October 15, 2026",
    release_time: str = "For release at 2:00 p.m. EDT",
    paragraphs: Optional[List[str]] = None,
    leading_bytes: bytes = b"",
) -> bytes:
    if paragraphs is None:
        paragraphs = [
            "The Federal Open Market Committee approved the following statement for release by a 12 – 0 vote:",
            "The Committee decided to maintain the target range for the federal funds rate at 4 to 4-1/4 percent.",
            "Economic activity has continued to expand at a solid pace.",
        ]

    p_html = "\n".join(f"<p>{p}</p>" for p in paragraphs)
    html_str = f"""<!doctype html>
<html>
<head><title>Board of Governors - {title}</title></head>
<body>
<nav>Navigation Header (Should not be in extraction)</nav>
<div id="article">
    <div class="heading col-xs-12 col-sm-8 col-md-8">
        <p class="article__time">{date_text}</p>
        <h3 class="title">{title}</h3>
        <p class="releaseTime">{release_time}</p>
    </div>
    <div class="col-xs-12 col-sm-4 col-md-4 hidden-sm">Sidebar</div>
    <div class="col-xs-12 col-sm-8 col-md-8">
        {p_html}
    </div>
</div>
<footer>Footer links (Should not be in extraction)</footer>
</body>
</html>"""
    return leading_bytes + html_str.encode("utf-8")


def test_real_fixture_monetary20260916a_extraction():
    """Real September 16, 2026 FOMC statement extracted cleanly with 100% valid ProofSpans."""
    raw_path = FIXTURES_DIR / "raw_monetary20260916a.html"
    man_path = FIXTURES_DIR / "manifest_monetary20260916a.json"
    assert raw_path.is_file(), f"Committed real fixture missing: {raw_path}"
    assert man_path.is_file(), f"Committed manifest missing: {man_path}"

    raw_bytes = raw_path.read_bytes()
    manifest = json.loads(man_path.read_text(encoding="utf-8"))

    result = extract_fed_statement(raw_bytes, manifest)
    assert result.is_success
    doc = result.document
    assert doc is not None

    assert doc.title == "Federal Reserve issues FOMC statement"
    assert doc.published_date == "2026-09-16"
    assert doc.published_date_precision == "day"
    assert doc.published_at == "2026-09-16T14:00:00-04:00"
    assert doc.published_at_provenance == "header_release_time"
    assert len(doc.paragraphs) == 6

    # Verify proofs
    assert doc.container_proof.verify_against_blob(raw_bytes)
    assert doc.title_proof.verify_against_blob(raw_bytes)
    assert doc.date_proof.verify_against_blob(raw_bytes)
    if doc.release_time_proof:
        assert doc.release_time_proof.verify_against_blob(raw_bytes)

    for p in doc.paragraphs:
        assert p.proof.verify_against_blob(raw_bytes)


def test_real_fixture_monetary20260729a_extraction():
    """Real July 29, 2026 FOMC statement extracted with 7 paragraphs including dissent."""
    raw_path = FIXTURES_DIR / "raw_monetary20260729a.html"
    man_path = FIXTURES_DIR / "manifest_monetary20260729a.json"
    assert raw_path.is_file(), f"Committed real fixture missing: {raw_path}"
    assert man_path.is_file(), f"Committed manifest missing: {man_path}"

    raw_bytes = raw_path.read_bytes()
    manifest = json.loads(man_path.read_text(encoding="utf-8"))

    result = extract_fed_statement(raw_bytes, manifest)
    assert result.is_success
    doc = result.document
    assert doc is not None

    assert doc.title == "Federal Reserve issues FOMC statement"
    assert doc.published_date == "2026-07-29"
    assert len(doc.paragraphs) == 7

    # Check dissent paragraph
    roles = [p.role for p in doc.paragraphs]
    assert "dissenting_votes" in roles
    assert "vote_approval" in roles
    assert "policy_decision" in roles

    for p in doc.paragraphs:
        assert p.proof.verify_against_blob(raw_bytes)


def test_synthetic_fixture_with_bom_and_entities(tmp_path: Path):
    """HTML with UTF-8 BOM, entities, and inline tags extracts exact clean text."""
    raw = _create_minimal_valid_html(
        title="Federal Reserve issues FOMC statement",
        paragraphs=[
            "The Committee approved the statement by a 12 &#8211; 0 vote.",
            "Interest rates maintained at <strong>4.00</strong> to 4.25 percent &amp; reserves remain ample.",
        ],
        leading_bytes=b"\xef\xbb\xbf",
    )
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "bom_test", raw)
    manifest = json.loads(mpath.read_text(encoding="utf-8"))

    result = extract_fed_statement(raw, manifest)
    assert result.is_success
    doc = result.document
    assert doc is not None
    assert doc.paragraphs[0].display_text == "The Committee approved the statement by a 12 – 0 vote."
    assert "4.00 to 4.25 percent & reserves remain ample." in doc.paragraphs[1].display_text

    for p in doc.paragraphs:
        assert p.proof.verify_against_blob(raw)


def test_corrupt_sha256_rejected(tmp_path: Path):
    """Corrupt SHA-256 in manifest causes honest rejection."""
    raw = _create_minimal_valid_html()
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "corrupt_sha", raw, corrupt_sha=True)
    manifest = json.loads(mpath.read_text(encoding="utf-8"))

    result = extract_fed_statement(raw, manifest)
    assert not result.is_success
    assert len(result.rejections) == 1
    assert result.rejections[0].status == "digest_mismatch"


def test_corrupt_size_rejected(tmp_path: Path):
    """Corrupt size in manifest causes honest rejection."""
    raw = _create_minimal_valid_html()
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "corrupt_size", raw, corrupt_size=True)
    manifest = json.loads(mpath.read_text(encoding="utf-8"))

    result = extract_fed_statement(raw, manifest)
    assert not result.is_success
    assert len(result.rejections) == 1
    assert result.rejections[0].status == "size_mismatch"


def test_invalid_utf8_encoding_rejected(tmp_path: Path):
    """Invalid UTF-8 bytes rejected honestly."""
    bad_bytes = b"\xff\xfe\x00\x00Some non-utf8 binary junk"
    manifest = {
        "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
        "final_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
        "bytes_stored": len(bad_bytes),
        "blob_sha256": hashlib.sha256(bad_bytes).hexdigest(),
        "transport_outcome": "success",
        "http_status": 200,
        "research_provenance": "synthetic_fixture",
        "issuer": "Federal Reserve Board",
        "parent_feed_reference": {"fixture_kind": "synthetic_fixture"},
    }
    result = extract_fed_statement(bad_bytes, manifest)
    assert not result.is_success
    assert result.rejections[0].status == "encoding_error"


def test_unsupported_document_types_rejected(tmp_path: Path):
    """Minutes, speeches, and projections in title are rejected as unsupported document types."""
    cases = [
        "Minutes of the Federal Open Market Committee",
        "Summary of Economic Projections",
        "Speech by Chair on Economic Outlook",
        "Federal Reserve issues FOMC statement on discount rate",
    ]
    for title in cases:
        raw = _create_minimal_valid_html(title=title)
        _, mpath = _make_fixture_and_manifest(tmp_path, f"rej_{hash(title)}", raw)
        manifest = json.loads(mpath.read_text(encoding="utf-8"))

        result = extract_fed_statement(raw, manifest)
        assert not result.is_success
        assert result.rejections[0].status == "unsupported_document_type"


def test_challenge_barrier_rejected(tmp_path: Path):
    """Cloudflare challenge / verification HTML rejected honestly."""
    challenge_html = b"""<!doctype html>
<html>
<head><title>Just a moment...</title></head>
<body>
<div id="challenge-running">Validating browser...</div>
</body>
</html>"""
    manifest = {
        "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
        "final_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
        "bytes_stored": len(challenge_html),
        "blob_sha256": hashlib.sha256(challenge_html).hexdigest(),
        "transport_outcome": "success",
        "http_status": 200,
        "research_provenance": "synthetic_fixture",
        "issuer": "Federal Reserve Board",
        "parent_feed_reference": {"fixture_kind": "synthetic_fixture"},
    }
    result = extract_fed_statement(challenge_html, manifest)
    assert not result.is_success
    assert result.rejections[0].status == "blocked_or_challenge"


def test_empty_content_rejected(tmp_path: Path):
    """Container with no paragraphs or only whitespace paragraphs rejected."""
    raw = _create_minimal_valid_html(paragraphs=[])
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "empty_paras", raw)
    res = extract_fed_statement(fpath.read_bytes(), json.loads(mpath.read_text()))
    assert not res.is_success
    assert len(res.rejections) == 1
    assert res.rejections[0].status == "empty_content"


def test_missing_article_structure_rejected(tmp_path: Path):
    """HTML lacking #article structure rejected without fallback to whole-page text."""
    bad_html = b"""<!doctype html>
<html>
<head><title>Federal Reserve issues FOMC statement</title></head>
<body>
<p>The Federal Open Market Committee approved the statement...</p>
</body>
</html>"""
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "no_article", bad_html)
    res = extract_fed_statement(fpath.read_bytes(), json.loads(mpath.read_text()))
    assert not res.is_success
    assert len(res.rejections) == 1
    assert res.rejections[0].status == "unsupported_structure"


def test_date_only_fixture_yields_null_published_at(tmp_path: Path):
    """Date-only release yields published_date with precision='day' and published_at=None."""
    raw = _create_minimal_valid_html(release_time="For immediate release")
    _, mpath = _make_fixture_and_manifest(tmp_path, "date_only", raw)
    manifest = json.loads(mpath.read_text(encoding="utf-8"))

    result = extract_fed_statement(raw, manifest)
    assert result.is_success
    doc = result.document
    assert doc is not None
    assert doc.published_date == "2026-10-15"
    assert doc.published_date_precision == "day"
    assert doc.published_at is None
    assert doc.published_at_provenance is None


def test_explicit_timezone_release_time_parsed(tmp_path: Path):
    """Explicit release time with EST timezone parsed accurately."""
    raw = _create_minimal_valid_html(
        date_text="November 05, 2026",
        release_time="For release at 2:00 p.m. EST",
    )
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "tz_time", raw)
    res = extract_fed_statement(fpath.read_bytes(), json.loads(mpath.read_text()))
    assert res.is_success
    doc = res.document
    assert doc is not None
    assert doc.published_date == "2026-11-05"
    assert doc.published_at == "2026-11-05T14:00:00-05:00"
    assert doc.published_at_provenance == "header_release_time"


def test_invalid_date_rejected_safely(tmp_path: Path):
    """Unparseable date text results in rejection rather than silent fallback."""
    raw = _create_minimal_valid_html(date_text="Not A Valid Calendar Date")
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "bad_date", raw)
    res = extract_fed_statement(fpath.read_bytes(), json.loads(mpath.read_text()))
    assert not res.is_success
    assert len(res.rejections) == 1
    assert res.rejections[0].status == "invalid_date"


def test_bundle_publication_idempotence_and_collision_conflict(tmp_path: Path):
    """Atomic bundle publication: replay is idempotent; competing content raises conflict and preserves bytes."""
    raw = _create_minimal_valid_html()
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "bundle_test", raw)
    out_dir = tmp_path / "bundles"

    path1, bundle1 = build_fed_statement_bundle(fpath, mpath, out_dir)
    assert path1.is_file()
    digest = bundle1["bundle_digest"]

    # Replay identical
    path2, bundle2 = build_fed_statement_bundle(fpath, mpath, out_dir)
    assert path1 == path2
    assert bundle1["bundle_digest"] == bundle2["bundle_digest"]

    # Verify versions in manifest and bundle
    bundle_data = json.loads(path1.read_text(encoding="utf-8"))["statement_bundle"]
    assert bundle_data["extractor_version"] == STATEMENT_EXTRACTOR_VERSION
    assert bundle_data["schema_version"] == STATEMENT_SCHEMA_VERSION
    assert bundle_data["bundle_version"] == STATEMENT_BUNDLE_VERSION

    # Collision test: competing pre-existing bytes must NOT be clobbered
    out_dir_comp = tmp_path / "output_comp"
    out_dir_comp.mkdir()
    sentinel = b"competing pre-existing file content"
    target = out_dir_comp / f"statement_bundle_{digest}.json"
    target.write_bytes(sentinel)

    with pytest.raises(StatementPublishError, match="Atomic publication conflict"):
        build_fed_statement_bundle(fpath, mpath, out_dir_comp)

    # Verify competing file was not overwritten
    assert target.read_bytes() == sentinel


def test_rejected_bundle_publication_preserves_frozen_versions(tmp_path: Path):
    """Rejection outcomes still produce published bundles preserving schema/extractor/bundle versions."""
    raw = _create_minimal_valid_html(title="Minutes of the Federal Open Market Committee")
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "rejected_test", raw)
    out_dir = tmp_path / "bundles"

    path, bundle_data = build_fed_statement_bundle(fpath, mpath, out_dir)
    assert path.is_file()
    b = bundle_data["statement_bundle"]
    assert b["extractor_version"] == STATEMENT_EXTRACTOR_VERSION
    assert b["schema_version"] == STATEMENT_SCHEMA_VERSION
    assert b["bundle_version"] == STATEMENT_BUNDLE_VERSION
    assert b["manifest"]["extraction_summary"]["status"] == "rejected"
    assert b["document"] is None
    assert len(b["rejections"]) > 0


def test_preview_generation_from_bundle(tmp_path: Path):
    """Preview is generated strictly from bundle dictionary without external calls."""
    raw = _create_minimal_valid_html()
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "preview_test", raw)
    _, bundle_data = build_fed_statement_bundle(fpath, mpath, tmp_path / "bundles")

    preview = generate_statement_preview(bundle_data)
    assert "# FOMC Statement Research Preview" in preview
    assert bundle_data["bundle_digest"] in preview
    assert "Federal Reserve issues FOMC statement" in preview
    assert "VALID" in preview


def test_cli_execution_with_child_network_guard(tmp_path: Path):
    """CLI child process executes with sitecustomize network guard and outputs valid bundle and preview."""
    raw = _create_minimal_valid_html()
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "cli_test", raw)
    out_dir = tmp_path / "bundles"
    preview_file = tmp_path / "previews" / "cli_preview.md"

    guard_dir = tmp_path / "child_guard"
    guard_dir.mkdir()
    child_marker = tmp_path / "child_network_attempted"
    (guard_dir / "sitecustomize.py").write_text(
        "import socket\n"
        "from pathlib import Path\n"
        "def forbid(*args, **kwargs):\n"
        f"    Path({repr(str(child_marker))}).write_text('blocked')\n"
        "    raise RuntimeError('Child network forbidden')\n"
        "socket.socket.connect = forbid\n"
        "socket.socket.connect_ex = forbid\n"
        "socket.getaddrinfo = forbid\n"
    )

    env = dict(os.environ)
    REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
    env["PYTHONPATH"] = f"{guard_dir}{os.pathsep}{REPO_ROOT}" 

    cmd = [
        sys.executable,
        "-m",
        "python.evaluation.fed_statement_extract",
        "--fixture-path",
        str(fpath),
        "--manifest-path",
        str(mpath),
        "--output-dir",
        str(out_dir),
        "--preview-path",
        str(preview_file),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert proc.returncode == 0, f"CLI failed: {proc.stderr}"
    assert not child_marker.exists()
    assert preview_file.is_file()


# ==============================================================================
# R3.1.2 Boundary Regressions (F1 - F6)
# ==============================================================================

def test_f1_mandatory_manifest_validation(tmp_path: Path):
    """F1: Empty manifest, missing fields, type violations, or contradictory metadata are rejected."""
    raw = _create_minimal_valid_html()

    # Empty manifest
    res1 = extract_fed_statement(raw, {})
    assert not res1.is_success
    assert res1.rejections[0].status == "invalid_manifest"

    # Non-official domain
    m_bad_url = {
        "source_url": "https://example.org/not-fed.html",
        "final_url": "https://example.org/not-fed.html",
        "blob_sha256": hashlib.sha256(raw).hexdigest(),
        "bytes_stored": len(raw),
        "transport_outcome": "success",
        "http_status": 200,
        "research_provenance": "synthetic_fixture",
    }
    res2 = extract_fed_statement(raw, m_bad_url)
    assert not res2.is_success
    assert res2.rejections[0].status == "unsupported_source_url"

    # Non-HTTPS URL
    m_http = copy.deepcopy(m_bad_url)
    m_http.update(source_url="http://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
                  final_url="http://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm")
    res_http = extract_fed_statement(raw, m_http)
    assert not res_http.is_success
    assert res_http.rejections[0].status == "unsupported_source_url"

    # Non-statement path on official host
    m_path = copy.deepcopy(m_bad_url)
    m_path.update(source_url="https://www.federalreserve.gov/aboutthefed.htm",
                  final_url="https://www.federalreserve.gov/aboutthefed.htm")
    res_path = extract_fed_statement(raw, m_path)
    assert not res_path.is_success
    assert res_path.rejections[0].status == "unsupported_source_url"

    # Non-string source URL (must not crash with AttributeError)
    m_int_url = copy.deepcopy(m_bad_url)
    m_int_url["source_url"] = 12345
    res_int_url = extract_fed_statement(raw, m_int_url)
    assert not res_int_url.is_success
    assert res_int_url.rejections[0].status == "invalid_manifest"

    # Non-integer bytes_stored (float rejected)
    m_float_size = copy.deepcopy(m_bad_url)
    m_float_size.update(source_url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
                        final_url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
                        bytes_stored=float(len(raw)))
    res_float = extract_fed_statement(raw, m_float_size)
    assert not res_float.is_success
    assert res_float.rejections[0].status == "invalid_manifest"

    # Null http_status
    m_null_http = copy.deepcopy(m_float_size)
    m_null_http.update(bytes_stored=len(raw), http_status=None)
    res_null_http = extract_fed_statement(raw, m_null_http)
    assert not res_null_http.is_success
    assert res_null_http.rejections[0].status == "invalid_manifest"

    # Transport failure (HTTP 403)
    m_fail = copy.deepcopy(m_null_http)
    m_fail.update(http_status=403, transport_outcome="http_error")
    res3 = extract_fed_statement(raw, m_fail)
    assert not res3.is_success
    assert res3.rejections[0].status == "transport_failure"

    # Explicit complete=False / eof_complete=False
    m_eof = copy.deepcopy(m_null_http)
    m_eof.update(http_status=200, transport_outcome="success", eof_complete=False)
    res_eof = extract_fed_statement(raw, m_eof)
    assert not res_eof.is_success
    assert res_eof.rejections[0].status == "transport_failure"

    # Contradictory provenance and forecast usability
    m_contra = copy.deepcopy(m_null_http)
    m_contra.update(http_status=200, transport_outcome="success",
                    research_provenance="production_verified", usable_for_forecast=True,
                    issuer="An unrelated issuer", parent_feed_reference={})
    res_contra = extract_fed_statement(raw, m_contra)
    assert not res_contra.is_success
    assert res_contra.rejections[0].status == "invalid_manifest"


def test_f2_negative_body_js_only(tmp_path: Path):
    """F2: Valid #article skeleton with JS-only body paragraph is rejected."""
    raw = _create_minimal_valid_html(
        paragraphs=["Enable JavaScript to view this article."]
    )
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "js_only", raw)
    manifest = json.loads(mpath.read_text())

    res = extract_fed_statement(raw, manifest)
    assert not res.is_success
    assert res.rejections[0].status == "blocked_or_challenge"


def test_f2_unsupported_minutes_body_rejected(tmp_path: Path):
    """F2: Supported statement title with discount rate minutes body is rejected."""
    raw = _create_minimal_valid_html(
        paragraphs=["Minutes of the Board of Governors discount rate meetings on August 24 and September 14, 2026."]
    )
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "minutes_body", raw)
    manifest = json.loads(mpath.read_text())

    res = extract_fed_statement(raw, manifest)
    assert not res.is_success
    assert res.rejections[0].status == "unsupported_document_type"


def test_f3_unambiguous_structural_locators(tmp_path: Path):
    """F3: Container and paragraph locators must resolve unambiguously (cardinality 1)."""
    nested_html = (
        b'<!doctype html><html><body>\r\n'
        b'<div id="article">\r\n'
        b'<!-- <h3 class="title">Commented Title</h3> -->\r\n'
        b'<div class="heading col-xs-12 col-sm-8 col-md-8">\r\n'
        b'<p class="article__time">October 15, 2026</p>\r\n'
        b'<h3 class="title">Federal Reserve issues FOMC statement</h3>\r\n'
        b'<p class="releaseTime">For release at 2:00 p.m. EDT</p>\r\n'
        b'</div>\r\n'
        b'<div class="col-xs-12 col-sm-8 col-md-8">\r\n'
        b'<p>First statement paragraph.</p>\r\n'
        b'<div><p>Second statement paragraph.</p></div>\r\n'
        b'<p>Third statement paragraph.</p>\r\n'
        b'</div>\r\n'
        b'</div>\r\n'
        b'</body></html>'
    )
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "nested_f3", nested_html)
    manifest = json.loads(mpath.read_text())

    res = extract_fed_statement(nested_html, manifest)
    assert res.is_success
    doc = res.document
    assert doc is not None

    # Title proof must match actual parsed title, not the comment
    assert doc.title == "Federal Reserve issues FOMC statement"
    assert "Commented" not in doc.title_proof.raw_quote_text

    soup = BeautifulSoup(nested_html.decode("utf-8"), "html.parser")
    # Container cardinality == 1
    assert len(soup.select(doc.container_locator["selector"])) == 1

    # Paragraph cardinalities == 1
    for p in doc.paragraphs:
        assert len(soup.select(p.locator["selector"])) == 1


def test_f3_heading_comments_and_duplicate_paragraphs(tmp_path: Path):
    """F3: Comments in heading do not contaminate proofs; duplicate siblings get distinct locators; h2 supported."""
    p_text = "The Committee decided to maintain the range at 4 to 5 percent."
    dup_html = (
        b'<!doctype html><html><body>\r\n'
        b'<div id="article">\r\n'
        b'<div class="heading col-xs-12 col-sm-8 col-md-8">\r\n'
        b'<!-- <p class="article__time">January 01, 1999</p> -->\r\n'
        b'<p class="article__time">September 16, 2026</p>\r\n'
        b'<h2 class="title">Federal Reserve issues FOMC statement</h2>\r\n'
        b'<!-- <p class="releaseTime">For release at 1:00 a.m. EST</p> -->\r\n'
        b'<p class="releaseTime">For release at 2:00 p.m. EDT</p>\r\n'
        b'</div>\r\n'
        b'<div class="col-xs-12 col-sm-8 col-md-8">\r\n'
        b'<p>' + p_text.encode("utf-8") + b'</p>\r\n'
        b'<p>' + p_text.encode("utf-8") + b'</p>\r\n'
        b'</div>\r\n'
        b'</div>\r\n'
        b'</body></html>'
    )
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "dup_f3", dup_html)
    manifest = json.loads(mpath.read_text())

    res = extract_fed_statement(dup_html, manifest)
    assert res.is_success
    doc = res.document
    assert doc is not None

    # Proofs must NOT match the commented dates or times
    assert "1999" not in doc.date_proof.raw_quote_text
    assert "1:00 a.m." not in (doc.release_time_proof.raw_quote_text if doc.release_time_proof else "")

    # h2 title tag must have matching locator
    assert doc.title_locator["selector"] == "#article > div.heading h2.title"
    soup = BeautifulSoup(dup_html.decode("utf-8"), "html.parser")
    assert len(soup.select(doc.title_locator["selector"])) == 1

    # Duplicate sibling paragraphs must have distinct selectors and distinct proof byte ranges
    p0 = doc.paragraphs[0]
    p1 = doc.paragraphs[1]
    assert p0.locator["selector"] != p1.locator["selector"]
    assert p0.proof.byte_start != p1.proof.byte_start
    assert len(soup.select(p0.locator["selector"])) == 1
    assert len(soup.select(p1.locator["selector"])) == 1


def test_f4_single_entity_decode(tmp_path: Path):
    """F4: HTML entities are decoded exactly once without double unescaping."""
    raw = _create_minimal_valid_html(
        paragraphs=["Literal &amp;#49; percent; literal &amp;lt;5."]
    )
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "f4_entities", raw)
    manifest = json.loads(mpath.read_text())

    res = extract_fed_statement(raw, manifest)
    assert res.is_success
    assert res.document is not None
    # Must NOT become "Literal 1 percent; literal <5."
    assert res.document.paragraphs[0].display_text == "Literal &#49; percent; literal &lt;5."


def test_f5_invalid_hour_minute_and_feed_mismatch(tmp_path: Path):
    """F5: Impossible timestamp 29:99 yields published_at=None; RSS mismatch records diagnostic."""
    raw = _create_minimal_valid_html(release_time="For release at 29:99 p.m. EDT")
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "f5_dates", raw)
    manifest = json.loads(mpath.read_text())
    manifest["parent_feed_reference"]["feed_published_at"] = "2026-10-16T18:00:00+00:00"

    res = extract_fed_statement(raw, manifest)
    assert res.is_success
    doc = res.document
    assert doc is not None

    # published_at is safely None due to invalid digits
    assert doc.published_at is None
    assert doc.published_date == "2026-10-15"

    # Date diagnostic recorded
    assert doc.date_diagnostics is not None
    assert doc.date_diagnostics["date_mismatch_detected"] is True
    assert doc.date_diagnostics["page_published_date"] == "2026-10-15"
    assert doc.date_diagnostics["feed_date"] == "2026-10-16"


def test_f5_same_instant_offset_boundary(tmp_path: Path):
    """F5: Page EDT 14:00 (18:00Z) vs Feed +09:00 03:00 (18:00Z) does NOT trigger date mismatch."""
    raw = _create_minimal_valid_html(
        date_text="September 16, 2026",
        release_time="For release at 2:00 p.m. EDT",
    )
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "same_instant", raw)
    manifest = json.loads(mpath.read_text())
    manifest["parent_feed_reference"]["feed_published_at"] = "2026-09-17T03:00:00+09:00"
    manifest["parent_feed_reference"]["feed_raw_pub_date"] = "Thu, 17 Sep 2026 03:00:00 +0900"

    res = extract_fed_statement(raw, manifest)
    assert res.is_success
    doc = res.document
    assert doc is not None
    assert doc.date_diagnostics is not None
    assert doc.date_diagnostics["date_mismatch_detected"] is False


def test_f6_output_destination_and_preview_safety(tmp_path: Path):
    """F6: Builder rejects publishing into fixture or manifest dir; CLI refuses symlink overwrite."""
    raw = _create_minimal_valid_html()
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "f6_safety", raw)
    fixture_dir = fpath.parent

    # 1. Output dir = fixture dir rejected
    with pytest.raises(StatementPublishError, match="Forbidden output_dir"):
        build_fed_statement_bundle(fpath, mpath, fixture_dir)

    # 2. Output dir inside fixture dir rejected
    with pytest.raises(StatementPublishError, match="Forbidden output_dir"):
        build_fed_statement_bundle(fpath, mpath, fixture_dir / "generated")

    # 3. Output dir via symlink to fixture dir rejected
    symlink_dir = tmp_path / "symlink_to_fixtures"
    symlink_dir.symlink_to(fixture_dir, target_is_directory=True)
    with pytest.raises(StatementPublishError, match="Forbidden output_dir"):
        build_fed_statement_bundle(fpath, mpath, symlink_dir)

    # 4. Preview publication refusing to overwrite raw input
    preview_text = "New Preview Content"
    with pytest.raises(StatementPublishError, match="Forbidden preview_path"):
        publish_statement_preview(preview_text, fpath, fpath, mpath)

    # 5. CLI refusing to overwrite raw input via symlink
    preview_symlink = tmp_path / "preview_alias.md"
    preview_symlink.symlink_to(fpath)
    raw_before = fpath.read_bytes()

    cmd = [
        sys.executable,
        "-m",
        "python.evaluation.fed_statement_extract",
        "--fixture-path",
        str(fpath),
        "--manifest-path",
        str(mpath),
        "--output-dir",
        str(tmp_path / "bundles"),
        "--preview-path",
        str(preview_symlink),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode != 0
    # Raw file must NOT be modified
    assert fpath.read_bytes() == raw_before


def test_f6_temp_symlink_preserves_input(tmp_path: Path):
    """F6: Pre-existing temporary symlink does not allow arbitrary file overwrite."""
    raw = _create_minimal_valid_html()
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "temp_symlink", raw)
    safe = tmp_path / "safe"
    safe.mkdir()

    # Pre-create a temporary symlink pointing to raw input
    final = safe / "fresh_preview.md"
    preview_text = "Test Preview Content"
    temp = final.with_name(f".tmp_prev_{os.getpid()}_{hashlib.sha256(preview_text.encode()).hexdigest()[:8]}.md")
    temp.symlink_to(fpath)

    before = fpath.read_bytes()
    with pytest.raises(StatementPublishError, match="already exists"):
        publish_statement_preview(preview_text, final, fpath, mpath)
    after = fpath.read_bytes()
    assert before == after


# ==============================================================================
# R3.1.3 Contract Boundaries (F1, F2, F3)
# ==============================================================================

def test_f1_mandatory_issuer_no_invention():
    """F1: Missing, null, or unsupported issuer must be rejected honestly."""
    raw = _create_minimal_valid_html()
    base_man = {
        "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
        "final_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
        "bytes_stored": len(raw),
        "blob_sha256": hashlib.sha256(raw).hexdigest(),
        "transport_outcome": "success",
        "http_status": 200,
        "research_provenance": "synthetic_fixture",
        "parent_feed_reference": {"fixture_kind": "synthetic_fixture"},
    }

    # Missing issuer
    res_missing = extract_fed_statement(raw, base_man)
    assert not res_missing.is_success
    assert res_missing.rejections[0].status == "invalid_manifest"

    # Explicit null issuer
    man_null = {**base_man, "issuer": None}
    res_null = extract_fed_statement(raw, man_null)
    assert not res_null.is_success
    assert res_null.rejections[0].status == "invalid_manifest"

    # Unsupported issuer
    man_bad = {**base_man, "issuer": "Random Organization"}
    res_bad = extract_fed_statement(raw, man_bad)
    assert not res_bad.is_success
    assert res_bad.rejections[0].status == "invalid_manifest"

    # Supported issuer preserved
    man_ok = {**base_man, "issuer": "Federal Reserve Board"}
    res_ok = extract_fed_statement(raw, man_ok)
    assert res_ok.is_success
    assert res_ok.document.issuer == "Federal Reserve Board"


def test_f1_typed_parent_lineage_validation():
    """F1: Real research manifests require typed parent lineage; non-typed parent rejected."""
    raw = _create_minimal_valid_html()
    real_man = {
        "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
        "final_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
        "bytes_stored": len(raw),
        "blob_sha256": hashlib.sha256(raw).hexdigest(),
        "transport_outcome": "success",
        "http_status": 200,
        "research_provenance": "research_fixture",
        "issuer": "Federal Reserve Board",
    }

    # Missing parent_feed_reference on real manifest
    res_missing = extract_fed_statement(raw, real_man)
    assert not res_missing.is_success
    assert res_missing.rejections[0].status == "invalid_manifest"

    # Non-typed dummy parent reference
    man_dummy = {**real_man, "parent_feed_reference": {"label": "not a feed reference"}}
    res_dummy = extract_fed_statement(raw, man_dummy)
    assert not res_dummy.is_success
    assert res_dummy.rejections[0].status == "invalid_manifest"

    # Complete typed parent reference accepted
    man_typed = {
        **real_man,
        "parent_feed_reference": {
            "archive_name": "live-r2-revision3",
            "feed_blob_sha256": "69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1",
            "feed_fact_id": "fact-b7cdc057cc6ec28d",
            "feed_item_locator": {"path": "/rss/channel/item[3]", "type": "xml_xpath"},
            "parent_bundle_digest": "cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde",
        },
    }
    res_typed = extract_fed_statement(raw, man_typed)
    assert res_typed.is_success
    assert res_typed.document.parent_feed_reference["feed_fact_id"] == "fact-b7cdc057cc6ec28d"


def test_f1_final_url_path_profile_matching():
    """F1: final_url path must match official statement endpoint prefix."""
    raw = _create_minimal_valid_html()
    man = {
        "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
        "final_url": "https://www.federalreserve.gov/aboutthefed.htm",
        "bytes_stored": len(raw),
        "blob_sha256": hashlib.sha256(raw).hexdigest(),
        "transport_outcome": "success",
        "http_status": 200,
        "research_provenance": "synthetic_fixture",
        "issuer": "Federal Reserve Board",
        "parent_feed_reference": {"fixture_kind": "synthetic_fixture"},
    }
    res = extract_fed_statement(raw, man)
    assert not res.is_success
    assert res.rejections[0].status == "unsupported_source_url"


def test_f1_synthetic_metadata_honesty(tmp_path: Path):
    """F1: Synthetic bundles must not claim bounded research fixture acquisition."""
    raw = _create_minimal_valid_html()
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "synth_honesty", raw)
    out_dir = tmp_path / "out"
    path, bundle = build_fed_statement_bundle(fpath, mpath, out_dir)

    payload = bundle["statement_bundle"]
    assert payload["research_metadata"]["provenance_class"] == "synthetic_fixture"
    assert payload["research_metadata"]["acquisition_verification_status"] == "NOT_APPLICABLE_SYNTHETIC"
    assert "bounded research fixture acquisition" not in payload["research_metadata"]["notes"]

    preview = generate_statement_preview(bundle)
    assert "- **Provenance Class:** `synthetic_fixture`" in preview
    assert "- **Acquisition Verification Status:** `NOT_APPLICABLE_SYNTHETIC`" in preview


def test_f2_taskforce_body_rejected_under_statement_skeleton(tmp_path: Path):
    """F2: Task force on bank supervision body under statement skeleton rejected."""
    raw = _create_minimal_valid_html(
        paragraphs=["The Federal Reserve Board announced the formation of a task force on bank supervision, chaired by the Vice Chair for Supervision."]
    )
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "taskforce_body", raw)
    res = extract_fed_statement(raw, json.loads(mpath.read_text()))
    assert not res.is_success
    assert res.rejections[0].status == "unsupported_document_type"


def test_f3_comment_closing_p_preserves_full_statement(tmp_path: Path):
    """F3: Inline comment with closing tag decoy does not truncate statement text."""
    p_html = "<p>The Committee decided <!-- </p> --> to maintain the range at 4 to 5 percent.</p>"
    raw = (
        b"<!doctype html><html><body>\r\n"
        b"<div id=\"article\">\r\n"
        b"<div class=\"heading col-xs-12 col-sm-8 col-md-8\">\r\n"
        b"<p class=\"article__time\">September 16, 2026</p>\r\n"
        b"<h3 class=\"title\">Federal Reserve issues FOMC statement</h3>\r\n"
        b"<p class=\"releaseTime\">For release at 2:00 p.m. EDT</p>\r\n"
        b"</div>\r\n"
        b"<div class=\"col-xs-12 col-sm-8 col-md-8\">\r\n"
        + p_html.encode("utf-8") +
        b"</div>\r\n"
        b"</div>\r\n"
        b"</body></html>"
    )
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "comment_p", raw)
    res = extract_fed_statement(raw, json.loads(mpath.read_text()))
    assert res.is_success
    doc = res.document
    assert len(doc.paragraphs) == 1
    assert doc.paragraphs[0].display_text == "The Committee decided to maintain the range at 4 to 5 percent."
    assert "4 to 5 percent" in doc.paragraphs[0].proof.raw_quote_text
    assert doc.paragraphs[0].proof.verify_against_blob(raw)


def test_f3_comment_closing_div_preserves_container_containment(tmp_path: Path):
    """F3: Inline comment with closing div decoy does not truncate container or break containment."""
    body_html = (
        "<!-- </div> --><p>The Committee decided to maintain the range at 4 to 5 percent.</p>"
    )
    raw = (
        b"<!doctype html><html><body>\r\n"
        b"<div id=\"article\">\r\n"
        b"<div class=\"heading col-xs-12 col-sm-8 col-md-8\">\r\n"
        b"<p class=\"article__time\">September 16, 2026</p>\r\n"
        b"<h3 class=\"title\">Federal Reserve issues FOMC statement</h3>\r\n"
        b"<p class=\"releaseTime\">For release at 2:00 p.m. EDT</p>\r\n"
        b"</div>\r\n"
        b"<div class=\"col-xs-12 col-sm-8 col-md-8\">\r\n"
        + body_html.encode("utf-8") +
        b"</div>\r\n"
        b"</div>\r\n"
        b"</body></html>"
    )
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "comment_div", raw)
    res = extract_fed_statement(raw, json.loads(mpath.read_text()))
    assert res.is_success
    doc = res.document
    assert len(doc.paragraphs) == 1
    assert doc.container_proof.byte_start <= doc.paragraphs[0].proof.byte_start
    assert doc.paragraphs[0].proof.byte_end <= doc.container_proof.byte_end
