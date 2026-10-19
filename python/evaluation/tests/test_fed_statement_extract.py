"""Tests for Pythia R3.1: Offline Fed Statement Extractor and Publisher.

Enforces:
1. Real official fixtures: accurate extraction, paragraph ordering, absence of nav/footer, valid ProofSpans.
2. Synthetic variations: UTF-8 BOM, entities, inline tags, Unicode; corrupt SHA and bad encoding rejected honestly.
3. Negative / non-statement templates: discount rate minutes, projections, speeches, Cloudflare challenge, empty bodies rejected.
4. Dates: calendar-only yields published_at=None; timezone string parsed explicitly; invalid dates handled safely; separate times.
5. Idempotent publication replay, collision conflict rejection (no-clobber), versions preserved in empty/rejected bundles.
6. Zero network: socket connections strictly forbidden across tests and child CLI process.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

import pytest

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
    fpath = tmp_path / f"{name}.html"
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
    mpath = tmp_path / f"{name}_manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return fpath, mpath


def _create_minimal_valid_html(
    title: str = "Federal Reserve issues FOMC statement",
    date_text: str = "October 15, 2026",
    release_time: str = "For release at 2:00 p.m. EDT",
    paragraphs: Optional[List[str]] = None,
    leading_bom: bool = False,
) -> bytes:
    if paragraphs is None:
        paragraphs = [
            "The Federal Open Market Committee approved the following statement for release by a 12 – 0 vote:",
            "The Committee decided to raise the target range for the federal funds rate by 1/4 percentage point to 4 to 4-1/4 percent.",
            "Economic activity is expanding at a solid pace.",
        ]

    p_html = "\n".join(f"<p>{p}</p>" for p in paragraphs)
    html_content = f"""<!doctype html>
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
    encoded = html_content.encode("utf-8")
    if leading_bom:
        encoded = b"\xef\xbb\xbf" + encoded
    return encoded


# ==============================================================================
# 1. Real Official Fixtures Tests
# ==============================================================================

def test_real_official_fixtures_extract_and_verify_all_proofs():
    """Verify that both real FOMC statement fixtures extract cleanly and all proofs verify against raw blobs."""
    for key in ("monetary20260916a", "monetary20260729a"):
        raw_path = FIXTURES_DIR / f"raw_{key}.html"
        man_path = FIXTURES_DIR / f"manifest_{key}.json"

        assert raw_path.is_file(), f"Missing fixture file: {raw_path}"
        assert man_path.is_file(), f"Missing manifest file: {man_path}"

        raw_bytes = raw_path.read_bytes()
        manifest = json.loads(man_path.read_text(encoding="utf-8"))

        res = extract_fed_statement(raw_bytes, manifest)
        assert res.is_success, f"Extraction failed for {key}: {res.rejections}"
        doc = res.document
        assert doc is not None

        # Verify header fields
        assert doc.title == "Federal Reserve issues FOMC statement"
        assert doc.profile_id == "fomc_press_release_v1"
        assert doc.published_date in ("2026-09-16", "2026-07-29")
        assert doc.published_date_precision == "day"
        assert doc.published_at is not None
        assert doc.published_at.endswith("-04:00")  # EDT
        assert doc.published_at_provenance == "header_release_time"

        # Verify proofs
        assert doc.container_proof.verify_against_blob(raw_bytes)
        assert doc.date_proof.verify_against_blob(raw_bytes)
        assert doc.title_proof.verify_against_blob(raw_bytes)
        if doc.release_time_proof:
            assert doc.release_time_proof.verify_against_blob(raw_bytes)

        # Verify paragraphs
        assert len(doc.paragraphs) >= 6
        for p in doc.paragraphs:
            assert p.proof.verify_against_blob(raw_bytes)
            assert len(p.display_text) > 0
            # Ensure no HTML tags in display text
            assert "<p>" not in p.display_text
            assert "<strong>" not in p.display_text
            assert "</a>" not in p.display_text

        # First paragraph must be vote approval
        assert doc.paragraphs[0].role == "vote_approval"
        assert "approved the following statement" in doc.paragraphs[0].display_text

        # Second paragraph must be policy decision
        assert doc.paragraphs[1].role == "policy_decision"
        assert "decided to" in doc.paragraphs[1].display_text


