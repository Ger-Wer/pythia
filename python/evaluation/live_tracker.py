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
from python.evaluation.fetch_kalshi import get_kalshi_fomc_markets
from python.evaluation.paper_sources import SourceError, fetch_model_inputs, next_meeting

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


def snapshot_market(m, source="polymarket"):
    if source == "kalshi":
        return snapshot_kalshi_market(m)
    return snapshot_polymarket_market(m)


def snapshot_kalshi_market(m):
    q = m.get("question", "")
    yes_price = m.get("yes_price")
    vol = m.get("volume", 0)
    end = m.get("end_date", "")
    market_id = m.get("market_id", "")

    return {
        "question": q,
        "market_id": market_id,
        "yes_price": yes_price,
        "volume": vol,
        "end_date": end,
        "clob_token_id": None,
        "source": "kalshi",
        "fetched_at": m.get("fetched_at"),
        "yes_bid": m.get("yes_bid"),
        "yes_ask": m.get("yes_ask"),
    }


def snapshot_polymarket_market(m):
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
    yes_idx = None
    for i, o in enumerate(outcomes):
        if o.lower() == "yes" and i < len(prices):
            yes_idx = i
            try:
                yes_price = float(prices[i])
            except (ValueError, TypeError) as exc:
                log(f"  Could not parse YES price for market {m.get('id')}: {exc}")
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
        "clob_token_id": clob_ids[yes_idx] if yes_idx is not None and yes_idx < len(clob_ids) else None,
        "condition_id": m.get("conditionId"),
        "outcomes": outcomes,
        "clob_token_ids": clob_ids,
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
        log(f"  GDPNow: {gdpnow['data']}% ({gdpnow['date']})")
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

    log("Fetching Kalshi FOMC markets...")
    kalshi_markets = get_kalshi_fomc_markets()
    if kalshi_markets:
        buckets["kalshi_fomc"] = kalshi_markets
        log(f"  Kalshi: {len(kalshi_markets)} meeting-specific markets")
    else:
        log("  WARNING: Could not fetch Kalshi FOMC markets")

    snapshots = []
    for cat, markets in buckets.items():
        source = "kalshi" if cat == "kalshi_fomc" else "polymarket"
        for m in markets:
            snap = snapshot_market(m, source=source)
            if snap:
                snap["category"] = cat
                if fed_funds_rate:
                    snap["fed_funds_rate"] = fed_funds_rate
                if gdp_growth:
                    snap["gdp_growth"] = gdp_growth["value"]
                snapshots.append(snap)

    log(f"Captured {len(snapshots)} market snapshots across {len(buckets)} categories")

    model_inputs = None
    model_inputs_error = None
    try:
        model_inputs = fetch_model_inputs(next_meeting())
    except (SourceError, KeyError, TypeError, ValueError) as exc:
        model_inputs_error = str(exc)
        log(f"  Model inputs unavailable; no paper forecast: {model_inputs_error}")
    now = datetime.now(timezone.utc)
    filename = f"macro_{now.strftime('%Y%m%d_%H%M')}.json"
    output_file = SNAPSHOT_DIR / filename

    daily_data = {
        "date": now.isoformat(),
        "fed_funds_rate": fed_funds_rate,
        "fed_funds_rate_series": "FEDFUNDS_MONTHLY_LEGACY",
        "model_inputs": model_inputs,
        "model_inputs_error": model_inputs_error,
        "effr": model_inputs["effr"] if model_inputs else None,
        "gdp_growth": gdp_growth["value"] if gdp_growth else None,
        "gdp_growth_date": gdp_growth["date"] if gdp_growth else None,
        "gdpnow": gdpnow["data"] if gdpnow else None,
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
