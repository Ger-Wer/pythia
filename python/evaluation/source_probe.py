"""Pythia R1: Bounded single-run probe for the 8 canonical evidence sources.

Runs sequential diagnostic HTTP/HTTPS requests against sources specified in
source_registry.json. Performs strict transport, envelope, and content validation
under controlled constraints (streaming 2 MiB limit, max 3 redirects, domain allowlists,
no external credentials or bypasses).

Usage:
    python -m python.evaluation.source_probe --output <path-to-json-output>
"""

from __future__ import annotations

import argparse
import hashlib
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

PROBE_VERSION = "pythia-probe-r1/1.0"
STREAM_LIMIT_BYTES = 2 * 1024 * 1024  # 2 MiB
DEFAULT_TIMEOUT_SECONDS = 15
MAX_REDIRECTS = 3

DEFAULT_USER_AGENT = "Pythia-source-probe/1.0 (+https://github.com/Ger-Wer/pythia)"

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


class SourceProbe:
    """Runs bounded diagnostic probes for configured evidence sources."""

    def __init__(
        self,
        registry_path: Optional[Path] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        max_redirects: int = MAX_REDIRECTS,
        max_bytes: int = STREAM_LIMIT_BYTES,
        user_agent: str = DEFAULT_USER_AGENT,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.registry_path = registry_path or Path(__file__).parent / "source_registry.json"
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self.session = session or requests.Session()

    def load_registry(self) -> List[Dict[str, Any]]:
        """Load and return the list of source configurations from registry."""
        with open(self.registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        sources = data.get("sources", [])
        if not isinstance(sources, list) or len(sources) != 8:
            logger.warning("Expected 8 sources in registry, found %d", len(sources))
        return sources

    def _fetch_bounded(
        self,
        source_id: str,
        initial_url: str,
    ) -> Tuple[Optional[int], Optional[str], bytes, bool, str, float, Optional[str]]:
        """Fetch URL with strict redirect, domain allowlist, timeout and stream size bounds.

        Returns:
            (status_code, content_type, body_bytes, limit_exceeded, final_url, duration_ms, error_msg)
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
            parsed = urllib.parse.urlparse(current_url)
            if parsed.scheme.lower() != "https":
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return None, None, b"", False, current_url, duration_ms, f"Non-HTTPS scheme rejected: {parsed.scheme}"

            domain = parsed.netloc.lower()
            if domain not in allowed_domains:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return (
                    None,
                    None,
                    b"",
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
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return None, None, b"", False, current_url, duration_ms, f"Network transport error: {type(exc).__name__}: {str(exc)}"

            # Handle redirects manually to enforce max_redirects and domain constraints
            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                redirect_count += 1
                if redirect_count > self.max_redirects:
                    duration_ms = (time.perf_counter() - start_time) * 1000.0
                    return (
                        resp.status_code,
                        resp.headers.get("Content-Type"),
                        b"",
                        False,
                        current_url,
                        duration_ms,
                        f"Exceeded maximum redirect limit ({self.max_redirects})",
                    )
                location = resp.headers.get("Location")
                if not location:
                    duration_ms = (time.perf_counter() - start_time) * 1000.0
                    return (
                        resp.status_code,
                        resp.headers.get("Content-Type"),
                        b"",
                        False,
                        current_url,
                        duration_ms,
                        "Redirect response missing Location header",
                    )
                current_url = urllib.parse.urljoin(current_url, location)
                continue

            # Stream body up to max_bytes + 1 byte
            content_type = resp.headers.get("Content-Type")
            chunks: List[bytes] = []
            total_bytes = 0
            limit_exceeded = False

            try:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        total_bytes += len(chunk)
                        if total_bytes > self.max_bytes:
                            limit_exceeded = True
                            break
                        chunks.append(chunk)
            except requests.RequestException as exc:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return (
                    resp.status_code,
                    content_type,
                    b"".join(chunks),
                    False,
                    current_url,
                    duration_ms,
                    f"Stream read error: {type(exc).__name__}: {str(exc)}",
                )
            finally:
                resp.close()

            duration_ms = (time.perf_counter() - start_time) * 1000.0
            body = b"".join(chunks)
            return resp.status_code, content_type, body, limit_exceeded, current_url, duration_ms, None

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
            return self._validate_fed_rss(body, expected_marker="monetary")
        elif source_id == "fed_speeches_rss":
            return self._validate_fed_rss(body, expected_marker="speeches")
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
            return "unverified", "transport", f"Unknown source_id {source_id}", None

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
                continue
            rate_type = str(item.get("type", "")).upper()
            effective_date = item.get("effectiveDate")
            percent_rate = item.get("percentRate")

            if "EFFR" in rate_type and isinstance(effective_date, str) and isinstance(percent_rate, (int, float)):
                valid_items += 1

        if valid_items == len(rates):
            return (
                "valid",
                "content",
                f"Successfully validated {valid_items} EFFR reference rate records.",
                valid_items,
            )
        return (
            "invalid_content",
            "content",
            f"refRates elements do not match EFFR schema ({valid_items}/{len(rates)} valid).",
            valid_items,
        )

    def _validate_fed_rss(self, body: bytes, expected_marker: str) -> Tuple[str, str, str, Optional[int]]:
        raw_text = body.decode("utf-8", errors="replace")
        # Check that it is not an HTML error or empty page
        if "<rss" not in raw_text.lower() and "<feed" not in raw_text.lower() and "<?xml" not in raw_text:
            return "invalid_content", "envelope", "Response does not contain XML/RSS root marker.", None

        feed = feedparser.parse(body)
        entries = getattr(feed, "entries", [])
        if not entries:
            # Check if feed had a bozo exception or empty channel
            if getattr(feed, "bozo", 0) == 1 and not getattr(feed, "feed", None):
                return "invalid_content", "envelope", f"RSS parsing error: {feed.get('bozo_exception', 'unknown')}", None
            return "invalid_content", "content", "RSS feed parsed but contains zero entries.", 0

        valid_items = 0
        for entry in entries:
            if getattr(entry, "title", None) and getattr(entry, "link", None):
                valid_items += 1

        if valid_items == 0:
            return "invalid_content", "content", "RSS feed entries missing required title and link elements.", 0

        return (
            "valid",
            "content",
            f"Valid Federal Reserve {expected_marker} RSS feed with {len(entries)} items ({valid_items} valid).",
            len(entries),
        )

    def _validate_bls_rss(self, body: bytes) -> Tuple[str, str, str, Optional[int]]:
        raw_text = body.decode("utf-8", errors="replace")
        if "<rss" not in raw_text.lower() and "<feed" not in raw_text.lower():
            # If server returned HTML 200 challenge
            if "<html" in raw_text.lower():
                return "blocked", "envelope", "HTTP 200 returned HTML challenge page instead of CPI RSS feed.", None
            return "invalid_content", "envelope", "Response does not contain RSS/XML feed.", None

        feed = feedparser.parse(body)
        entries = getattr(feed, "entries", [])
        if not entries:
            return "invalid_content", "content", "BLS RSS feed parsed but contains no entries.", 0

        return (
            "valid",
            "content",
            f"Valid BLS CPI RSS feed with {len(entries)} entries.",
            len(entries),
        )

    def _validate_atlanta_gdpnow(self, body: bytes, content_type: Optional[str]) -> Tuple[str, str, str, Optional[int]]:
        raw_text = body.decode("utf-8", errors="replace")
        if "<html" not in raw_text.lower():
            return "invalid_content", "envelope", "Response is not an HTML document.", None

        # Honest classification per prompt: HTML transport/envelope confirmed, but content extraction unverified
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
            return (
                "valid",
                "content",
                "Valid empty GitHub releases array (item_count=0).",
                0,
            )

        valid_items = 0
        for item in payload:
            if not isinstance(item, dict):
                continue
            html_url = str(item.get("html_url", ""))
            tag_name = item.get("tag_name") or item.get("name")
            if "deepseek-ai/DeepSeek-V3" in html_url and tag_name:
                valid_items += 1

        if valid_items == len(payload):
            return (
                "valid",
                "content",
                f"Valid GitHub releases array for deepseek-ai/DeepSeek-V3 with {len(payload)} releases.",
                len(payload),
            )
        return (
            "invalid_content",
            "content",
            f"Releases items failed DeepSeek-V3 validation ({valid_items}/{len(payload)} matched).",
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
            return (
                "valid",
                "content",
                "Valid empty Hugging Face models array (item_count=0).",
                0,
            )

        valid_items = 0
        for item in payload:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id", item.get("_id", "")))
            if model_id.startswith("deepseek-ai/"):
                valid_items += 1

        if valid_items == len(payload):
            return (
                "valid",
                "content",
                f"Valid Hugging Face models array for deepseek-ai with {len(payload)} models.",
                len(payload),
            )
        return (
            "invalid_content",
            "content",
            f"Models items failed deepseek-ai author check ({valid_items}/{len(payload)} matched).",
            valid_items,
        )

    def _validate_arena_html(self, body: bytes, content_type: Optional[str]) -> Tuple[str, str, str, Optional[int]]:
        raw_text = body.decode("utf-8", errors="replace")
        if "<html" not in raw_text.lower():
            return "invalid_content", "envelope", "Response is not an HTML document.", None

        # Honest classification per prompt:
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

        status_code, content_type, body, limit_exceeded, final_url, duration_ms, error_msg = self._fetch_bounded(
            source_id, initial_url
        )

        completed_at = utc_now_iso()

        # Compute SHA-256 of complete body only if not exceeded and no fatal transport error
        if limit_exceeded or error_msg and not body:
            body_sha256 = None
        else:
            body_sha256 = hashlib.sha256(body).hexdigest()

        status, validation_level, reason, item_count = self.validate_content(
            source_id=source_id,
            http_status=status_code,
            content_type=content_type,
            body=body,
            limit_exceeded=limit_exceeded,
            error_msg=error_msg,
        )

        # If limit was exceeded, ensure sha256 is null
        if limit_exceeded:
            body_sha256 = None

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
            "bytes_received": len(body),
            "content_type": content_type,
            "body_sha256": body_sha256,
            "status": status,
            "validation_level": validation_level,
            "reason": reason,
            "item_count": item_count,
            "published_at": None,  # Explicitly null in R1 probe
            "checker_version": PROBE_VERSION,
            "usable_for_forecast": False,  # Strict R1 invariant
        }

    def run(self) -> Dict[str, Any]:
        """Run sequential probes across all registered sources and compile report."""
        sources = self.load_registry()
        started_at = utc_now_iso()

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

        # Probe health summary
        all_valid = (status_counts["valid"] == len(sources)) and (len(sources) > 0)
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
    headers = ["source_id", "HTTP", "Bytes", "ms", "Status", "Level", "Items", "Reason"]
    rows = []
    for r in results:
        rows.append([
            r["source_id"],
            str(r["http_status"]) if r["http_status"] is not None else "ERR",
            str(r["bytes_received"]),
            str(int(r["duration_ms"])),
            r["status"],
            r["validation_level"],
            str(r["item_count"]) if r["item_count"] is not None else "—",
            r["reason"][:45] + ("…" if len(r["reason"]) > 45 else ""),
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
    parser = argparse.ArgumentParser(description="Pythia R1 source probe")
    parser.add_argument("--output", "-o", type=Path, help="Output path for probe JSON results", required=False)
    parser.add_argument("--registry", "-r", type=Path, help="Custom registry path", required=False)
    args = parser.parse_args()

    probe = SourceProbe(registry_path=args.registry)
    summary = probe.run()

    print("\n=== Pythia R1 Source Probe Summary ===")
    print(f"Total Sources: {summary['total_sources']}")
    print(f"Status Counts: {summary['status_counts']}")
    print(f"Summary Status: {summary['summary_status']} (all_healthy={summary['all_healthy']})")
    print("\n" + format_table(summary["results"]) + "\n")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"Detailed JSON results written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