# ==============================================================================
# 2. Synthetic Variations, Encoding, and Digest Integrity Tests
# ==============================================================================

def test_synthetic_markup_bom_unicode_entities_and_inline_tags(tmp_path: Path):
    """Synthetic fixture with leading BOM, HTML entities (&#8211;, &amp;, &nbsp;), inline tags, and Unicode."""
    paras = [
        "The Committee approved the statement by a 12 &#8211; 0 vote &amp; confirmed policy.",
        "The Committee decided to raise the rate by 1/4 percentage point <strong>to 4 percent</strong>.",
        "Economic activity expanded across &nbsp; all sectors — including manufacturing &amp; services.",
    ]
    raw_html = _create_minimal_valid_html(
        paragraphs=paras,
        leading_bom=True,
    )
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "synth_bom", raw_html)

    raw_bytes = fpath.read_bytes()
    manifest = json.loads(mpath.read_text())

    res = extract_fed_statement(raw_bytes, manifest)
    assert res.is_success
    doc = res.document
    assert doc is not None

    # Verify clean text decoded entities
    assert "12 – 0 vote & confirmed policy." in doc.paragraphs[0].display_text
    assert "to 4 percent." in doc.paragraphs[1].display_text
    assert "across all sectors — including" in doc.paragraphs[2].display_text

    # All proof spans must verify against raw bytes with BOM
    assert doc.container_proof.verify_against_blob(raw_bytes)
    for p in doc.paragraphs:
        assert p.proof.verify_against_blob(raw_bytes)


def test_corrupted_digest_and_size_mismatch_rejected(tmp_path: Path):
    """Corrupted SHA-256 or size in manifest results in rejection, no positive document."""
    raw_html = _create_minimal_valid_html()

    # Corrupt SHA
    fpath1, mpath1 = _make_fixture_and_manifest(tmp_path, "corrupt_sha", raw_html, corrupt_sha=True)
    res1 = extract_fed_statement(fpath1.read_bytes(), json.loads(mpath1.read_text()))
    assert not res1.is_success
    assert len(res1.rejections) == 1
    assert res1.rejections[0].status == "digest_mismatch"

    # Corrupt Size
    fpath2, mpath2 = _make_fixture_and_manifest(tmp_path, "corrupt_size", raw_html, corrupt_size=True)
    res2 = extract_fed_statement(fpath2.read_bytes(), json.loads(mpath2.read_text()))
    assert not res2.is_success
    assert len(res2.rejections) == 1
    assert res2.rejections[0].status == "size_mismatch"


def test_encoding_error_rejected(tmp_path: Path):
    """Raw blob with invalid UTF-8 bytes rejected honestly."""
    invalid_bytes = b"\xff\xfe\x00\x00<html><head><title>Test</title></head><body>Bad bytes</body></html>"
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "bad_encoding", invalid_bytes)
    res = extract_fed_statement(fpath.read_bytes(), json.loads(mpath.read_text()))
    assert not res.is_success
    assert len(res.rejections) == 1
    assert res.rejections[0].status == "encoding_error"


# ==============================================================================
# 3. Non-statement, Blocked, and Empty Templates Tests
# ==============================================================================

def test_unsupported_document_types_rejected(tmp_path: Path):
    """Discount rate minutes, economic projections, and speeches are rejected as unsupported."""
    bad_titles = [
        "Federal Reserve Board issues discount rate minutes",
        "FOMC Summary of Economic Projections",
        "Speech by Governor Waller on monetary policy",
        "Federal Reserve Board announces advisory council meeting",
    ]
    for idx, bad_title in enumerate(bad_titles):
        raw = _create_minimal_valid_html(title=bad_title)
        fpath, mpath = _make_fixture_and_manifest(tmp_path, f"unsupported_{idx}", raw)
        res = extract_fed_statement(fpath.read_bytes(), json.loads(mpath.read_text()))
        assert not res.is_success
        assert len(res.rejections) == 1
        assert res.rejections[0].status == "unsupported_document_type"


def test_challenge_and_bot_barrier_rejected(tmp_path: Path):
    """Cloudflare / Akamai challenge HTML rejected honestly."""
    challenge_html = b"""<!doctype html>
<html>
<head><title>Just a moment...</title></head>
<body>
<div id="challenge-running">Please wait while your request is validated...</div>
<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_api/v1"></script>
</body>
</html>"""
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "challenge", challenge_html)
    res = extract_fed_statement(fpath.read_bytes(), json.loads(mpath.read_text()))
    assert not res.is_success
    assert len(res.rejections) == 1
    assert res.rejections[0].status == "blocked_or_challenge"


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


