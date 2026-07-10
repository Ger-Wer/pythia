#!/usr/bin/env python3
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

NOWCAST_URL = "https://www.clevelandfed.org/-/media/Files/webcharts/inflationnowcasting/nowcast_month.json"
SERIES = ["CPI Inflation", "Core CPI Inflation", "PCE Inflation", "Core PCE Inflation"]
_TIP_RE = re.compile(r"\{br\}(\d{2}/\d{2})\{br\}(-?\d+\.?\d*)")

CACHE_DIR = Path(__file__).resolve().parent / "data"
CACHE_FILE = CACHE_DIR / "cpi_nowcast_cache.json"
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


def _fetch_raw():
    try:
        r = requests.get(NOWCAST_URL, timeout=15)
    except requests.RequestException:
        return None
    if r.ok:
        return r.json()
    return None


def _parse_nowcast(raw_list):
    result = {name: [] for name in SERIES}
    for graph in raw_list:
        for dataset in graph.get("dataset", []):
            series = dataset.get("seriesname")
            if series not in result:
                continue
            for point in dataset.get("data", []):
                match = _TIP_RE.search(point.get("tooltext", ""))
                if match:
                    result[series].append({"date": match.group(1), "data": float(match.group(2))})
    return result


def get_latest_cpi_nowcast():
    cache = _load_cache()
    cached = cache.get("latest")
    if cached and (datetime.now(timezone.utc).timestamp() - cached.get("ts", 0)) < CACHE_TTL:
        return cached
    raw = _fetch_raw()
    if raw is None:
        return None
    parsed = _parse_nowcast(raw)
    if not all(parsed[name] for name in SERIES):
        return None
    latest = {name: parsed[name][-1] for name in SERIES}
    date = latest[SERIES[0]]["date"]
    result = {
        "data": {name: latest[name]["data"] for name in SERIES},
        "date": date,
        "ts": datetime.now(timezone.utc).timestamp(),
    }
    cache["latest"] = result
    cache["history"] = parsed
    _save_cache(cache)
    return result


def get_cpi_nowcast_history(series, limit=30):
    cache = _load_cache()
    history = cache.get("history", {}).get(series)
    if history:
        return history[-limit:]
    raw = _fetch_raw()
    if raw is None:
        return []
    parsed = _parse_nowcast(raw)
    cache["history"] = parsed
    _save_cache(cache)
    return parsed.get(series, [])[-limit:]
