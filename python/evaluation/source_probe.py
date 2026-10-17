"""Pythia R1.1: Bounded single-run probe for the 8 canonical evidence sources.

Runs sequential diagnostic HTTP/HTTPS requests against sources specified in
source_registry.json. Performs strict transport, envelope, and content validation
under controlled constraints (streaming 2 MiB limit, overall 15s deadline per source,
max 3 redirects within domain allowlists, anonymous transport with no ambient credentials
or persistent cookies).

Usage:
    python -m python.evaluation.source_probe --output <path-to-json-output>
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import logging
import math
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import feedparser
import requests

PROBE_VERSION = "pythia-probe-r1.1/1.0"
STREAM_LIMIT_BYTES = 2 * 1024 * 1024  # 2 MiB = 2,097,152 bytes
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_REDIRECTS = 3

DEFAULT_USER_AGENT = "Pythia-source-probe/1.1 (+https://github.com/Ger-Wer/pythia)"

# Canonical 8 source IDs and their exact official URLs
CANONICAL_SOURCES: Dict[str, str] = {
    "nyfed_effr": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json",
    "fed_monetary_rss": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "fed_speeches_rss": "https://www.federalreserve.gov/feeds/speeches.xml",
    "bls_cpi_rss": "https://www.bls.gov/feed/cpi.rss",
    "atlanta_gdpnow": "https://www.atlantafed.org/research-and-data/data/gdpnow",
    "github_deepseek_v3_releases": "https://api.github.com/repos/deepseek-ai/DeepSeek-V3/releases?per_page=2",
    "hf_deepseek_models": "https://huggingface.co/api/models?author=deepseek-ai&sort=lastModified&direction=-1&limit=3",
    "arena_text_overall": "https://arena.ai/leaderboard/text/overall-no-style-control",
}

ALLOWED_DOMAINS: Dict[str, Set[str]] = {
    "nyfed_effr": {"markets.newyorkfed.org"},
    "fed_monetary_rss": {"www.federalreserve.gov", "federalreserve.gov"},
    "fed_speeches_rss": {"www.federalreserve.gov", "federalreserve.gov"},
    "bls_cpi_rss": {"www.bls.gov", "bls.gov"},
    "atlanta_gdpnow": {"www.atlantafed.org", "atlantafed.org"},
    "github_deepseek_v3_releases": {"api.github.com"},
    "hf_deepseek_models": {"huggingface.co"},
    "arena_text_overall": {"arena.ai"},
}

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    """Return ISO 8601 UTC timestamp string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class NullCookieJar(requests.cookies.RequestsCookieJar):
    """CookieJar that completely rejects storing, updating, or sending any cookies."""

    def set_cookie(self, *args: Any, **kwargs: Any) -> None:
        pass

    def update_cookies(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __iter__(self) -> Any:
        return iter(())

    def __len__(self) -> int:
        return 0


def create_anonymous_session() -> requests.Session:
    """Create an isolated, anonymous requests Session without ambient credentials or cookies."""
    sess = requests.Session()
    # Explicitly disable ambient environment (.netrc, ambient proxy credentials)
    sess.trust_env = False
    # Explicitly block all incoming and outgoing cookies via null jar
    sess.cookies = NullCookieJar()
    # Explicitly ensure no ambient authentication header
    sess.auth = None
    # Preserve TLS verification
    sess.verify = True
    return sess


def validate_registry(sources: Any) -> List[Dict[str, Any]]:
    """Validate that the registry contains exactly the 8 canonical sources with correct URLs."""
    if not isinstance(sources, list):
        raise ValueError(f"Registry 'sources' must be a list, got {type(sources).__name__}")

    if len(sources) != len(CANONICAL_SOURCES):
        raise ValueError(
            f"Registry must contain exactly {len(CANONICAL_SOURCES)} canonical sources, found {len(sources)}"
        )

    seen_ids: Set[str] = set()
    for s in sources:
        if not isinstance(s, dict):
            raise ValueError(f"Registry source entry must be an object, got {type(s).__name__}")
        sid = s.get("source_id")
        if not sid or not isinstance(sid, str):
            raise ValueError(f"Registry entry missing valid 'source_id': {s}")
        if sid in seen_ids:
            raise ValueError(f"Duplicate source_id in registry: '{sid}'")
        if sid not in CANONICAL_SOURCES:
            raise ValueError(f"Disallowed or extraneous source_id in registry: '{sid}'")
        seen_ids.add(sid)

        url = s.get("url")
        canonical_url = CANONICAL_SOURCES[sid]
        if url != canonical_url:
            raise ValueError(
                f"URL mismatch for '{sid}': expected '{canonical_url}', got '{url}'"
            )

    if seen_ids != set(CANONICAL_SOURCES.keys()):
        missing = set(CANONICAL_SOURCES.keys()) - seen_ids
        raise ValueError(f"Registry is missing required canonical source IDs: {missing}")

    return sources


class SourceProbe:
    """Runs bounded diagnostic probes for configured evidence sources."""

    def __init__(
        self,
        registry_path: Optional[Path] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_redirects: int = MAX_REDIRECTS,
        max_bytes: int = STREAM_LIMIT_BYTES,
        user_agent: str = DEFAULT_USER_AGENT,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.registry_path = registry_path or Path(__file__).parent / "source_registry.json"
        self.timeout = float(timeout)
        self.max_redirects = max_redirects
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self._owns_session = session is None
        if session is not None:
            self.session = session
        else:
            self.session = create_anonymous_session()

    def close(self) -> None:
        """Close internal session if owned."""
        if self._owns_session and self.session:
            self.session.close()

    def __enter__(self) -> SourceProbe:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def load_registry(self) -> List[Dict[str, Any]]:
        """Load and return the list of source configurations from registry."""
        with open(self.registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        sources = data.get("sources", [])
        return validate_registry(sources)

    def _fetch_bounded(
        self,
        source_id: str,
        initial_url: str,
    ) -> Tuple[Optional[int], Optional[str], bytes, int, int, bool, str, float, Optional[str]]:
        """Fetch URL with strict redirect, domain allowlist, deadline, and stream size bounds.

        Returns:
            (status_code, content_type, body_bytes, bytes_received, bytes_stored, limit_exceeded, final_url, duration_ms, error_msg)
        """
        current_url = initial_url
        redirect_count = 0
        allowed_domains = ALLOWED_DOMAINS.get(source_id, set())

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
        }

        start_time = time.perf_counter()

        while True:
            # Check elapsed time before next hop
            elapsed = time.perf_counter() - start_time
            if elapsed > self.timeout:
                duration_ms = elapsed * 1000.0
                return (
                    None,
                    None,
                    b"",
                    0,
                    0,
                    False,
                    current_url,
                    duration_ms,
                    f"Overall source deadline exceeded ({self.timeout}s): elapsed {elapsed:.2f}s",
                )

            remaining_timeout = max(0.001, self.timeout - elapsed)

            parsed = urllib.parse.urlparse(current_url)
            if parsed.scheme.lower() != "https":
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return None, None, b"", 0, 0, False, current_url, duration_ms, f"Non-HTTPS scheme rejected: {parsed.scheme}"

            domain = parsed.netloc.lower()
            if domain not in allowed_domains:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return (
                    None,
                    None,
                    b"",
                    0,
                    0,
                    False,
                    current_url,
                    duration_ms,
                    f"Domain {domain} not in allowlist for source {source_id}",
                )

            try:
                resp = self.session.get(
                    current_url,
                    headers=headers,
                    stream=True,
                    allow_redirects=False,
                    timeout=remaining_timeout,
                )
            except requests.RequestException as exc:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return None, None, b"", 0, 0, False, current_url, duration_ms, f"Network transport error: {type(exc).__name__}: {str(exc)}"

            # Handle redirects manually to enforce max_redirects and domain constraints
            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                redirect_count += 1
                location = resp.headers.get("Location")
                status_code = resp.status_code
                content_type = resp.headers.get("Content-Type")
                # ALWAYS close redirect response immediately to prevent descriptor leaks!
                resp.close()

                if redirect_count > self.max_redirects:
                    duration_ms = (time.perf_counter() - start_time) * 1000.0
                    return (
                        status_code,
                        content_type,
                        b"",
                        0,
                        0,
                        False,
                        current_url,
                        duration_ms,
                        f"Exceeded maximum redirect limit ({self.max_redirects})",
                    )
                if not location:
                    duration_ms = (time.perf_counter() - start_time) * 1000.0
                    return (
                        status_code,
                        content_type,
                        b"",
                        0,
                        0,
                        False,
                        current_url,
                        duration_ms,
                        "Redirect response missing Location header",
                    )
                current_url = urllib.parse.urljoin(current_url, location)
                continue

            # Stream body up to max_bytes + 1 byte
            content_type = resp.headers.get("Content-Type")
            status_code = resp.status_code
            chunks: List[bytes] = []
            bytes_received = 0
            bytes_stored = 0
            limit_exceeded = False
            read_error: Optional[str] = None

            try:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        chunk_len = len(chunk)
                        bytes_received += chunk_len
                        if bytes_stored + chunk_len > self.max_bytes:
                            limit_exceeded = True
                            remaining = self.max_bytes - bytes_stored
                            if remaining > 0:
                                chunks.append(chunk[:remaining])
                                bytes_stored += remaining
                            break
                        else:
                            chunks.append(chunk)
                            bytes_stored += chunk_len

                    if (time.perf_counter() - start_time) > self.timeout:
                        read_error = f"Overall source deadline exceeded ({self.timeout}s) during stream transfer"
                        break
            except requests.RequestException as exc:
                read_error = f"Stream read error: {type(exc).__name__}: {str(exc)}"
            finally:
                resp.close()

            duration_ms = (time.perf_counter() - start_time) * 1000.0
            body = b"".join(chunks)

            # Check overall deadline after streaming completes
            if duration_ms > (self.timeout * 1000.0) and not read_error:
                read_error = f"Overall source deadline exceeded ({self.timeout}s): elapsed {duration_ms / 1000.0:.2f}s"

            return status_code, content_type, body, bytes_received, bytes_stored, limit_exceeded, current_url, duration_ms, read_error

    def validate_content(
        self,
        source_id: str,
        http_status: Optional[int],
        content_type: Optional[str],
        body: bytes,
        limit_exceeded: bool,
        error_msg: Optional[str],
    ) -> Tuple[str, str, str, Optional[int]]:
        """Validate content against source contract.

        Returns:
            (status, validation_level, reason, item_count)
        """
        if error_msg:
            return "network_error", "transport", error_msg, None

        if http_status is None:
            return "network_error", "transport", "No HTTP response received", None

        # Check for stream limit violation
        if limit_exceeded:
            return (
                "invalid_content",
                "transport",
                f"Payload exceeded maximum stream limit of {self.max_bytes} bytes (2 MiB); full body hash not computed.",
                None,
            )

        # Check standard blocked statuses
        if http_status in (401, 403, 429):
            return (
                "blocked",
                "transport",
                f"HTTP {http_status} client access restricted by remote host / automated protection.",
                None,
            )

        if http_status >= 400:
            return (
                "network_error",
                "transport",
                f"HTTP error status {http_status}.",
                None,
            )

        # Endpoint-specific validation
        if source_id == "nyfed_effr":
            return self._validate_nyfed_effr(body)
        elif source_id == "fed_monetary_rss":
            return self._validate_fed_monetary_rss(body)
        elif source_id == "fed_speeches_rss":
            return self._validate_fed_speeches_rss(body)
        elif source_id == "bls_cpi_rss":
            return self._validate_bls_rss(body)
        elif source_id == "atlanta_gdpnow":
            return self._validate_atlanta_gdpnow(body, content_type)
        elif source_id == "github_deepseek_v3_releases":
            return self._validate_github_releases(body)
        elif source_id == "hf_deepseek_models":
            return self._validate_hf_models(body)
        elif source_id == "arena_text_overall":
            return self._validate_arena_html(body, content_type)
        else:
            return "invalid_content", "content", f"Unknown source_id '{source_id}'", None

    def _validate_nyfed_effr(self, body: bytes) -> Tuple[str, str, str, Optional[int]]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return "invalid_content", "envelope", "Failed to parse response body as JSON.", None

        if not isinstance(payload, dict):
            return "invalid_content", "envelope", "JSON response is not an object.", None

        rates = payload.get("refRates")
        if not isinstance(rates, list) or len(rates) == 0:
            return "invalid_content", "content", "refRates key missing or empty in JSON object.", None

        valid_items = 0
        for item in rates:
            if not isinstance(item, dict):
                return "invalid_content", "content", "refRates entry is not an object.", None

            # Exact series type check: must be strictly "EFFR"
            if item.get("type") != "EFFR":
                return (
                    "invalid_content",
                    "content",
                    f"refRates entry has invalid series type: expected 'EFFR', got '{item.get('type')}'",
                    None,
                )

            # Strict ISO calendar date check YYYY-MM-DD
            eff_date = item.get("effectiveDate")
            if not isinstance(eff_date, str):
                return "invalid_content", "content", f"effectiveDate must be string, got {type(eff_date).__name__}", None
            try:
                parsed_date = datetime.strptime(eff_date, "%Y-%m-%d").date()
                if parsed_date.strftime("%Y-%m-%d") != eff_date:
                    return "invalid_content", "content", f"Non-canonical date format: '{eff_date}'", None
            except (ValueError, TypeError) as exc:
                return "invalid_content", "content", f"Invalid calendar date '{eff_date}': {str(exc)}", None

            # percentRate must be a finite number and NOT a bool
            rate = item.get("percentRate")
            if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate):
                return (
                    "invalid_content",
                    "content",
                    f"Invalid percentRate: expected finite number, got {rate!r} ({type(rate).__name__})",
                    None,
                )
            valid_items += 1

        return (
            "valid",
            "content",
            f"Successfully validated {valid_items} EFFR reference rate records.",
            valid_items,
        )

    def _validate_fed_monetary_rss(self, body: bytes) -> Tuple[str, str, str, Optional[int]]:
        return self._validate_rss_feed(
            body=body,
            expected_feed_name="Federal Reserve Monetary Policy",
            allowed_channel_domains={"federalreserve.gov", "www.federalreserve.gov"},
            required_title_keywords={"monetary", "fomc", "press release"},
            forbidden_title_keywords={"speeches"},
            allowed_entry_domains={"federalreserve.gov", "www.federalreserve.gov"},
            required_entry_path_markers={"monetary", "pressreleases"},
        )

    def _validate_fed_speeches_rss(self, body: bytes) -> Tuple[str, str, str, Optional[int]]:
        return self._validate_rss_feed(
            body=body,
            expected_feed_name="Federal Reserve Speeches",
            allowed_channel_domains={"federalreserve.gov", "www.federalreserve.gov"},
            required_title_keywords={"speeches", "remarks", "speech"},
            forbidden_title_keywords={"monetary policy"},
            allowed_entry_domains={"federalreserve.gov", "www.federalreserve.gov"},
            required_entry_path_markers={"speech", "speeches"},
        )

    def _validate_bls_rss(self, body: bytes) -> Tuple[str, str, str, Optional[int]]:
        raw_text = body.decode("utf-8", errors="replace")
        if "<html" in raw_text.lower() and "<rss" not in raw_text.lower() and "<feed" not in raw_text.lower():
            return "blocked", "envelope", "HTTP 200 returned HTML challenge page instead of CPI RSS feed.", None

        return self._validate_rss_feed(
            body=body,
            expected_feed_name="BLS Consumer Price Index",
            allowed_channel_domains={"bls.gov", "www.bls.gov"},
            required_title_keywords={"cpi", "consumer price index", "bureau of labor statistics"},
            forbidden_title_keywords=set(),
            allowed_entry_domains={"bls.gov", "www.bls.gov"},
            required_entry_path_markers=set(),
        )

    def _validate_rss_feed(
        self,
        body: bytes,
        expected_feed_name: str,
        allowed_channel_domains: Set[str],
        required_title_keywords: Set[str],
        forbidden_title_keywords: Set[str],
        allowed_entry_domains: Set[str],
        required_entry_path_markers: Set[str],
    ) -> Tuple[str, str, str, Optional[int]]:
        raw_text = body.decode("utf-8", errors="replace")
        if "<rss" not in raw_text.lower() and "<feed" not in raw_text.lower() and "<?xml" not in raw_text:
            return "invalid_content", "envelope", "Response does not contain XML/RSS root marker.", None

        feed = feedparser.parse(body)
        channel_title = getattr(feed.feed, "title", "")
        channel_link = getattr(feed.feed, "link", "")

        title_lower = channel_title.lower() if isinstance(channel_title, str) else ""
        link_lower = channel_link.lower() if isinstance(channel_link, str) else ""

        if link_lower:
            parsed_link = urllib.parse.urlparse(link_lower)
            channel_domain = parsed_link.netloc.lower()
            if allowed_channel_domains and channel_domain not in allowed_channel_domains:
                return (
                    "invalid_content",
                    "content",
                    f"Feed identity mismatch: channel link domain '{channel_domain}' not in expected domains for {expected_feed_name}.",
                    None,
                )

        if required_title_keywords:
            if not any(kw in title_lower for kw in required_title_keywords):
                return (
                    "invalid_content",
                    "content",
                    f"Feed identity mismatch: expected {expected_feed_name}, got channel title '{channel_title}'.",
                    None,
                )

        if any(fkw in title_lower for fkw in forbidden_title_keywords):
            return (
                "invalid_content",
                "content",
                f"Feed identity mismatch: channel title '{channel_title}' contains forbidden marker for {expected_feed_name}.",
                None,
            )

        entries = getattr(feed, "entries", [])
        if not entries:
            return "invalid_content", "content", f"RSS feed '{channel_title}' contains zero entries.", 0

        valid_items = 0
        for entry in entries:
            title = getattr(entry, "title", None)
            link = getattr(entry, "link", None)
            if not isinstance(title, str) or len(title.strip()) == 0:
                continue
            if not isinstance(link, str) or len(link.strip()) == 0:
                continue

            parsed_entry_link = urllib.parse.urlparse(link)
            entry_domain = parsed_entry_link.netloc.lower()
            if allowed_entry_domains and entry_domain not in allowed_entry_domains:
                continue

            if required_entry_path_markers:
                path_lower = parsed_entry_link.path.lower()
                if not any(pm in path_lower for pm in required_entry_path_markers):
                    continue

            valid_items += 1

        if valid_items != len(entries) or valid_items == 0:
            return (
                "invalid_content",
                "content",
                f"Feed entries validation failed: {valid_items}/{len(entries)} entries valid for {expected_feed_name}.",
                valid_items,
            )

        return (
            "valid",
            "content",
            f"Valid {expected_feed_name} feed with {len(entries)} items ({valid_items} valid).",
            len(entries),
        )

    def _validate_atlanta_gdpnow(self, body: bytes, content_type: Optional[str]) -> Tuple[str, str, str, Optional[int]]:
        raw_text = body.decode("utf-8", errors="replace")
        if "<html" not in raw_text.lower():
            return "invalid_content", "envelope", "Response is not an HTML document.", None

        return (
            "unverified",
            "envelope",
            "HTML document received (transport and envelope confirmed); quantitative model table extraction not verified in R1.",
            None,
        )

    def _validate_github_releases(self, body: bytes) -> Tuple[str, str, str, Optional[int]]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return "invalid_content", "envelope", "Failed to parse GitHub response as JSON.", None

        if isinstance(payload, dict):
            message = str(payload.get("message", ""))
            if "rate limit" in message.lower():
                return "blocked", "transport", f"GitHub API rate limit exceeded: {message}", None
            return "invalid_content", "envelope", f"Expected releases array, got JSON object: {message}", None

        if not isinstance(payload, list):
            return "invalid_content", "envelope", "Expected JSON array of releases.", None

        if len(payload) == 0:
            return "valid", "content", "Valid empty GitHub releases array (item_count=0).", 0

        valid_items = 0
        for item in payload:
            if not isinstance(item, dict):
                return "invalid_content", "content", "Release entry is not a dictionary.", None
            tag = item.get("tag_name")
            if not isinstance(tag, str) or len(tag.strip()) == 0:
                return "invalid_content", "content", f"Release entry missing non-empty string tag_name: {tag!r}", None
            html_url = item.get("html_url")
            if not isinstance(html_url, str):
                return "invalid_content", "content", f"Release entry missing string html_url: {html_url!r}", None

            parsed = urllib.parse.urlparse(html_url)
            if parsed.netloc.lower() not in ("github.com", "api.github.com"):
                return "invalid_content", "content", f"Invalid GitHub release host: '{parsed.netloc}'", None
            parts = [p for p in parsed.path.strip("/").split("/") if p]
            if len(parts) < 3 or parts[0] != "deepseek-ai" or parts[1] != "DeepSeek-V3" or parts[2] != "releases":
                return (
                    "invalid_content",
                    "content",
                    f"Release repo/path mismatch: expected 'deepseek-ai/DeepSeek-V3/releases', got '{'/'.join(parts[:3])}'",
                    None,
                )
            valid_items += 1

        return (
            "valid",
            "content",
            f"Valid GitHub releases array for deepseek-ai/DeepSeek-V3 with {valid_items} releases.",
            valid_items,
        )

    def _validate_hf_models(self, body: bytes) -> Tuple[str, str, str, Optional[int]]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return "invalid_content", "envelope", "Failed to parse Hugging Face response as JSON.", None

        if not isinstance(payload, list):
            return "invalid_content", "envelope", "Expected JSON array of models.", None

        if len(payload) == 0:
            return "valid", "content", "Valid empty Hugging Face models array (item_count=0).", 0

        valid_items = 0
        for item in payload:
            if not isinstance(item, dict):
                return "invalid_content", "content", "Model entry is not a dictionary.", None
            model_id = item.get("id") or item.get("_id")
            if not isinstance(model_id, str):
                return "invalid_content", "content", f"Model entry missing string id: {model_id!r}", None
            if not model_id.startswith("deepseek-ai/"):
                return "invalid_content", "content", f"Model id '{model_id}' does not start with 'deepseek-ai/'", None
            name = model_id[len("deepseek-ai/"):].strip()
            if not name or "/" in name:
                return "invalid_content", "content", f"Model id '{model_id}' has empty or invalid model name", None

            author = item.get("author")
            if author is not None and author != "deepseek-ai":
                return "invalid_content", "content", f"Model author '{author}' contradicts namespace 'deepseek-ai'", None
            valid_items += 1

        return (
            "valid",
            "content",
            f"Valid Hugging Face models array for deepseek-ai with {valid_items} models.",
            valid_items,
        )

    def _validate_arena_html(self, body: bytes, content_type: Optional[str]) -> Tuple[str, str, str, Optional[int]]:
        raw_text = body.decode("utf-8", errors="replace")
        if "<html" not in raw_text.lower():
            return "invalid_content", "envelope", "Response is not an HTML document.", None

        return (
            "unverified",
            "envelope",
            "HTML document received (transport and envelope confirmed); leaderboard table extraction not verified in R1.",
            None,
        )

    def probe_single(self, source_cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single bounded probe for a source configuration."""
        source_id = source_cfg["source_id"]
        initial_url = source_cfg["url"]

        started_at = utc_now_iso()

        (
            status_code,
            content_type,
            body,
            bytes_received,
            bytes_stored,
            limit_exceeded,
            final_url,
            duration_ms,
            error_msg,
        ) = self._fetch_bounded(source_id, initial_url)

        completed_at = utc_now_iso()

        status, validation_level, reason, item_count = self.validate_content(
            source_id=source_id,
            http_status=status_code,
            content_type=content_type,
            body=body,
            limit_exceeded=limit_exceeded,
            error_msg=error_msg,
        )

        # Invariant: SHA-256 of complete body computed ONLY after successful EOF without error or overflow
        if limit_exceeded or error_msg is not None or status_code is None or status_code >= 400 or status != "valid":
            body_sha256 = None
        else:
            body_sha256 = hashlib.sha256(body).hexdigest()

        return {
            "source_id": source_id,
            "platform": source_cfg.get("platform"),
            "family": source_cfg.get("family"),
            "initial_url": initial_url,
            "final_url": final_url,
            "http_status": status_code,
            "fetch_started_at": started_at,
            "fetch_completed_at": completed_at,
            "duration_ms": round(duration_ms, 2),
            "bytes_received": bytes_received,
            "bytes_stored": bytes_stored,
            "content_type": content_type,
            "body_sha256": body_sha256,
            "status": status,
            "validation_level": validation_level,
            "reason": reason,
            "item_count": item_count,
            "published_at": None,  # Strictly null in R1 probe
            "checker_version": PROBE_VERSION,
            "usable_for_forecast": False,  # Strict invariant
        }

    def run(self) -> Dict[str, Any]:
        """Run sequential probes across all registered sources and compile report."""
        started_at = utc_now_iso()

        try:
            sources = self.load_registry()
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            logger.error("Registry validation failed: %s", exc)
            return {
                "probe_run_version": PROBE_VERSION,
                "started_at": started_at,
                "completed_at": utc_now_iso(),
                "total_sources": 0,
                "status_counts": {
                    "valid": 0,
                    "blocked": 0,
                    "invalid_content": 0,
                    "network_error": 0,
                    "unverified": 0,
                },
                "summary_status": "INVALID_REGISTRY",
                "all_healthy": False,
                "usable_for_forecast_all": False,
                "registry_error": str(exc),
                "results": [],
            }

        results: List[Dict[str, Any]] = []
        status_counts = {
            "valid": 0,
            "blocked": 0,
            "invalid_content": 0,
            "network_error": 0,
            "unverified": 0,
        }

        for cfg in sources:
            source_id = cfg.get("source_id", "unknown")
            try:
                res = self.probe_single(cfg)
            except Exception as exc:
                logger.exception("Unexpected exception probing %s", source_id)
                res = {
                    "source_id": source_id,
                    "platform": cfg.get("platform"),
                    "family": cfg.get("family"),
                    "initial_url": cfg.get("url"),
                    "final_url": cfg.get("url"),
                    "http_status": None,
                    "fetch_started_at": utc_now_iso(),
                    "fetch_completed_at": utc_now_iso(),
                    "duration_ms": 0.0,
                    "bytes_received": 0,
                    "bytes_stored": 0,
                    "content_type": None,
                    "body_sha256": None,
                    "status": "network_error",
                    "validation_level": "transport",
                    "reason": f"Unhandled probe exception: {type(exc).__name__}: {str(exc)}",
                    "item_count": None,
                    "published_at": None,
                    "checker_version": PROBE_VERSION,
                    "usable_for_forecast": False,
                }
            results.append(res)
            st = res.get("status", "network_error")
            status_counts[st] = status_counts.get(st, 0) + 1

        completed_at = utc_now_iso()

        # Probe health summary: requires all canonical sources present AND all valid
        all_valid = (status_counts["valid"] == len(CANONICAL_SOURCES)) and (len(sources) == len(CANONICAL_SOURCES))
        summary_status = "ALL_VALID" if all_valid else "PARTIAL_REACHABLE"

        return {
            "probe_run_version": PROBE_VERSION,
            "started_at": started_at,
            "completed_at": completed_at,
            "total_sources": len(sources),
            "status_counts": status_counts,
            "summary_status": summary_status,
            "all_healthy": all_valid,
            "usable_for_forecast_all": False,
            "results": results,
        }


def format_table(results: List[Dict[str, Any]]) -> str:
    """Format probe results as a readable markdown/text table."""
    headers = ["source_id", "HTTP", "Recv", "Stored", "ms", "Status", "Level", "Items", "Reason"]
    rows = []
    for r in results:
        rows.append([
            r["source_id"],
            str(r["http_status"]) if r["http_status"] is not None else "ERR",
            str(r.get("bytes_received", 0)),
            str(r.get("bytes_stored", 0)),
            str(int(r["duration_ms"])),
            r["status"],
            r["validation_level"],
            str(r["item_count"]) if r["item_count"] is not None else "—",
            r["reason"][:40] + ("…" if len(r["reason"]) > 40 else ""),
        ])

    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(cell))

    lines = []
    header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_line = "-|-".join("-" * col_widths[i] for i in range(len(headers)))
    lines.append(f"| {header_line} |")
    lines.append(f"| {sep_line} |")
    for row in rows:
        row_line = " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row))
        lines.append(f"| {row_line} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pythia R1.1 source probe")
    parser.add_argument("--output", "-o", type=Path, help="Output path for probe JSON results", required=False)
    args = parser.parse_args()

    with SourceProbe() as probe:
        summary = probe.run()

    print("\n=== Pythia R1.1 Source Probe Summary ===")
    print(f"Total Sources: {summary['total_sources']}")
    print(f"Status Counts: {summary['status_counts']}")
    print(f"Summary Status: {summary['summary_status']} (all_healthy={summary['all_healthy']})")
    if summary.get("registry_error"):
        print(f"Registry Error: {summary['registry_error']}")
    print("\n" + format_table(summary["results"]) + "\n")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"Detailed JSON results written to {args.output}")

    if summary["summary_status"] == "INVALID_REGISTRY":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
