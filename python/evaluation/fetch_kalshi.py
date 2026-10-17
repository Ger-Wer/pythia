#!/usr/bin/env python3
"""
Kalshi FOMC meeting-specific markets fetcher.
Fetches prediction market prices for Fed rate decisions from Kalshi's public API.
No auth required for read-only market data.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import requests

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES_TICKER = "KXFEDDECISION"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

CACHE_DIR = Path(__file__).resolve().parent / "data"
CACHE_FILE = CACHE_DIR / "kalshi_cache.json"
CACHE_TTL = 3600


def _load_cache():
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cache(cache):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def _parse_strike_info(ticker: str) -> dict:
    """Extract strike/bucket info from market ticker.

    E.g., KXFEDDECISION-26JUL-H0 -> {"direction": "Hike", "bucket": "0"}
    """
    parts = ticker.rsplit("-", 1)
    if len(parts) < 2:
        return {}
    suffix = parts[-1]
    if suffix.startswith("H"):
        return {"direction": "Hike", "bucket": suffix[1:]}
    elif suffix.startswith("C"):
        return {"direction": "Cut", "bucket": suffix[1:]}
    return {"raw_suffix": suffix}


def _parse_date(close_time: str) -> str:
    """Parse ISO timestamp to YYYY-MM-DD."""
    try:
        dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return ""


def _fetch_markets_page(cursor=None):
    """Fetch one page of Kalshi markets. Returns (markets, next_cursor)."""
    params = {
        "series_ticker": SERIES_TICKER,
        "status": "open",
        "limit": 25,
    }
    if cursor:
        params["cursor"] = cursor
    try:
        r = requests.get(
            f"{KALSHI_BASE}/markets",
            headers=HEADERS,
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        fetched_at = datetime.now(timezone.utc).isoformat()
        markets = data.get("markets", [])
        # Stamp the successful provider response, not cache access. Cached
        # normalized records retain this original timestamp unchanged.
        markets = [dict(m, _fetched_at=fetched_at) for m in markets]
        next_cursor = data.get("cursor")
        return markets, next_cursor
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"[kalshi] Error fetching markets: {e}")
        return [], None


def _fetch_all_markets():
    """Fetch all open FOMC markets with pagination."""
    all_markets = []
    cursor = None
    while True:
        markets, next_cursor = _fetch_markets_page(cursor)
        if not markets:
            break
        all_markets.extend(markets)
        if not next_cursor:
            break
        cursor = next_cursor
    return all_markets


def _normalize_market(m: dict) -> dict:
    """Convert raw Kalshi market to standardized format."""
    last_price = m.get("last_price_dollars")
    if last_price is None:
        last_price = 0.0
    return {
        "source": "kalshi",
        "market_id": m.get("ticker", ""),
        "event_ticker": m.get("event_ticker", ""),
        "question": m.get("title", ""),
        "yes_price": float(last_price),
        "yes_bid": float(m.get("yes_bid_dollars", 0.0)),
        "yes_ask": float(m.get("yes_ask_dollars", 0.0)),
        "end_date": _parse_date(m.get("close_time", "")),
        "volume": float(m.get("volume_fp", 0)),
        "strike_info": _parse_strike_info(m.get("ticker", "")),
        "fetched_at": m.get("_fetched_at"),
    }


def get_kalshi_fomc_markets() -> list[dict]:
    """Fetch all active FOMC meeting-specific markets from Kalshi.

    Returns list of dicts with standardized fields:
    - source: "kalshi"
    - market_id: ticker
    - question: title
    - yes_price: float (0-1)
    - end_date: str (YYYY-MM-DD from close_time)
    - volume: float
    - event_ticker: str
    - strike_info: dict (Hike/Cut buckets)
    """
    cache = _load_cache()
    now = datetime.now(timezone.utc).timestamp()

    if "kalshi_markets" in cache and "kalshi_ts" in cache:
        age = now - cache["kalshi_ts"]
        if age < CACHE_TTL:
            return cache["kalshi_markets"]

    raw = _fetch_all_markets()
    if not raw:
        # Return stale cache if fetch fails
        return cache.get("kalshi_markets", [])

    markets = [_normalize_market(m) for m in raw]
    cache["kalshi_markets"] = markets
    cache["kalshi_ts"] = now
    _save_cache(cache)
    return markets


if __name__ == "__main__":
    markets = get_kalshi_fomc_markets()
    print(f"Kalshi FOMC markets: {len(markets)}")
    for m in markets[:5]:
        print(f"  {m['market_id']}: {m['question']}")
        print(f"    yes_price={m['yes_price']:.2f}  volume={m['volume']:.0f}  end={m['end_date']}")
        print(f"    strike_info={m['strike_info']}")