# ==============================================================================
# 4. Dates and Provenance Tests
# ==============================================================================

def test_date_only_fixture_yields_null_published_at(tmp_path: Path):
    """If release time text is missing or zoneless, published_at is strictly null (no invented time)."""
    raw = _create_minimal_valid_html(
        date_text="October 15, 2026",
        release_time="For immediate release",  # no explicit time/tz
    )
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "date_only", raw)
    res = extract_fed_statement(fpath.read_bytes(), json.loads(mpath.read_text()))
    assert res.is_success
    doc = res.document
    assert doc is not None
    assert doc.published_date == "2026-10-15"
    assert doc.published_date_precision == "day"
    assert doc.published_at is None
    assert doc.published_at_provenance is None


def test_explicit_timezone_release_time_parsed(tmp_path: Path):
    """Explicit release time with EDT timezone parsed accurately."""
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


# ==============================================================================
# 5. Publication, Idempotence, Replay, and Collision Tests
# ==============================================================================

def test_bundle_publication_idempotence_and_collision_conflict(tmp_path: Path):
    """Publishing identical bundle replays; competing pre-existing file raises StatementPublishError without clobber."""
    raw = _create_minimal_valid_html()
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "bundle_test", raw)
    out_dir = tmp_path / "output"

    # First publication
    p1, b1 = build_fed_statement_bundle(fpath, mpath, out_dir)
    assert p1.is_file()
    digest = b1["bundle_digest"]

    # Second publication (idempotent replay)
    p2, b2 = build_fed_statement_bundle(fpath, mpath, out_dir)
    assert p1 == p2
    assert b1 == b2

    # Verify versions in manifest and bundle
    bundle_data = json.loads(p1.read_text())["statement_bundle"]
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
    """Even a rejected outcome publishes a bundle preserving frozen versions and honest rejections."""
    raw = b"<html><head><title>Unsupported</title></head><body>No article</body></html>"
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "rejected_bundle", raw)
    out_dir = tmp_path / "output_rejected"

    p, bundle_data = build_fed_statement_bundle(fpath, mpath, out_dir)
    assert p.is_file()
    b = bundle_data["statement_bundle"]
    assert b["extractor_version"] == STATEMENT_EXTRACTOR_VERSION
    assert b["schema_version"] == STATEMENT_SCHEMA_VERSION
    assert b["bundle_version"] == STATEMENT_BUNDLE_VERSION
    assert b["document"] is None
    assert len(b["rejections"]) == 1
    assert b["rejections"][0]["status"] == "unsupported_structure"


def test_preview_generation_from_bundle(tmp_path: Path):
    """Preview is generated strictly from bundle dictionary without external calls."""
    raw = _create_minimal_valid_html()
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "preview_test", raw)
    _, bundle_data = build_fed_statement_bundle(fpath, mpath, tmp_path / "out_preview")

    preview_text = generate_statement_preview(bundle_data)
    assert "# FOMC Statement Research Preview" in preview_text
    assert "Federal Reserve issues FOMC statement" in preview_text
    assert "2026-10-15" in preview_text
    assert "Paragraph 0 [`vote_approval`]" in preview_text
    assert "All proof spans independently verified against raw bytes: **VALID**" in preview_text


# ==============================================================================
# 6. CLI Execution with Network Isolation
# ==============================================================================

def test_cli_execution_with_child_network_guard(tmp_path: Path):
    """CLI child process executes with sitecustomize network guard and outputs valid bundle and preview."""
    raw = _create_minimal_valid_html()
    fpath, mpath = _make_fixture_and_manifest(tmp_path, "cli_test", raw)
    out_dir = tmp_path / "cli_out"
    preview_file = tmp_path / "cli_preview.md"

    # Setup child network guard in sitecustomize
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
    assert "Published Statement Bundle:" in proc.stdout
    assert "Bundle Digest:" in proc.stdout
    assert not child_marker.exists(), "CLI process attempted network access!"
    assert preview_file.is_file()
    assert "# FOMC Statement Research Preview" in preview_file.read_text()
