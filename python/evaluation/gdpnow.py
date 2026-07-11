#!/usr/bin/env python3
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import feedparser

GDPNOW_RSS = "https://www.atlantafed.org/rss/GDPNow"
_EST_RE = re.compile(r"is\s+(-?\d+\.?\d*)\s+percent")

CACHE_DIR = Path(__file__).resolve().parent / "data"
CACHE_FILE = CACHE_DIR / "gdpnow_cache.json"
CACHE_TTL = 3600

logger = logging.getLogger(__name__)


def _load_cache():
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE) as f:
                return json.load(f)
        except json.JSONDecodeError as exc:
            logger.warning("Ignoring invalid GDPNow cache %s: %s", CACHE_FILE, exc)
        except OSError as exc:
            logger.warning("Could not read GDPNow cache %s: %s", CACHE_FILE, exc)
    return {}


def _save_cache(cache):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def _parse_entry(start):
    match = _EST_RE.search(start)
    if match:
        return float(match.group(1))
    return None


def get_latest_gdpnow() -> dict | None:
    cache = _load_cache()
    cached = cache.get("gdpnow")
    if cached and (datetime.now(timezone.utc).timestamp() - cached.get("ts", 0)) < CACHE_TTL:
        return cached
    feed = feedparser.parse(GDPNOW_RSS)
    if feed.bozo:
        logger.warning("GDPNow RSS parse failed")
        return None
    for entry in feed.entries:
        estimate = _parse_entry(entry.get("description", ""))
        if estimate is not None:
            start = {"data": estimate, "date": entry.get("published", ""), "ts": datetime.now(timezone.utc).timestamp()}
            cache["gdpnow"] = start
            _save_cache(cache)
            return start
    logger.warning("GDPNow RSS contained no estimates")
    return None


def get_gdpnow_vintage(limit=20) -> list[dict]:
    feed = feedparser.parse(GDPNOW_RSS)
    if feed.bozo:
        logger.warning("GDPNow RSS parse failed")
        return []
    result = []
    for entry in feed.entries[:limit]:
        estimate = _parse_entry(entry.get("description", ""))
        if estimate is not None:
            result.append({"data": estimate, "date": entry.get("published", "")})
    return result
