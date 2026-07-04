#!/usr/bin/env python3
"""
Stage 1: Daily snapshot of active FOMC markets.
Compares Polymarket prices vs ZQ-implied probabilities (baseline).
Stores daily snapshots for longitudinal analysis.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from python.backtest.fred_data import get_latest_fed_funds, get_all_indicators
from python.backtest.fedwatch import get_current_rate, probability_of_cut, zq_price_to_rate

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
FOMC_KEYWORDS = ["rate cut", "rate hike", "fomc", "interest rate", "fed rate", "fed funds"]
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
BACKOFF = [1, 3, 9]


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def _get(url: str, params: dict = None, retries: int = 3):
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = BACKOFF[min(attempt, len(BACKOFF) - 1)]
                log(f"  429 rate limit, waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
            else:
                log(f"  Request failed: {e}")
                return None


def fetch_active_fomc_markets():
    events = []
    for offset in range(0, 1000, 100):
        data = _get(f"{GAMMA_API}/events", {
            "limit": 100, "offset": offset, "active": "true", "closed": "false"
        })
        if not data or not isinstance(data, list) or len(data) == 0:
            break
        events.extend(data)
        if len(data) < 100:
            break
        time.sleep(0.3)

    log(f"Fetched {len(events)} active events")

    fomc_markets = []
    for e in events:
        title = (e.get("title") or "").lower()
        mkts = e.get("markets") or []
        qs = " ".join((m.get("question") or "").lower() for m in mkts)
        text = title + " " + qs

        if not any(kw in text for kw in FOMC_KEYWORDS):
            continue

        for m in mkts:
            q = (m.get("question") or "").lower()
            if any(kw in q for kw in FOMC_KEYWORDS):
                fomc_markets.append(m)

    log(f"Found {len(fomc_markets)} active FOMC/rate markets")
    return fomc_markets


def snapshot_market(m):
    q = m.get("question", "")
    outcomes_raw = m.get("outcomes", "[]")
    prices_raw = m.get("outcomePrices", "[]")
    vol = m.get("volumeNum", 0)
    end = m.get("endDate", "")

    try:
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
    except json.JSONDecodeError:
        return None

    yes_price = None
    for i, o in enumerate(outcomes):
        if o.lower() == "yes" and i < len(prices):
            try:
                yes_price = float(prices[i])
            except (ValueError, TypeError):
                pass
            break

    clob_ids_raw = m.get("clobTokenIds", "[]")
    try:
        clob_ids = json.loads(clob_ids_raw) if isinstance(clob_ids_raw, str) else clob_ids_raw
    except json.JSONDecodeError:
        clob_ids = []

    return {
        "question": q,
        "market_id": m.get("id"),
        "yes_price": yes_price,
        "volume": vol,
        "end_date": end[:10] if end else None,
        "clob_token_id": clob_ids[0] if clob_ids else None,
    }


def main():
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    log("Fetching current fed funds rate from FRED...")
    fed_funds_rate = get_current_rate()
    if fed_funds_rate:
        log(f"  Current fed funds rate: {fed_funds_rate}%")
    else:
        log("  WARNING: Could not fetch fed funds rate from FRED")

    log("Fetching active FOMC markets...")
    markets = fetch_active_fomc_markets()

    snapshots = []
    for m in markets:
        snap = snapshot_market(m)
        if snap:
            if fed_funds_rate:
                snap["fed_funds_rate"] = fed_funds_rate
            snapshots.append(snap)

    log(f"Captured {len(snapshots)} market snapshots")

    now = datetime.now(timezone.utc)
    filename = f"fomc_{now.strftime('%Y%m%d_%H%M')}.json"
    output_file = SNAPSHOT_DIR / filename

    daily_data = {
        "date": now.isoformat(),
        "fed_funds_rate": fed_funds_rate,
        "markets": snapshots,
        "total_markets": len(snapshots),
    }

    with open(output_file, "w") as f:
        json.dump(daily_data, f, indent=2)

    log(f"Saved to {output_file}")

    for s in snapshots[:10]:
        price_str = f"{s['yes_price']:.3f}" if s['yes_price'] is not None else "N/A"
        log(f"  {s['question'][:70]} | P(YES)={price_str} | vol={s['volume']:.0f}")

    log(f"\n=== STAGE 1 STATUS ===")
    log(f"Current fed funds rate: {fed_funds_rate}%")
    log(f"Active FOMC markets tracked: {len(snapshots)}")
    log(f"Next: run daily to build longitudinal dataset")
    log(f"Analyze after 30+ days for edge signal")


if __name__ == "__main__":
    main()
