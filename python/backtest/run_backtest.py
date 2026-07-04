#!/usr/bin/env python3
"""
Stage 0: Compare Polymarket FOMC/rate prices vs ZQ-implied probabilities.
ponytail: no real ZQ data source yet — uses synthetic ZQ baseline (current fed funds rate).
Upgrade: integrate CME ZQ futures via paid data feed or CBOE delayed quotes.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "data" / "macro_markets.json"

FOMC_KEYWORDS = ["fed", "rate cut", "rate hike", "fomc", "interest rate", "fed funds"]


def load_data():
    with open(DATA_FILE) as f:
        return json.load(f)


def filter_fomc(data):
    result = []
    for m in data:
        q = (m.get("question") or "").lower()
        if any(kw in q for kw in FOMC_KEYWORDS) and m.get("price_history"):
            result.append(m)
    return result


def extract_prices_at_horizons(price_history, end_date_str, horizons=(1, 3, 7)):
    if not price_history or not end_date_str:
        return {}
    try:
        end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return {}
    result = {}
    for h in horizons:
        target = end_dt - timedelta(days=h)
        target_ts = target.timestamp() * 1000
        best = None
        best_diff = float("inf")
        for pt in price_history:
            t = pt.get("t", 0)
            diff = abs(t - target_ts)
            if diff < best_diff:
                best_diff = diff
                best = pt
        if best and best_diff < 86400000:
            result[h] = best.get("p", None)
    return result


def analyze_market(m):
    q = m.get("question", "")
    hist = m.get("price_history", [])
    end = m.get("end_date", "")
    resolved = m.get("resolved_outcome")
    vol = m.get("volume", 0)
    prices_at_h = extract_prices_at_horizons(hist, end)
    if not hist:
        return None
    first_price = hist[0].get("p") if hist else None
    last_price = hist[-1].get("p") if hist else None
    avg_price = sum(pt.get("p", 0) for pt in hist) / len(hist) if hist else None
    return {
        "question": q,
        "resolved": resolved,
        "volume": vol,
        "end_date": end[:10],
        "data_points": len(hist),
        "first_price": first_price,
        "last_price": last_price,
        "avg_price": avg_price,
        "price_at_T1": prices_at_h.get(1),
        "price_at_T3": prices_at_h.get(3),
        "price_at_T7": prices_at_h.get(7),
    }


def main():
    data = load_data()
    fomc = filter_fomc(data)
    print(f"Total markets: {len(data)}")
    print(f"FOMC/rate with history: {len(fomc)}")
    print()

    results = []
    for m in fomc:
        r = analyze_market(m)
        if r:
            results.append(r)

    resolved = [r for r in results if r["resolved"] in ("YES", "NO")]
    unresolved = [r for r in results if r["resolved"] not in ("YES", "NO")]

    print(f"Resolved: {len(resolved)}  Unresolved: {len(unresolved)}")
    print()

    if resolved:
        print("=== RESOLVED FOMC MARKETS ===")
        yes_correct = 0
        no_correct = 0
        total_pred = 0
        for r in resolved:
            avg = r["avg_price"]
            res = r["resolved"]
            predicted_yes = avg > 0.5 if avg is not None else None
            if predicted_yes is not None:
                total_pred += 1
                if res == "YES" and predicted_yes:
                    yes_correct += 1
                elif res == "NO" and not predicted_yes:
                    no_correct += 1
            print(f"  {r['question'][:80]}")
            print(f"    resolved: {res}  avg: {avg:.3f}  T7: {r['price_at_T7']}  T3: {r['price_at_T3']}  T1: {r['price_at_T1']}")
            print(f"    vol: {r['volume']:.0f}  pts: {r['data_points']}")
            print()

        if total_pred > 0:
            accuracy = (yes_correct + no_correct) / total_pred
            print(f"Market efficiency: {accuracy:.1%} ({yes_correct + no_correct}/{total_pred})")
            print(f"  YES correct: {yes_correct}  NO correct: {no_correct}")
        print()

    if unresolved:
        print("=== UNRESOLVED (ACTIVE) FOMC MARKETS ===")
        for r in unresolved:
            print(f"  {r['question'][:80]}")
            print(f"    avg: {r['avg_price']:.3f}  last: {r['last_price']:.3f}  T7: {r['price_at_T7']}  T3: {r['price_at_T3']}  T1: {r['price_at_T1']}")
            print(f"    vol: {r['volume']:.0f}  pts: {r['data_points']}  end: {r['end_date']}")
            print()

    print("=== STAGE 0 VERDICT ===")
    if len(resolved) < 5:
        print(f"INSUFFICIENT DATA: only {len(resolved)} resolved FOMC markets.")
        print("Cannot determine systematic mispricing with statistical significance.")
        print("Recommendation: proceed to Stage 1 (live paper track) with active markets.")
    else:
        avg_prices = [r["avg_price"] for r in resolved if r["avg_price"] is not None]
        extreme = [p for p in avg_prices if p < 0.15 or p > 0.85]
        print(f"Resolved markets: {len(resolved)}")
        print(f"Avg price distribution: {len(extreme)}/{len(avg_prices)} extreme (<0.15 or >0.85)")
        if len(extreme) / max(len(avg_prices), 1) > 0.7:
            print("Markets heavily one-sided — limited edge opportunity.")
        else:
            print("Sufficient price variation — potential edge exists.")


if __name__ == "__main__":
    main()
