#!/usr/bin/env python3
"""
ZQ (fed funds futures) price fetcher with source-pluggable fallback.
Primary: Yahoo Finance ZQ=F (free, no key).
Fallback: Barchart HTML scrape (best-effort, requires bs4).
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/ZQ=F?range=5d&interval=1d"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

CACHE_DIR = Path(__file__).resolve().parent / "data"
CACHE_FILE = CACHE_DIR / "zq_cache.json"
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


def zq_price_to_rate(price: float) -> float:
    """Convert ZQ futures price to implied fed funds rate."""
    return 100.0 - price


def _fetch_yahoo():
    """Fetch ZQ price from Yahoo Finance (primary source)."""
    try:
        r = requests.get(YAHOO_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None
        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice")
        if price is None:
            return None
        return {"price": float(price), "source": "yahoo"}
    except Exception:
        return None


def _fetch_barchart():
    """Fetch ZQ price from Barchart (best-effort fallback)."""
    try:
        from bs4 import BeautifulSoup
        url = "https://www.barchart.com/futures/quotes/ZQM25"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if not r.ok:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        # Look for price in page
        price_el = soup.find("span", {"class": "last-change"})
        if price_el:
            price_text = price_el.get_text(strip=True)
            price = float(price_text)
            return {"price": price, "source": "barchart"}
    except Exception:
        pass
    return None


def get_zq_price():
    """Get current ZQ futures price with source fallback.
    
    Returns dict {price, implied_rate, date, ts, source} or None.
    """
    # Check cache first
    cache = _load_cache()
    cached = cache.get("zq_price")
    if cached and (datetime.now(timezone.utc).timestamp() - cached.get("ts", 0)) < CACHE_TTL:
        return cached

    # Try sources in order
    for fetcher in [_fetch_yahoo, _fetch_barchart]:
        result = fetcher()
        if result:
            price = result["price"]
            implied_rate = zq_price_to_rate(price)
            entry = {
                "price": price,
                "implied_rate": implied_rate,
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "ts": datetime.now(timezone.utc).timestamp(),
                "source": result["source"],
            }
            cache["zq_price"] = entry
            _save_cache(cache)
            return entry

    return None


def get_zq_prices(days=5):
    """Get recent ZQ prices (cached). Returns list of dicts."""
    cache = _load_cache()
    return cache.get("zq_prices", [])


if __name__ == "__main__":
    result = get_zq_price()
    if result:
        print(f"ZQ price: {result['price']}")
        print(f"Implied rate: {result['implied_rate']:.3f}%")
        print(f"Source: {result['source']}")
        print(f"Date: {result['date']}")
    else:
        print("Failed to fetch ZQ price")
