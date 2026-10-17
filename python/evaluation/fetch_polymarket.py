#!/usr/bin/env python3
"""
Stage 0: Fetch resolved macro/economic markets from Polymarket.
Searches Gamma API for FOMC, rate, CPI, GDP, inflation markets.
Fetches historical prices via CLOB prices-history endpoint.
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

MACRO_KEYWORDS = [
    "fed", "fomc", "interest rate", "rate cut", "rate hike",
    "federal funds", "inflation", "cpi", "gdp", "recession",
    "unemployment", "nonfarm", "payroll", "fed funds",
    "rate decision", "powell", "fed chair",
]

FOMC_SLUGS = [
    "fed-decision-in-january",
    "fed-decision-in-february",
    "fed-decision-in-march",
    "fed-decision-in-april",
    "fed-decision-in-may",
    "fed-decision-in-june",
    "fed-decision-in-july",
    "fed-decision-in-august",
    "fed-decision-in-september",
    "fed-decision-in-october",
    "fed-decision-in-november",
    "fed-decision-in-december",
]

OUTPUT_DIR = Path(__file__).resolve().parent / "data"
BACKOFF = [1, 3, 9]


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def _get(url: str, params: dict = None, retries: int = 3) -> list | dict | None:
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


def fetch_all_events(limit: int = 100, max_pages: int = 50) -> list:
    all_events = []
    offset = 0
    for page in range(max_pages):
        data = _get(f"{GAMMA_API}/events", {
            "limit": limit, "offset": offset, "closed": "true"
        })
        if not data or not isinstance(data, list) or len(data) == 0:
            break
        all_events.extend(data)
        offset += len(data)
        if len(data) < limit:
            break
        time.sleep(0.5)
    log(f"Fetched {len(all_events)} closed events total")

    active_events = []
    for offset in range(0, 1000, 100):
        data = _get(f"{GAMMA_API}/events", {
            "limit": 100, "offset": offset, "active": "true", "closed": "false"
        })
        if not data or not isinstance(data, list) or len(data) == 0:
            break
        active_events.extend(data)
        if len(data) < 100:
            break
        time.sleep(0.5)
    log(f"Fetched {len(active_events)} active events")
    all_events.extend(active_events)
    return all_events


def filter_macro_events(events: list) -> list:
    matched = []
    for e in events:
        title = (e.get("title") or "").lower()
        markets = e.get("markets") or []
        market_questions = " ".join(
            (m.get("question") or "").lower() for m in markets
        )
        text = title + " " + market_questions
        if any(kw in text for kw in MACRO_KEYWORDS):
            matched.append(e)
    log(f"Filtered {len(matched)} macro events from {len(events)}")
    return matched


def fetch_fomc_by_slug() -> list[dict]:
    """Fetch FOMC events by slug pattern (fallback for keyword search).

    Returns list of dicts with standardized fields:
    - source: "polymarket_slug"
    - market_id: condition_id
    - question: market question
    - yes_price: float (0-1)
    - end_date: str (YYYY-MM-DD)
    - volume: float
    - event_slug: str
    """
    results = []
    for slug in FOMC_SLUGS:
        data = _get(f"{GAMMA_API}/events", {"slug": slug})
        if not data:
            continue
        events = data if isinstance(data, list) else [data]
        for event in events:
            for market in event.get("markets", []):
                outcome_prices = market.get("outcomePrices")
                yes_price = None

                end_date_raw = market.get("endDate") or event.get("endDate") or ""
                end_date = end_date_raw[:10] if end_date_raw else ""

                outcomes_raw = market.get("outcomes", "[]")
                token_ids_raw = market.get("clobTokenIds", "[]")
                try:
                    outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                    token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
                    yes_idx = next(i for i, outcome in enumerate(outcomes) if str(outcome).lower() == "yes")
                    prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
                    yes_price = float(prices[yes_idx]) if prices else None
                except (json.JSONDecodeError, StopIteration, IndexError, TypeError, ValueError):
                    continue
                results.append({
                    "source": "polymarket_slug",
                    "market_id": market.get("conditionId", ""),
                    "question": market.get("question", ""),
                    "yes_price": yes_price,
                    "end_date": end_date,
                    "volume": float(market.get("volume") or 0),
                    "event_slug": slug,
                    "gamma_market_id": market.get("id"),
                    "outcomes": outcomes,
                    "clob_token_ids": token_ids,
                    "outcome_prices": prices,
                    "closed": market.get("closed", False),
                    "uma_resolution_status": market.get("umaResolutionStatus"),
                })
        time.sleep(0.3)
    log(f"Fetched {len(results)} FOMC markets via slug fallback")
    return results


def fetch_prices_history(token_id: str, fidelity: int = 60) -> list | None:
    data = _get(f"{CLOB_API}/prices-history", {
        "market": token_id,
        "interval": "all",
        "fidelity": fidelity,
    })
    if data and isinstance(data, dict):
        return data.get("history")
    return None


def build_dataset(events: list) -> list:
    records = []
    for ei, event in enumerate(events):
        title = event.get("title", "")
        slug = event.get("slug", "")
        end_date = event.get("endDate", "")
        markets = event.get("markets") or []

        log(f"[{ei+1}/{len(events)}] {title}")

        for m in markets:
            question = m.get("question", "")
            outcomes_raw = m.get("outcomes", "[]")
            prices_raw = m.get("outcomePrices", "[]")
            closed = m.get("closed", False)
            m_end = m.get("endDate", end_date)
            clob_ids_raw = m.get("clobTokenIds", "[]")

            try:
                outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                final_prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                clob_ids = json.loads(clob_ids_raw) if isinstance(clob_ids_raw, str) else clob_ids_raw
            except json.JSONDecodeError:
                continue

            if not clob_ids or not outcomes:
                continue

            yes_idx = None
            for i, o in enumerate(outcomes):
                if o.lower() == "yes":
                    yes_idx = i
                    break

            if yes_idx is None:
                continue
            yes_token = clob_ids[yes_idx] if yes_idx < len(clob_ids) else None
            try:
                resolved_price = float(final_prices[yes_idx]) if yes_idx < len(final_prices) else None
            except (ValueError, TypeError):
                resolved_price = None

            resolved_outcome = None
            # Closed is not resolved: halted/disputed markets may still trade at
            # near-terminal prices. Only the provider's final resolution status
            # plus an exact terminal outcome can settle a historical record.
            try:
                terminal_prices = [float(value) for value in final_prices]
                terminal_pair = (len(terminal_prices) == 2 and all(math.isfinite(value) for value in terminal_prices)
                                 and sorted(terminal_prices) == [0.0, 1.0])
            except (ValueError, TypeError):
                terminal_pair = False
            final_resolution = (closed is True
                                and str(m.get("umaResolutionStatus", "")).lower() == "resolved"
                                and terminal_pair)
            if final_resolution and resolved_price is not None:
                if resolved_price == 1.0:
                    resolved_outcome = "YES"
                elif resolved_price == 0.0:
                    resolved_outcome = "NO"

            price_history = None
            if yes_token:
                log(f"  Fetching price history for: {question[:60]}")
                price_history = fetch_prices_history(yes_token)
                if price_history:
                    log(f"    Got {len(price_history)} price points")
                else:
                    log(f"    No price history")
                time.sleep(0.5)

            records.append({
                "event_title": title,
                "event_slug": slug,
                "question": question,
                "market_id": m.get("id"),
                "end_date": m_end,
                "closed": closed,
                "resolved_outcome": resolved_outcome,
                "resolved_price": resolved_price,
                "resolution_status": m.get("umaResolutionStatus"),
                "condition_id": m.get("conditionId"),
                "yes_token_id": yes_token,
                "price_history": price_history,
                "volume": float(m.get("volumeNum", 0) or 0),
            })

    return records


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log("Fetching all closed events...")
    events = fetch_all_events()

    log("Filtering macro/economic events...")
    macro_events = filter_macro_events(events)

    # Fallback: if keyword search found 0 FOMC-related events, try slug discovery
    fomc_count = sum(
        1 for e in macro_events
        if any(kw in (e.get("title") or "").lower() for kw in ["fomc", "fed", "rate decision"])
    )
    if fomc_count == 0:
        log("No FOMC events found via keywords, trying slug fallback...")
        slug_markets = fetch_fomc_by_slug()
        if slug_markets:
            for sm in slug_markets:
                macro_events.append({
                    "title": sm["question"],
                    "slug": sm["event_slug"],
                    "markets": [{
                        "question": sm["question"],
                        "conditionId": sm["market_id"],
                        "id": sm["gamma_market_id"],
                        "outcomePrices": json.dumps(sm["outcome_prices"]),
                        "outcomes": json.dumps(sm["outcomes"]),
                        "clobTokenIds": json.dumps(sm["clob_token_ids"]),
                        "closed": sm["closed"],
                        "umaResolutionStatus": sm["uma_resolution_status"],
                        "endDate": sm["end_date"],
                        "volumeNum": sm["volume"],
                    }],
                })
            log(f"Added {len(slug_markets)} slug-based FOMC markets to dataset")

    for e in macro_events[:10]:
        title = e.get("title", "")
        slug = e.get("slug", "")
        mkts = len(e.get("markets", []))
        log(f"  {title}  (slug: {slug}, markets: {mkts})")

    log("Building dataset with price histories...")
    dataset = build_dataset(macro_events)

    output_file = OUTPUT_DIR / "macro_markets.json"
    with open(output_file, "w") as f:
        json.dump(dataset, f, indent=2)

    log(f"Saved {len(dataset)} markets to {output_file}")

    summary = [r for r in dataset if r["price_history"]]
    log(f"Markets with price history: {len(summary)}")
    for r in summary[:10]:
        log(f"  {r['question'][:70]} | resolved: {r['resolved_outcome']} | pts: {len(r['price_history'])}")


if __name__ == "__main__":
    main()
