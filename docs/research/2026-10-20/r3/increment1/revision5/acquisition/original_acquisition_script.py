"""Verbatim recovery of initial bounded acquisition script from execution transcript.

Timestamp: 2026-10-19T07:47:12Z
Runtime: /home/pythia/pythia-paper-channel-20261017/python/venv/bin/python3
Server: amper (Ubuntu 24.04, Linux 6.8.0-45-generic x86_64)
Execution user: pythia
Network policy: HTTPS only, verified TLS, anonymous session (trust_env=False), max 2 redirects, max 2 MiB, 20s timeout.
"""

import hashlib
import json
import socket
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
import requests

TARGETS = [
    {
        "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm",
        "fixture_key": "monetary20260916a",
        "parent_feed": {
            "source_id": "fed_monetary_rss",
            "feed_fact_id": "fact-b7cdc057cc6ec28d",
            "feed_receipt_id": "39bf5ee5-5918-489f-b9e1-dfcfcfbce96b",
            "feed_revision_id": "26b40558-1014-47f2-9b10-d7b8007a07d0",
            "feed_blob_sha256": "69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1",
            "feed_item_locator": {"type": "xml_xpath", "path": "/rss/channel/item[3]"},
            "feed_raw_pub_date": "Wed, 16 Sep 2026 18:00:00 GMT",
            "feed_published_at": "2026-09-16T18:00:00+00:00",
        }
    },
    {
        "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm",
        "fixture_key": "monetary20260729a",
        "parent_feed": {
            "source_id": "fed_monetary_rss",
            "feed_fact_id": "fact-23bc4c0fc0243b9e",
            "feed_receipt_id": "39bf5ee5-5918-489f-b9e1-dfcfcfbce96b",
            "feed_revision_id": "26b40558-1014-47f2-9b10-d7b8007a07d0",
            "feed_blob_sha256": "69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1",
            "feed_item_locator": {"type": "xml_xpath", "path": "/rss/channel/item[7]"},
            "feed_raw_pub_date": "Wed, 29 Jul 2026 18:00:00 GMT",
            "feed_published_at": "2026-07-29T18:00:00+00:00",
        }
    }
]

ALLOWED_DOMAINS = {"federalreserve.gov", "www.federalreserve.gov"}
MAX_BYTES = 2 * 1024 * 1024  # 2 MiB
MAX_REDIRECTS = 2
TIMEOUT = 20.0
USER_AGENT = "PythiaResearch/1.0 (bounded offline statement fixture acquisitor; +https://github.com/Ger-Wer/pythia)"

out_dir = Path("/home/pythia/pythia-evidence-r31/docs/research/2026-10-19/r3/increment1/fixtures")
out_dir.mkdir(parents=True, exist_ok=True)

results = []

for item in TARGETS:
    url = item["source_url"]
    key = item["fixture_key"]
    print(f"[Acquiring] {url}...")
    
    session = requests.Session()
    session.trust_env = False  # Strictly no ambient credentials / netrc / environment proxies
    
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    
    current_url = url
    redirects = 0
    raw_body = bytearray()
    status_code = None
    resp_headers = {}
    content_type = None
    outcome = "failure"
    error_msg = None
    
    try:
        while True:
            parsed = urllib.parse.urlsplit(current_url)
            if parsed.scheme.lower() != "https":
                raise ValueError(f"Non-HTTPS scheme rejected: {parsed.scheme}")
            if parsed.netloc.lower() not in ALLOWED_DOMAINS:
                raise ValueError(f"Domain {parsed.netloc} not in allowed domains: {ALLOWED_DOMAINS}")
                
            resp = session.get(
                current_url,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
                timeout=TIMEOUT,
                allow_redirects=False,
                stream=True,
                verify=True,
            )
            status_code = resp.status_code
            resp_headers = dict(resp.headers)
            content_type = resp.headers.get("content-type")
            
            if status_code in (301, 302, 303, 307, 308):
                redirects += 1
                if redirects > MAX_REDIRECTS:
                    raise ValueError(f"Max redirects ({MAX_REDIRECTS}) exceeded")
                location = resp.headers.get("Location")
                if not location:
                    raise ValueError(f"Redirect status {status_code} missing Location header")
                current_url = urllib.parse.urljoin(current_url, location)
                continue
                
            # Read body up to max_bytes
            for chunk in resp.iter_content(chunk_size=8192):
                raw_body.extend(chunk)
                if len(raw_body) > MAX_BYTES:
                    raise ValueError(f"Response body exceeded limit {MAX_BYTES} bytes")
            break
            
        completed_at = datetime.now(timezone.utc).isoformat()
        t1 = time.perf_counter()
        duration_ms = (t1 - t0) * 1000.0
        
        final_bytes = bytes(raw_body)
        raw_sha = hashlib.sha256(final_bytes).hexdigest()
        outcome = "success" if status_code == 200 else "negative"
        
        # Save raw HTML fixture
        raw_file = out_dir / f"raw_{key}.html"
        raw_file.write_bytes(final_bytes)
        
        # Create explicit fixture manifest
        manifest = {
            "fixture_key": key,
            "source_url": url,
            "final_url": current_url,
            "redirect_count": redirects,
            "http_status": status_code,
            "content_type": content_type,
            "response_headers": {k: v for k, v in resp_headers.items() if not k.lower().startswith("set-cookie")},
            "bytes_stored": len(final_bytes),
            "blob_sha256": raw_sha,
            "fetch_started_at": started_at,
            "fetch_completed_at": completed_at,
            "duration_ms": round(duration_ms, 3),
            "collected_at": completed_at,
            "research_provenance": "research_fixture",
            "parent_feed_reference": item["parent_feed"],
            "transport_outcome": outcome,
            "error_msg": error_msg,
            "usable_for_forecast": False,
        }
        
        manifest_file = out_dir / f"manifest_{key}.json"
        manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        
        print(f"  Success: {len(final_bytes)} bytes, sha256={raw_sha}")
        print(f"  Saved to: {raw_file.name} and {manifest_file.name}")
        results.append(manifest)
        
    except Exception as exc:
        completed_at = datetime.now(timezone.utc).isoformat()
        t1 = time.perf_counter()
        duration_ms = (t1 - t0) * 1000.0
        print(f"  Error fetching {url}: {exc}")
        results.append({
            "fixture_key": key,
            "source_url": url,
            "final_url": current_url,
            "transport_outcome": "failure",
            "error_msg": str(exc),
            "fetch_started_at": started_at,
            "fetch_completed_at": completed_at,
            "duration_ms": round(duration_ms, 3),
            "collected_at": completed_at,
            "research_provenance": "research_fixture",
            "parent_feed_reference": item["parent_feed"],
            "usable_for_forecast": False,
        })
    finally:
        session.close()

summary_file = out_dir / "fixtures_summary.json"
summary_file.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
print(f"Done! Summary written to {summary_file}")
