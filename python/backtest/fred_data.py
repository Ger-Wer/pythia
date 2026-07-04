#!/usr/bin/env python3
import os
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

FRED_API_KEY = os.getenv("FRED_API_KEY", "")
FRED_API = "https://api.stlouisfed.org/fred"

SERIES = {
    "fed_funds": "FEDFUNDS",
    "cpi_yoy": "CPIAUCSL",
    "gdp": "GDP",
    "unemployment": "UNRATE",
    "core_cpi": "CPILFESL",
}

CACHE_DIR = Path(__file__).resolve().parent / "data"
CACHE_FILE = CACHE_DIR / "fred_cache.json"
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


def get_series(series_id, limit=12, sort_order="desc"):
    if not FRED_API_KEY:
        return None
    r = requests.get(f"{FRED_API}/series/observations", params={
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": sort_order,
        "limit": limit,
    }, timeout=15)
    if r.ok:
        return r.json().get("observations", [])
    return None


def get_latest_fed_funds():
    cache = _load_cache()
    cached = cache.get("fed_funds")
    if cached and (datetime.now(timezone.utc).timestamp() - cached.get("ts", 0)) < CACHE_TTL:
        return cached["value"]
    obs = get_series("FEDFUNDS", limit=5)
    if obs:
        for o in obs:
            val = o.get("value", ".")
            if val != ".":
                rate = float(val)
                cache["fed_funds"] = {"value": rate, "date": o["date"], "ts": datetime.now(timezone.utc).timestamp()}
                _save_cache(cache)
                return rate
    return None


def get_all_indicators():
    result = {}
    for name, sid in SERIES.items():
        obs = get_series(sid, limit=3)
        if obs:
            for o in obs:
                if o.get("value", ".") != ".":
                    result[name] = {
                        "date": o["date"],
                        "value": float(o["value"]),
                        "series_id": sid,
                    }
                    break
    return result


if __name__ == "__main__":
    indicators = get_all_indicators()
    for name, info in indicators.items():
        print(f"  {name}: {info['value']} ({info['date']}) [{info['series_id']}]")
    ffr = get_latest_fed_funds()
    print(f"\nCurrent fed funds rate: {ffr}%")
