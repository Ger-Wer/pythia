"""Pythia R1.2: Offline unit tests for source registry and bounded source probe."""

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import multiprocessing as mp
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from python.evaluation.source_probe import (
    ALLOWED_DOMAINS,
    CANONICAL_SOURCES,
    PROBE_VERSION,
    STREAM_LIMIT_BYTES,
    NullCookieJar,
    SourceProbe,
    create_anonymous_session,
    validate_registry,
)


@pytest.fixture
def probe():
    p = SourceProbe()
    yield p
    p.close()


def test_registry_has_exact_eight_sources():
    """Verify registry has exactly eight canonical sources matching CANONICAL_SOURCES."""
    registry_file = Path(__file__).parents[1] / "source_registry.json"
    assert registry_file.is_file(), "source_registry.json must exist"

    with open(registry_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data.get("version") == "1.0.0"
    assert data.get("usable_for_forecast_all") is False

    sources = data.get("sources", [])
    validated = validate_registry(sources)
    assert len(validated) == 8

    found_ids = {s["source_id"] for s in sources}
    assert found_ids == set(CANONICAL_SOURCES.keys())

    for s in sources:
        assert s["url"] == CANONICAL_SOURCES[s["source_id"]]
        assert s["source_id"] in ALLOWED_DOMAINS
        assert s["usable_for_forecast"] is False
        assert s["family"] in ("macro", "ai")
        assert s["role"] in ("mandatory", "experimental")
        assert s["primacy_level"] in ("primary", "secondary")
        assert "freshness_definition" in s
        assert "proposed_cadence" in s
        assert s["sla_policy"]["status"] == "proposed_policy_only"
        assert s["sla_policy"]["measured_freshness"] == "not_measured"


def test_validate_registry_rejects_incomplete_or_altered():
    """validate_registry raises ValueError on missing, duplicate, extra, or altered sources."""
    # Incomplete (1 source)
    with pytest.raises(ValueError, match="canonical sources"):
        validate_registry([{"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}])

    # Altered URL
    bad_url = [{"source_id": sid, "url": "https://evil.com"} for sid in CANONICAL_SOURCES]
    with pytest.raises(ValueError, match="URL mismatch"):
        validate_registry(bad_url)

    # Duplicate source_id
    duplicate = [{"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}] * 8
    with pytest.raises(ValueError, match="Duplicate source_id"):
        validate_registry(duplicate)


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


@pytest.mark.parametrize("field,value,case_name", [
    ("type", "NOT_EFFR", "wrong_series"),
    ("effectiveDate", "2026-02-30", "invalid_calendar_date"),
    ("effectiveDate", "2026-13-01", "invalid_month"),
    ("percentRate", True, "boolean_rate_true"),
    ("percentRate", False, "boolean_rate_false"),
    ("percentRate", float("nan"), "nan_rate"),
    ("percentRate", float("inf"), "infinite_rate"),
    ("percentRate", "4.83", "string_rate"),
])
def test_nyfed_effr_rejects_invalid_types_and_dates(probe, field, value, case_name):
    """NY Fed EFFR rejects non-EFFR, bad calendar dates, bools, NaN, and Inf."""
    good_item = {"type": "EFFR", "effectiveDate": "2026-10-16", "percentRate": 3.88}
    item = dict(good_item)
    item[field] = value
    body = json.dumps({"refRates": [item]}).encode("utf-8")

    status, level, reason, count = probe.validate_content(
        source_id="nyfed_effr",
        http_status=200,
        content_type="application/json",
        body=body,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content", f"Case {case_name} should be invalid_content, got {status}"
    assert level == "content"
    assert count is None


def test_fed_monetary_rss_valid(probe):
    """Fed monetary feed with valid channel title and entry links returns valid."""
    rss_xml = b"""<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0">
      <channel>
        <title>FRB: Press Release - Monetary Policy</title>
        <link>https://www.federalreserve.gov/feeds/feeds.htm</link>
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


def test_fed_speeches_rss_valid(probe):
    """Fed speeches feed with valid channel title and entry links returns valid."""
    rss_xml = b"""<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0">
      <channel>
        <title>FRB: Speeches</title>
        <link>https://www.federalreserve.gov/feeds/feeds.htm</link>
        <item>
          <title>Governor Remarks</title>
          <link>https://www.federalreserve.gov/newsevents/speech/bowman20261014a.htm</link>
          <pubDate>Wed, 14 Oct 2026 10:00:00 EST</pubDate>
        </item>
      </channel>
    </rss>"""

    status, level, reason, count = probe.validate_content(
        source_id="fed_speeches_rss",
        http_status=200,
        content_type="application/rss+xml",
        body=rss_xml,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "valid"
    assert level == "content"
    assert count == 1


@pytest.mark.parametrize("source_id", ["fed_monetary_rss", "fed_speeches_rss", "bls_cpi_rss"])
def test_rss_rejects_unrelated_sports_feed(probe, source_id):
    """Unrelated feed (Sports scores from example.org) is rejected by all RSS validators."""
    wrong_rss = b"""<?xml version="1.0"?><rss version="2.0"><channel>
    <title>Sports scores</title><link>https://example.org/sports</link>
    <item><title>Football score</title><link>https://example.org/sports/1</link></item>
    </channel></rss>"""

    status, level, reason, count = probe.validate_content(
        source_id=source_id,
        http_status=200,
        content_type="application/rss+xml",
        body=wrong_rss,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "content"
    assert "identity mismatch" in reason


def test_rss_rejects_cross_substitution(probe):
    """Speeches feed passed to monetary validator or vice versa is rejected."""
    speeches_rss = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
    <title>FRB: Speeches</title><link>https://www.federalreserve.gov/feeds/feeds.htm</link>
    <item><title>Speech</title><link>https://www.federalreserve.gov/newsevents/speech/1.htm</link></item>
    </channel></rss>"""

    status, level, reason, count = probe.validate_content(
        source_id="fed_monetary_rss",
        http_status=200,
        content_type="application/rss+xml",
        body=speeches_rss,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "content"
    assert "identity mismatch" in reason


def test_github_releases_valid_and_empty(probe):
    """GitHub releases checks valid releases, empty array, wrong repo, and non-string tags."""
    valid_releases = json.dumps([
        {
            "tag_name": "v3.0.0",
            "html_url": "https://github.com/deepseek-ai/DeepSeek-V3/releases/tag/v3.0.0",
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

    # Empty array is valid with item_count=0
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

    # Wrong repo (DeepSeek-V3-other)
    wrong_repo = json.dumps([
        {"tag_name": "v1", "html_url": "https://github.com/deepseek-ai/DeepSeek-V3-other/releases/tag/v1"}
    ]).encode("utf-8")
    status, level, reason, count = probe.validate_content(
        source_id="github_deepseek_v3_releases",
        http_status=200,
        content_type="application/json",
        body=wrong_repo,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "content"
    assert "repo/path mismatch" in reason

    # Non-string tag_name
    non_str_tag = json.dumps([
        {"tag_name": ["v1"], "html_url": "https://github.com/deepseek-ai/DeepSeek-V3/releases/tag/v1"}
    ]).encode("utf-8")
    status, level, reason, count = probe.validate_content(
        source_id="github_deepseek_v3_releases",
        http_status=200,
        content_type="application/json",
        body=non_str_tag,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "content"


def test_hf_models_valid_and_empty(probe):
    """Hugging Face models checks namespace filtering, empty model name, and empty array."""
    valid_models = json.dumps([
        {"id": "deepseek-ai/DeepSeek-V3", "author": "deepseek-ai"}
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

    # Empty model name (just "deepseek-ai/")
    empty_name = b'[{"id":"deepseek-ai/"}]'
    status, level, reason, count = probe.validate_content(
        source_id="hf_deepseek_models",
        http_status=200,
        content_type="application/json",
        body=empty_name,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "content"

    # Contradicting author
    wrong_author = json.dumps([{"id": "deepseek-ai/DeepSeek-V3", "author": "openai"}]).encode("utf-8")
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


def test_partial_stream_error_clears_sha256():
    """When a stream read error occurs after receiving prefix bytes, body_sha256 must be None."""
    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.is_redirect = False
            self.headers = {"Content-Type": "application/json"}
            self.closed = False
        def iter_content(self, chunk_size=8192):
            yield b"prefix"
            raise requests.ConnectionError("stream interrupted")
        def close(self):
            self.closed = True

    class FakeSession:
        def get(self, url, **kwargs):
            return FakeResponse()

    probe = SourceProbe(session=FakeSession())
    cfg = {"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}
    res = probe.probe_single(cfg)

    assert res["status"] == "network_error"
    assert res["body_sha256"] is None
    assert "stream interrupted" in res["reason"]


def test_redirect_response_is_closed():
    """Redirect response must be explicitly closed to avoid resource leaks."""
    class FakeRedirectResponse:
        def __init__(self):
            self.status_code = 302
            self.is_redirect = True
            self.headers = {"Location": CANONICAL_SOURCES["nyfed_effr"] + "?step=1"}
            self.closed = False
        def close(self):
            self.closed = True

    class FakeFinalResponse:
        def __init__(self):
            self.status_code = 200
            self.is_redirect = False
            self.headers = {"Content-Type": "application/json"}
            self.closed = False
        def iter_content(self, chunk_size=8192):
            yield json.dumps({
                "refRates": [{"type": "EFFR", "effectiveDate": "2026-10-16", "percentRate": 3.88}]
            }).encode()
        def close(self):
            self.closed = True

    redirect_resp = FakeRedirectResponse()
    final_resp = FakeFinalResponse()

    class FakeSession:
        def __init__(self):
            self.responses = iter([redirect_resp, final_resp])
        def get(self, url, **kwargs):
            return next(self.responses)

    probe = SourceProbe(session=FakeSession())
    cfg = {"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}
    res = probe.probe_single(cfg)

    assert res["status"] == "valid"
    assert redirect_resp.closed is True
    assert final_resp.closed is True


def test_anonymous_session_excludes_netrc_and_cookies():
    """Anonymous session does not attach NETRC Authorization or retain cookies."""
    with tempfile.TemporaryDirectory() as td:
        canary_netrc = Path(td) / "canary.netrc"
        canary_netrc.write_text("machine markets.newyorkfed.org login testuser password testpass\n")
        with patch.dict(os.environ, {"NETRC": str(canary_netrc)}):
            probe = SourceProbe()
            req = requests.Request("GET", CANONICAL_SOURCES["nyfed_effr"])
            prep = probe.session.prepare_request(req)
            assert "Authorization" not in prep.headers

    sess = create_anonymous_session()
    cookie = requests.cookies.create_cookie(name="test_cookie", value="test_val")
    sess.cookies.set_cookie(cookie)
    req = requests.Request("GET", CANONICAL_SOURCES["nyfed_effr"])
    prep = sess.prepare_request(req)
    assert "Cookie" not in prep.headers


def test_simulated_deadline_exceeded_returns_error():
    """When elapsed time exceeds deadline, probe returns network_error and sha256 is None."""
    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.is_redirect = False
            self.headers = {"Content-Type": "application/json"}
        def iter_content(self, chunk_size=8192):
            yield json.dumps({
                "refRates": [{"type": "EFFR", "effectiveDate": "2026-10-16", "percentRate": 3.88}]
            }).encode()
        def close(self):
            pass

    class FakeSession:
        def get(self, url, **kwargs):
            return FakeResponse()

    probe = SourceProbe(session=FakeSession())
    cfg = {"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}

    with patch("python.evaluation.source_probe.time.perf_counter", side_effect=[0.0, 20.0]):
        res = probe.probe_single(cfg)
        assert res["status"] != "valid"
        assert res["status"] == "network_error"
        assert res["body_sha256"] is None
        assert "deadline exceeded" in res["reason"].lower()


def test_incomplete_registry_aborts_without_reporting_healthy():
    """An incomplete registry file reports INVALID_REGISTRY and all_healthy=False."""
    with tempfile.TemporaryDirectory() as td:
        incomplete_file = Path(td) / "incomplete.json"
        incomplete_file.write_text(json.dumps({
            "sources": [{"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}]
        }))
        probe = SourceProbe(registry_path=incomplete_file)
        res = probe.run()

        assert res["all_healthy"] is False
        assert res["summary_status"] == "INVALID_REGISTRY"
        assert res["total_sources"] == 0
        assert "Registry must contain exactly 8 canonical sources" in res["registry_error"]


def test_partial_batch_failure_and_overall_summary():
    """Probe handles single source failure gracefully and reports partial reachability."""
    mock_session = MagicMock()

    def mock_get(url, **kwargs):
        resp = MagicMock()
        resp.is_redirect = False
        if "effr" in url:
            resp.status_code = 200
            resp.headers = {"Content-Type": "application/json"}
            resp.iter_content.return_value = [
                json.dumps({"refRates": [{"type": "EFFR", "effectiveDate": "2026-10-16", "percentRate": 4.83}]}).encode()
            ]
            return resp
        elif "cpi.rss" in url:
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

    # Verify that failed sources have specific network_error and not unhandled NameError
    failing = next(r for r in summary["results"] if r["source_id"] == "fed_monetary_rss")
    assert failing["status"] == "network_error"
    assert "Connection timed out" in failing["reason"]
    assert "NameError" not in failing["reason"]


def test_rss_rejects_truncated_xml(probe):
    """RSS XML with truncated closing tags is rejected as invalid_content at envelope level."""
    valid_feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        '<title>Federal Reserve Press Releases - Monetary Policy</title>'
        '<link>https://www.federalreserve.gov/</link>'
        '<item><title>FOMC statement</title>'
        '<link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20261016a.htm</link>'
        '</item></channel></rss>'
    ).encode()
    truncated = valid_feed.removesuffix(b"</channel></rss>")
    status, level, reason, count = probe.validate_content(
        source_id="fed_monetary_rss",
        http_status=200,
        content_type="application/rss+xml",
        body=truncated,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "envelope"
    assert "Malformed or truncated XML" in reason
    assert count is None


def test_rss_rejects_fed_banking_orders_for_monetary(probe):
    """Fed Banking Applications / orders feed is rejected for fed_monetary_rss."""
    orders_feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        '<title>FRB: Press Release - Banking Applications</title>'
        '<link>https://www.federalreserve.gov/</link>'
        '<item><title>Banking order</title>'
        '<link>https://www.federalreserve.gov/newsevents/pressreleases/orders20261016a.htm</link>'
        '</item></channel></rss>'
    ).encode()
    status, level, reason, count = probe.validate_content(
        source_id="fed_monetary_rss",
        http_status=200,
        content_type="application/rss+xml",
        body=orders_feed,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "content"
    assert "identity mismatch" in reason.lower()


def test_rss_rejects_bls_employment_for_cpi(probe):
    """BLS Employment Situation feed is rejected for bls_cpi_rss."""
    empsit_feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        '<title>Bureau of Labor Statistics</title>'
        '<link>https://www.bls.gov/</link>'
        '<item><title>Employment Situation</title>'
        '<link>https://www.bls.gov/news.release/empsit.nr0.htm</link>'
        '</item></channel></rss>'
    ).encode()
    status, level, reason, count = probe.validate_content(
        source_id="bls_cpi_rss",
        http_status=200,
        content_type="application/rss+xml",
        body=empsit_feed,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "content"
    assert "identity mismatch" in reason.lower()


def test_rss_rejects_mixed_category_entries(probe):
    """Feed containing a valid monetary item mixed with a non-monetary order item is rejected."""
    mixed_feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        '<title>Federal Reserve Press Releases - Monetary Policy</title>'
        '<link>https://www.federalreserve.gov/</link>'
        '<item><title>FOMC statement</title>'
        '<link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20261016a.htm</link></item>'
        '<item><title>Banking order</title>'
        '<link>https://www.federalreserve.gov/newsevents/pressreleases/orders20261016a.htm</link></item>'
        '</channel></rss>'
    ).encode()
    status, level, reason, count = probe.validate_content(
        source_id="fed_monetary_rss",
        http_status=200,
        content_type="application/rss+xml",
        body=mixed_feed,
        limit_exceeded=False,
        error_msg=None,
    )
    assert status == "invalid_content"
    assert level == "content"
    assert "validation failed" in reason.lower()
    assert count == 1


def test_real_requests_loopback_drip_interrupted_by_deadline():
    """Real slow loopback HTTP server dripping bytes is interrupted by worker process deadline."""
    body_data = b"x" * 120
    request_received = mp.Value("b", False)
    bytes_sent = mp.Value("i", 0)

    class DripHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            request_received.value = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body_data)))
            self.end_headers()
            try:
                for b in body_data:
                    self.wfile.write(bytes([b]))
                    self.wfile.flush()
                    bytes_sent.value += 1
                    time.sleep(0.025)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DripHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class LoopbackSession(requests.Session):
        def __init__(self):
            super().__init__()
            self.trust_env = False

        def get(self, url, **kwargs):
            return super().get(f"http://127.0.0.1:{server.server_address[1]}/drip", **kwargs)

    budget = 0.25
    sess = LoopbackSession()
    probe = SourceProbe(timeout=budget, session=sess)
    cfg = {"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}

    t0 = time.monotonic()
    try:
        res = probe.probe_single(cfg)
        elapsed = time.monotonic() - t0

        assert res["status"] == "network_error"
        assert res["validation_level"] == "transport"
        assert res["body_sha256"] is None
        assert res["bytes_received"] is None
        assert res["bytes_stored"] is None
        assert "deadline exceeded" in res["reason"].lower()

        # Elapsed must be strictly within budget + 0.35s (and well under the 3.0s total stream)
        assert elapsed <= budget + 0.35, f"Elapsed {elapsed:.4f}s exceeded budget ({budget}s + 0.35s)"
        assert bool(request_received.value) is True, "Test server did not receive GET request"
        assert bytes_sent.value > 0, "No bytes were sent before interruption"
        assert bytes_sent.value < len(body_data), "Entire stream was sent without being interrupted"
    finally:
        sess.close()
        probe.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)


def test_blocking_resolver_interrupted_by_deadline():
    """Blocking DNS resolver inside worker process is interrupted promptly by parent deadline."""
    resolver_entered = mp.Value("b", False)

    def delayed_resolver(*args, **kwargs):
        resolver_entered.value = True
        time.sleep(1.0)
        raise socket.gaierror("synthetic resolver delay")

    budget = 0.20
    probe = SourceProbe(timeout=budget)
    cfg = {"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}

    t0 = time.monotonic()
    with patch("socket.getaddrinfo", delayed_resolver):
        res = probe.probe_single(cfg)
    elapsed = time.monotonic() - t0

    assert res["status"] == "network_error"
    assert res["validation_level"] == "transport"
    assert res["body_sha256"] is None
    assert res["bytes_received"] is None
    assert res["bytes_stored"] is None
    assert "deadline exceeded" in res["reason"].lower()

    assert elapsed <= budget + 0.35, f"Elapsed {elapsed:.4f}s exceeded budget ({budget}s + 0.35s)"
    assert bool(resolver_entered.value) is True, "Resolver was not entered"


def test_timeout_on_first_source_does_not_block_subsequent_source():
    """Timeout on first probed source terminates worker and leaves subsequent sources unaffected."""
    class StallingSession(requests.Session):
        def __init__(self):
            super().__init__()
            self.trust_env = False

        def get(self, url, **kwargs):
            if "last/5.json" in url:
                time.sleep(1.5)
                raise requests.ConnectionError("stalled")
            resp = requests.Response()
            resp.status_code = 200
            resp.headers["Content-Type"] = "application/json"
            resp._content = json.dumps([
                {"tag_name": "v3.0.0", "html_url": "https://github.com/deepseek-ai/DeepSeek-V3/releases/tag/v3.0.0"}
            ]).encode("utf-8")
            resp._content_consumed = True
            return resp

    sess = StallingSession()
    probe = SourceProbe(timeout=0.20, session=sess)

    # First probe times out
    cfg1 = {"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}
    res1 = probe.probe_single(cfg1)
    assert res1["status"] == "network_error"
    assert res1["body_sha256"] is None

    # Second probe succeeds normally without interference
    cfg2 = {"source_id": "github_deepseek_v3_releases", "url": CANONICAL_SOURCES["github_deepseek_v3_releases"]}
    res2 = probe.probe_single(cfg2)
    assert res2["status"] == "valid"
    assert res2["body_sha256"] is not None

    # Verify no lingering zombie child processes
    active_children = mp.active_children()
    assert len(active_children) == 0, f"Lingering child processes detected: {active_children}"


def test_real_oversized_stream_through_fetch_path():
    """A real fetch exceeding 2 MiB stream limit correctly truncates buffer and invalidates SHA-256."""
    total_chunks = 34  # 34 * 65536 = 2,228,224 bytes (> 2,097,152 bytes)

    class LargeStreamAdapter(requests.adapters.BaseAdapter):
        def send(self, request, **kwargs):
            resp = requests.Response()
            resp.status_code = 200
            resp.headers = {"Content-Type": "application/json"}
            resp.url = request.url

            class LargeRaw:
                def __init__(self):
                    self.chunks_sent = 0
                def stream(self, chunk_size, decode_content=True):
                    for _ in range(total_chunks):
                        self.chunks_sent += 1
                        yield b"x" * 65536
                def close(self):
                    pass
                def release_conn(self):
                    pass

            resp.raw = LargeRaw()
            return resp

        def close(self):
            pass

    sess = requests.Session()
    sess.trust_env = False
    sess.mount("https://", LargeStreamAdapter())

    probe = SourceProbe(session=sess)
    cfg = {"source_id": "nyfed_effr", "url": CANONICAL_SOURCES["nyfed_effr"]}
    res = probe.probe_single(cfg)

    assert res["status"] == "invalid_content"
    assert res["validation_level"] == "transport"
    assert res["body_sha256"] is None
    assert res["bytes_stored"] == STREAM_LIMIT_BYTES  # exactly 2 MiB
    assert res["bytes_received"] > STREAM_LIMIT_BYTES  # exceeded boundary
    assert "Payload exceeded maximum stream limit" in res["reason"]

