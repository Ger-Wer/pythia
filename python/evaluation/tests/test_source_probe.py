"""Pythia R1: Offline unit tests for source registry and bounded source probe."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from python.evaluation.source_probe import (
    ALLOWED_DOMAINS,
    PROBE_VERSION,
    STREAM_LIMIT_BYTES,
    SourceProbe,
)


@pytest.fixture
def probe(tmp_path):
    return SourceProbe()


def test_registry_has_exact_eight_sources():
    """Verify registry has exactly eight sources with all required fields."""
    registry_file = Path(__file__).parents[1] / "source_registry.json"
    assert registry_file.is_file(), "source_registry.json must exist"

    with open(registry_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data.get("version") == "1.0.0"
    assert data.get("usable_for_forecast_all") is False

    sources = data.get("sources", [])
    assert len(sources) == 8, f"Expected exactly 8 sources, found {len(sources)}"

    expected_ids = {
        "nyfed_effr",
        "fed_monetary_rss",
        "fed_speeches_rss",
        "bls_cpi_rss",
        "atlanta_gdpnow",
        "github_deepseek_v3_releases",
        "hf_deepseek_models",
        "arena_text_overall",
    }
    found_ids = {s["source_id"] for s in sources}
    assert found_ids == expected_ids, f"Mismatch in source IDs: {found_ids ^ expected_ids}"

    for s in sources:
        assert s["source_id"] in ALLOWED_DOMAINS
        assert s["usable_for_forecast"] is False
        assert s["family"] in ("macro", "ai")
        assert s["role"] in ("mandatory", "experimental")
        assert s["primacy_level"] in ("primary", "secondary")
        assert "freshness_definition" in s
        assert "proposed_cadence" in s
        assert s["sla_policy"]["status"] == "proposed_policy_only"
        assert s["sla_policy"]["measured_freshness"] == "not_measured"


def test_nyfed_effr_valid(probe):
    """NY Fed EFFR valid refRates JSON returns status valid and correct item count."""
    body = json.dumps({
        "refRates": [
            {"type": "EFFR", "effectiveDate": "2026-10-16", "percentRate": 4.83},
            {"type": "EFFR", "effectiveDate": "2026-10-15", "percentRate": 4.83},
        ]
    }).encode("utf-8")

    status, level, reason, count = probe.validate_content(
        source_id="nyfed_effr",
        http_status=200,
        content_type="application/json",
        body=body,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "valid"
    assert level == "content"
    assert count == 2
    assert "Successfully validated 2" in reason


def test_nyfed_effr_invalid_structure(probe):
    """NY Fed EFFR with invalid structure (missing refRates or bad types) returns invalid_content."""
    bad_body = json.dumps({"otherKey": [1, 2, 3]}).encode("utf-8")
    status, level, reason, count = probe.validate_content(
        source_id="nyfed_effr",
        http_status=200,
        content_type="application/json",
        body=bad_body,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "content"
    assert count is None

    non_numeric = json.dumps({
        "refRates": [{"type": "EFFR", "effectiveDate": "2026-10-16", "percentRate": "bad"}]
    }).encode("utf-8")
    status, level, reason, count = probe.validate_content(
        source_id="nyfed_effr",
        http_status=200,
        content_type="application/json",
        body=non_numeric,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "content"


def test_fed_rss_valid(probe):
    """Fed monetary and speeches feeds with valid RSS XML return status valid."""
    rss_xml = b"""<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0">
      <channel>
        <title>Federal Reserve Press Releases - Monetary Policy</title>
        <link>https://www.federalreserve.gov</link>
        <item>
          <title>FOMC Statement</title>
          <link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20261016a.htm</link>
          <pubDate>Fri, 16 Oct 2026 14:00:00 EST</pubDate>
        </item>
      </channel>
    </rss>"""

    status, level, reason, count = probe.validate_content(
        source_id="fed_monetary_rss",
        http_status=200,
        content_type="application/rss+xml",
        body=rss_xml,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "valid"
    assert level == "content"
    assert count == 1


def test_rss_invalid_structure_or_empty(probe):
    """RSS with missing root or zero items returns invalid_content."""
    not_rss = b"<html><body>404 Not Found</body></html>"
    status, level, reason, count = probe.validate_content(
        source_id="fed_monetary_rss",
        http_status=200,
        content_type="text/html",
        body=not_rss,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "envelope"

    empty_rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>"""
    status, level, reason, count = probe.validate_content(
        source_id="fed_speeches_rss",
        http_status=200,
        content_type="application/rss+xml",
        body=empty_rss,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "content"
    assert count == 0


def test_github_releases_valid_and_empty(probe):
    """GitHub releases checks valid releases, empty array, and non-array errors."""
    valid_releases = json.dumps([
        {
            "tag_name": "v3.0.0",
            "html_url": "https://github.com/deepseek-ai/DeepSeek-V3/releases/tag/v3.0.0",
            "published_at": "2026-10-15T10:00:00Z",
        }
    ]).encode("utf-8")
    status, level, reason, count = probe.validate_content(
        source_id="github_deepseek_v3_releases",
        http_status=200,
        content_type="application/json",
        body=valid_releases,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "valid"
    assert level == "content"
    assert count == 1

    empty_releases = b"[]"
    status, level, reason, count = probe.validate_content(
        source_id="github_deepseek_v3_releases",
        http_status=200,
        content_type="application/json",
        body=empty_releases,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "valid"
    assert level == "content"
    assert count == 0

    rate_limited_dict = json.dumps({"message": "API rate limit exceeded"}).encode("utf-8")
    status, level, reason, count = probe.validate_content(
        source_id="github_deepseek_v3_releases",
        http_status=200,
        content_type="application/json",
        body=rate_limited_dict,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "blocked"
    assert level == "transport"

    other_repo = json.dumps([
        {"tag_name": "v1.0", "html_url": "https://github.com/other-org/other-repo/releases"}
    ]).encode("utf-8")
    status, level, reason, count = probe.validate_content(
        source_id="github_deepseek_v3_releases",
        http_status=200,
        content_type="application/json",
        body=other_repo,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "content"


def test_hf_models_valid_and_empty(probe):
    """Hugging Face models checks namespace filtering, empty array, and invalid payloads."""
    valid_models = json.dumps([
        {"id": "deepseek-ai/DeepSeek-V3", "lastModified": "2026-10-16T12:00:00Z"}
    ]).encode("utf-8")
    status, level, reason, count = probe.validate_content(
        source_id="hf_deepseek_models",
        http_status=200,
        content_type="application/json",
        body=valid_models,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "valid"
    assert level == "content"
    assert count == 1

    empty_models = b"[]"
    status, level, reason, count = probe.validate_content(
        source_id="hf_deepseek_models",
        http_status=200,
        content_type="application/json",
        body=empty_models,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "valid"
    assert count == 0

    wrong_author = json.dumps([{"id": "openai/gpt-5"}]).encode("utf-8")
    status, level, reason, count = probe.validate_content(
        source_id="hf_deepseek_models",
        http_status=200,
        content_type="application/json",
        body=wrong_author,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "content"


def test_html_endpoints_remain_unverified(probe):
    """GDPNow and Arena HTML return unverified rather than false valid."""
    html_doc = b"<!DOCTYPE html><html><head><title>GDPNow</title></head><body><h1>Forecast</h1></body></html>"
    status, level, reason, count = probe.validate_content(
        source_id="atlanta_gdpnow",
        http_status=200,
        content_type="text/html",
        body=html_doc,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "unverified"
    assert level == "envelope"
    assert count is None

    arena_html = b"<!DOCTYPE html><html><body>Leaderboard</body></html>"
    status, level, reason, count = probe.validate_content(
        source_id="arena_text_overall",
        http_status=200,
        content_type="text/html",
        body=arena_html,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "unverified"
    assert level == "envelope"
    assert count is None


def test_blocked_http_status_codes(probe):
    """HTTP 401, 403, 429 return status blocked at transport level."""
    for code in (401, 403, 429):
        status, level, reason, count = probe.validate_content(
            source_id="bls_cpi_rss",
            http_status=code,
            content_type="text/html",
            body=b"Access Denied",
            limit_exceeded=False,
            error_msg=None,
        )
        assert status == "blocked"
        assert level == "transport"
        assert str(code) in reason


def test_http_200_challenge_page_blocked_for_feed(probe):
    """BLS returning HTTP 200 with HTML challenge instead of RSS is blocked."""
    challenge_html = b"<html><head><title>Bot Verification</title></head><body>Solve Challenge</body></html>"
    status, level, reason, count = probe.validate_content(
        source_id="bls_cpi_rss",
        http_status=200,
        content_type="text/html",
        body=challenge_html,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "blocked"
    assert level == "envelope"


def test_stream_limit_exceeded(probe):
    """Stream exceeding 2 MiB sets sha256 to None and marks invalid_content."""
    status, level, reason, count = probe.validate_content(
        source_id="nyfed_effr",
        http_status=200,
        content_type="application/json",
        body=b"x" * 1000,
        limit_exceeded=True,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "transport"
    assert "Payload exceeded maximum stream limit" in reason


def test_redirect_outside_allowlist():
    """Redirect to an untrusted domain outside the allowlist is aborted."""
    mock_session = MagicMock()
    mock_resp1 = MagicMock()
    mock_resp1.is_redirect = True
    mock_resp1.status_code = 302
    mock_resp1.headers = {"Location": "https://malicious-domain.com/data.json"}
    mock_session.get.return_value = mock_resp1

    probe = SourceProbe(session=mock_session)
    source_cfg = {
        "source_id": "nyfed_effr",
        "url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json",
    }
    result = probe.probe_single(source_cfg)

    assert result["status"] == "network_error"
    assert result["validation_level"] == "transport"
    assert "not in allowlist" in result["reason"]
    assert result["body_sha256"] is None
    assert result["usable_for_forecast"] is False


def test_redirect_max_limit_exceeded():
    """More than 3 redirects results in error."""
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.is_redirect = True
    mock_resp.status_code = 302
    mock_resp.headers = {"Location": "https://markets.newyorkfed.org/step"}
    mock_session.get.return_value = mock_resp

    probe = SourceProbe(session=mock_session)
    source_cfg = {
        "source_id": "nyfed_effr",
        "url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json",
    }
    result = probe.probe_single(source_cfg)

    assert result["status"] == "network_error"
    assert result["validation_level"] == "transport"
    assert "Exceeded maximum redirect limit (3)" in result["reason"]


def test_published_at_is_strictly_null(probe):
    """R1 probe records published_at strictly as null (never fetch time or Last-Modified)."""
    source_cfg = {
        "source_id": "nyfed_effr",
        "url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json",
    }
    with patch.object(probe, "_fetch_bounded") as mock_fetch:
        mock_fetch.return_value = (
            200,
            "application/json",
            json.dumps({"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-16", "percentRate": 4.83}]}).encode(),
            False,
            source_cfg["url"],
            100.0,
            None,
        )
        res = probe.probe_single(source_cfg)
        assert res["published_at"] is None
        assert res["status"] == "valid"
        assert res["usable_for_forecast"] is False


def test_partial_batch_failure_and_overall_summary():
    """Probe handles single source failure gracefully and reports partial reachability."""
    mock_session = MagicMock()

    def mock_get(url, **kwargs):
        if "effr" in url:
            resp = MagicMock()
            resp.is_redirect = False
            resp.status_code = 200
            resp.headers = {"Content-Type": "application/json"}
            resp.iter_content.return_value = [
                json.dumps({"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-16", "percentRate": 4.83}]}).encode()
            ]
            return resp
        elif "cpi.rss" in url:
            resp = MagicMock()
            resp.is_redirect = False
            resp.status_code = 403
            resp.headers = {"Content-Type": "text/html"}
            resp.iter_content.return_value = [b"Forbidden"]
            return resp
        else:
            raise requests.ConnectionError("Connection timed out")

    mock_session.get.side_effect = mock_get

    probe = SourceProbe(session=mock_session)
    summary = probe.run()

    assert summary["total_sources"] == 8
    assert len(summary["results"]) == 8
    assert summary["all_healthy"] is False
    assert summary["summary_status"] == "PARTIAL_REACHABLE"
    assert summary["usable_for_forecast_all"] is False

    effr_res = next(r for r in summary["results"] if r["source_id"] == "nyfed_effr")
    assert effr_res["status"] == "valid"

    cpi_res = next(r for r in summary["results"] if r["source_id"] == "bls_cpi_rss")
    assert cpi_res["status"] == "blocked"

    # All sources retain usable_for_forecast = False
    for r in summary["results"]:
        assert r["usable_for_forecast"] is False
