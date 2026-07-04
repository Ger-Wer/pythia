#!/usr/bin/env python3
"""
Stage 0: Fetch resolved macro/economic markets from Polymarket.
Searches Gamma API for FOMC, rate, CPI, GDP, inflation markets.
Fetches historical prices via CLOB prices-history endpoint.
"""

import json
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
    "unemployment", "nonfarm", "payroll",
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

            yes_idx = 0
            for i, o in enumerate(outcomes):
                if o.lower() == "yes":
                    yes_idx = i
                    break

            yes_token = clob_ids[yes_idx] if yes_idx < len(clob_ids) else None
            resolved_price = float(final_prices[yes_idx]) if yes_idx < len(final_prices) else None

            resolved_outcome = None
            if resolved_price is not None:
                if resolved_price > 0.9:
                    resolved_outcome = "YES"
                elif resolved_price < 0.1:
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
