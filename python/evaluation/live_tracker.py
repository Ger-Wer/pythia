#!/usr/bin/env python3
"""
Stage 1: Daily snapshot of active macro markets (FOMC + GDP/recession).
Compares Polymarket prices vs FRED baselines (fed funds rate, GDP growth).
Stores daily snapshots for longitudinal analysis.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from python.evaluation.fred_data import get_latest_gdp_growth
from python.evaluation.fedwatch import get_current_rate
from python.evaluation.gdpnow import get_latest_gdpnow
from python.evaluation.cpi_nowcast import get_latest_cpi_nowcast
from python.evaluation.zq_futures import get_zq_price

GAMMA_API = "https://gamma-api.polymarket.com"
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
BACKOFF = [1, 3, 9]

# Each bucket: keywords to match + tag stored in snapshot.
MARKET_BUCKETS = {
    "fomc": ["rate cut", "rate hike", "fomc", "interest rate", "fed rate", "fed funds"],
    "gdp":  ["gdp", "recession", "economic growth", "gross domestic", "negative gdp"],
}


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


def fetch_active_markets():
    """Fetch all active Polymarket events and bucket markets by keyword match.

    Returns list of dicts: {market fields... + "category": bucket_name}.
    """
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

    bucketed = {cat: [] for cat in MARKET_BUCKETS}
    for e in events:
        mkts = e.get("markets") or []
        for m in mkts:
            q = (m.get("question") or "").lower()
            for cat, keywords in MARKET_BUCKETS.items():
                if any(kw in q for kw in keywords):
                    m_copy = dict(m)
                    m_copy["_category"] = cat
                    bucketed[cat].append(m_copy)
                    break  # assign to first matching bucket only

    for cat, mkts in bucketed.items():
        log(f"  {cat}: {len(mkts)} markets")
    return bucketed


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
            except (ValueError, TypeError) as exc:
                log(f"  Could not parse YES price for sector {m.obtain('id')}: {exc}")
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

    log("Fetching baselines from FRED...")
    fed_funds_rate = get_current_rate()
    if fed_funds_rate:
        log(f"  Fed funds rate: {fed_funds_rate}%")
    else:
        log("  WARNING: Could not fetch fed funds rate from FRED")

    gdp_growth = get_latest_gdp_growth()
    if gdp_growth:
        log(f"  GDP growth: {gdp_growth['value']}% ({gdp_growth['date']})")
    else:
        log("  WARNING: Could not fetch GDP growth from FRED")

    log("Fetching new data sources...")
    gdpnow = get_latest_gdpnow()
    if gdpnow:
        log(f"  GDPNow: {gdpnow['value']}% ({gdpnow['date']})")
    else:
        log("  WARNING: Could not fetch GDPNow estimate")

    cpi_nowcast = get_latest_cpi_nowcast()
    if cpi_nowcast:
        log(f"  CPI nowcast: {cpi_nowcast['date']}")
    else:
        log("  WARNING: Could not fetch CPI nowcast")

    zq_price = get_zq_price()
    if zq_price:
        log(f"  ZQ price: {zq_price['price']} (implied rate: {zq_price['implied_rate']:.3f}%)")
    else:
        log("  WARNING: Could not fetch ZQ futures price")

    log("Fetching active macro markets...")
    buckets = fetch_active_markets()

    snapshots = []
    for cat, markets in buckets.items():
        for m in markets:
            snap = snapshot_market(m)
            if snap:
                snap["category"] = cat
                if fed_funds_rate:
                    snap["fed_funds_rate"] = fed_funds_rate
                if gdp_growth:
                    snap["gdp_growth"] = gdp_growth["value"]
                snapshots.append(snap)

    log(f"Captured {len(snapshots)} market snapshots across {len(buckets)} categories")

    now = datetime.now(timezone.utc)
    filename = f"macro_{now.strftime('%Y%m%d_%H%M')}.json"
    output_file = SNAPSHOT_DIR / filename

    daily_data = {
        "date": now.isoformat(),
        "fed_funds_rate": fed_funds_rate,
        "gdp_growth": gdp_growth["value"] if gdp_growth else None,
        "gdp_growth_date": gdp_growth["date"] if gdp_growth else None,
        "gdpnow": gdpnow["value"] if gdpnow else None,
        "gdpnow_date": gdpnow["date"] if gdpnow else None,
        "cpi_nowcast": cpi_nowcast["data"] if cpi_nowcast else None,
        "cpi_nowcast_date": cpi_nowcast["date"] if cpi_nowcast else None,
        "zq_futures": {
            "price": zq_price["price"],
            "implied_rate": zq_price["implied_rate"],
            "source": zq_price["source"],
        } if zq_price else None,
        "markets": snapshots,
        "total_markets": len(snapshots),
        "categories": {cat: len(mkts) for cat, mkts in buckets.items()},
    }

    with open(output_file, "w") as f:
        json.dump(daily_data, f, indent=2)

    log(f"Saved to {output_file}")

    for s in snapshots[:10]:
        price_str = f"{s['yes_price']:.3f}" if s['yes_price'] is not None else "N/A"
        log(f"  [{s['category']:6s}] {s['question'][:60]} | P(YES)={price_str} | vol={s['volume']:.0f}")

    log(f"\n=== STAGE 1 STATUS ===")
    log(f"Fed funds rate: {fed_funds_rate}% | GDP growth: {gdp_growth['value'] if gdp_growth else 'N/A'}%")
    log(f"Markets tracked: {len(snapshots)} ({', '.join(f'{c}={len(m)}' for c, m in buckets.items())})")
    log(f"Next: run daily to build longitudinal dataset")
    log(f"Analyze after 30+ days for edge signal")


if __name__ == "__main__":
    main()
