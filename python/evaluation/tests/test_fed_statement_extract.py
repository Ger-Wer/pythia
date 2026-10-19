"""Tests for Pythia R3.1 / R3.1.1: Offline Fed Statement Extractor and Publisher.

Enforces:
1. Real official fixtures: accurate extraction, paragraph ordering, absence of nav/footer, valid ProofSpans.
2. Synthetic variations: UTF-8 BOM, entities, inline tags, Unicode; corrupt SHA and bad encoding rejected honestly.
3. Negative / non-statement templates: discount rate minutes, projections, speeches, Cloudflare challenge, empty bodies rejected.
4. Dates: calendar-only yields published_at=None; timezone string parsed explicitly; invalid dates handled safely; separate times.
5. Idempotent publication replay, collision conflict rejection (no-clobber), versions preserved in empty/rejected bundles.
6. Zero network: socket connections strictly forbidden across tests and child CLI process.
7. F1: Mandatory typed manifest & provenance validation (empty manifest, non-fed domains, transport failures).
8. F2: Negative body remains negative (JS-only barrier inside valid #article skeleton).
9. F3: Structural locators unambiguous (cardinality 1); title proof matches parsed title and skips comments.
10. F4: HTML entities decoded exactly once (no double unescaping of literal escaped entities).
11. F5: Invalid hour/minute yields published_at=None; feed vs page date mismatch diagnostic.
12. F6: Output directory & preview cannot overwrite source inputs or mutate archives.
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

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "docs/research/2026-10-19/r3/increment1/fixtures"


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
        "research_provenance": "research_fixture",
        "parent_feed_reference": {
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
<div id="article">
    <div class="heading col-xs-12 col-sm-8 col-md-8">
        <p class="article__time">{date_text}</p>
        <h3 class="title">{title}</h3>
        <p class="releaseTime">{release_time}</p>
    </div>
    <div class="col-xs-12 col-sm-8 col-md-8">
        {p_html}
    </div>
</div>
</body>
</html>"""
    return leading_bytes + html_str.encode("utf-8")


def test_real_fixture_monetary20260916a_extraction():
    """Real September 16, 2026 FOMC statement extracted cleanly with 100% valid ProofSpans."""
    raw_path = FIXTURES_DIR / "raw_monetary20260916a.html"
    man_path = FIXTURES_DIR / "manifest_monetary20260916a.json"
    if not raw_path.is_file():
        pytest.skip("Fixture not found")

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
    if not raw_path.is_file():
        pytest.skip("Fixture not found")

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
    }
    result = extract_fed_statement(bad_bytes, manifest)
    assert not result.is_success
    assert result.rejections[0].status == "encoding_error"


def test_unsupported_document_types_rejected(tmp_path: Path):
    """Minutes, speeches, and projections are rejected as unsupported document types."""
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
    }
    result = extract_fed_statement(challenge_html, manifest)
    assert not result.is_success
    assert result.rejections[0].status == "blocked_or_challenge"


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


def test_bundle_publication_idempotence_and_collision_conflict(tmp_path: Path):
    """Atomic bundle publication: replay is idempotent; competing content raises conflict."""
    raw = _create_minimal_valid_html()
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "bundle_test", raw)
    out_dir = tmp_path / "bundles"

    path1, bundle1 = build_fed_statement_bundle(fpath, mpath, out_dir)
    assert path1.is_file()

    # Replay identical
    path2, bundle2 = build_fed_statement_bundle(fpath, mpath, out_dir)
    assert path1 == path2
    assert bundle1["bundle_digest"] == bundle2["bundle_digest"]

    # Competing file at same target path raises StatementPublishError
    path1.write_text("{\"competing\": true}", encoding="utf-8")
    with pytest.raises(StatementPublishError, match="Atomic publication conflict"):
        build_fed_statement_bundle(fpath, mpath, out_dir)


def test_rejected_bundle_publication_preserves_frozen_versions(tmp_path: Path):
    """Rejection outcomes still produce published bundles preserving schema/extractor versions."""
    raw = _create_minimal_valid_html(title="Minutes of the Federal Open Market Committee")
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "rejected_test", raw)
    out_dir = tmp_path / "bundles"

    path, bundle = build_fed_statement_bundle(fpath, mpath, out_dir)
    assert path.is_file()
    assert bundle["statement_bundle"]["manifest"]["extraction_summary"]["status"] == "rejected"
    assert bundle["statement_bundle"]["document"] is None
    assert len(bundle["statement_bundle"]["rejections"]) > 0


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
    env["PYTHONPATH"] = f"{guard_dir}{os.pathsep}{Path.cwd()}"

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

    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"CLI failed: {proc.stderr}"
    assert not child_marker.exists()
    assert preview_file.is_file()


# ==============================================================================
# R3.1.1 Boundary Regressions (F1 - F6)
# ==============================================================================

def test_f1_mandatory_manifest_validation(tmp_path: Path):
    """F1: Empty manifest, non-official URLs, or transport failure are rejected."""
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
    }
    res2 = extract_fed_statement(raw, m_bad_url)
    assert not res2.is_success
    assert res2.rejections[0].status == "unsupported_source_url"

    # Transport failure
    m_fail = {
        "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
        "final_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm",
        "blob_sha256": hashlib.sha256(raw).hexdigest(),
        "bytes_stored": len(raw),
        "transport_outcome": "http_error",
        "http_status": 403,
    }
    res3 = extract_fed_statement(raw, m_fail)
    assert not res3.is_success
    assert res3.rejections[0].status == "transport_failure"


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


def test_f3_unambiguous_structural_locators(tmp_path: Path):
    """F3: Container and paragraph locators must resolve unambiguously (cardinality 1)."""
    # Nested synthetic HTML
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


def test_f6_output_destination_and_preview_safety(tmp_path: Path):
    """F6: Builder rejects publishing into fixture dir; CLI refuses symlink overwrite."""
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
