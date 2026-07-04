#!/usr/bin/env python3
"""
Stage 1 analysis: reads daily FOMC snapshots, computes price drift,
volatility, and edge signal per market.
Run weekly via cron. Meaningful after 30+ days of data.
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
REPORT_DIR = Path(__file__).resolve().parent / "reports"


def load_snapshots():
    files = sorted(SNAPSHOT_DIR.glob("fomc_*.json"))
    snapshots = []
    for f in files:
        with open(f) as fh:
            snapshots.append(json.load(fh))
    return snapshots


def build_timeseries(snapshots):
    ts = defaultdict(list)
    for snap in snapshots:
        date = snap["date"]
        fed_rate = snap.get("fed_funds_rate")
        for m in snap.get("markets", []):
            mid = m["market_id"]
            ts[mid].append({
                "date": date,
                "yes_price": m["yes_price"],
                "volume": m["volume"],
                "question": m["question"],
                "end_date": m.get("end_date"),
                "fed_funds_rate": fed_rate,
            })
    for mid in ts:
        ts[mid].sort(key=lambda x: x["date"])
    return ts


def compute_stats(prices):
    if len(prices) < 2:
        return None
    drift = prices[-1] - prices[0]
    mean = sum(prices) / len(prices)
    variance = sum((p - mean) ** 2 for p in prices) / len(prices)
    vol = variance ** 0.5
    min_p = min(prices)
    max_p = max(prices)
    spread = max_p - min_p
    return {
        "drift": drift,
        "volatility": vol,
        "spread": spread,
        "min": min_p,
        "max": max_p,
        "mean": mean,
        "n": len(prices),
    }


def detect_edge(mid, series, threshold=0.05):
    prices = [s["yes_price"] for s in series if s["yes_price"] is not None]
    stats = compute_stats(prices)
    if not stats:
        return None
    edge = None
    if stats["volatility"] > threshold:
        edge = "high_volatility"
    elif abs(stats["drift"]) > threshold:
        edge = "drift"
    elif stats["spread"] > threshold * 2:
        edge = "wide_spread"
    return {
        "market_id": mid,
        "question": series[0]["question"],
        "stats": stats,
        "edge_type": edge,
    }


def main():
    snapshots = load_snapshots()
    if not snapshots:
        print("No snapshots found.")
        sys.exit(0)

    n_days = len(snapshots)
    first = snapshots[0]["date"][:10]
    last = snapshots[-1]["date"][:10]

    ts = build_timeseries(snapshots)
    n_markets = len(ts)

    edges = []
    no_data = 0
    for mid, series in ts.items():
        result = detect_edge(mid, series)
        if result is None:
            no_data += 1
        elif result["edge_type"]:
            edges.append(result)

    edges.sort(key=lambda x: x["stats"]["volatility"], reverse=True)

    print(f"=== Pythia Stage 1 Analysis ===")
    print(f"Period: {first} -> {last} ({n_days} days)")
    print(f"Markets tracked: {n_markets}")
    print(f"Markets with edge signal: {len(edges)}")
    print(f"Markets with <2 data points: {no_data}")
    print()

    if n_days < 7:
        print(f"WARNING: Only {n_days} day(s) of data. Need 30+ for reliable signal.")
        print("Current prices are single-point snapshots — no drift/volatility yet.")
        print()

    if edges:
        print("--- Edge Signals (sorted by volatility) ---")
        for e in edges[:15]:
            s = e["stats"]
            q = e["question"][:65]
            print(
                f"  {e['edge_type']:16s} | drift={s['drift']:+.3f} "
                f"vol={s['volatility']:.3f} spread={s['spread']:.3f} "
                f"n={s['n']} | {q}"
            )
    else:
        print("No edge signals detected yet.")

    if n_days >= 1:
        print()
        print("--- Latest Snapshot Prices ---")
        latest = snapshots[-1]
        for m in sorted(latest.get("markets", []), key=lambda x: x["volume"], reverse=True)[:10]:
            price = m["yes_price"]
            vol = m["volume"]
            q = m["question"][:65]
            print(f"  P={price:.3f} vol=${vol/1e6:.1f}M | {q}")

    if n_days >= 30:
        print()
        print("=== STAGE 1 VERDICT ===")
        if len(edges) >= 3:
            print(f"EDGE DETECTED in {len(edges)} markets. Proceed to Stage 2.")
        else:
            print("Insufficient edge. Extend observation or pivot targets.")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "period": {"first": first, "last": last, "days": n_days},
        "markets_tracked": n_markets,
        "edges": [
            {
                "market_id": e["market_id"],
                "question": e["question"],
                "edge_type": e["edge_type"],
                "stats": e["stats"],
            }
            for e in edges
        ],
    }
    report_file = REPORT_DIR / f"analysis_{last}.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved to {report_file}")


if __name__ == "__main__":
    main()
