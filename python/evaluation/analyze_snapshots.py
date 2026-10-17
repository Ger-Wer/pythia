#!/usr/bin/env python3
"""
Stage 1 analysis: reads daily snapshots. Primary hypothesis test:
edge_diff (ZQ-implied probabilities vs Kalshi meeting prices).
Per-market drift/volatility signals are informational only.
Run weekly via cron. Meaningful after 20+ days of data.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from python.evaluation.edge_diff import analyze_edge_diff
from python.evaluation.edge_diff_v2 import analyze_edge_diff_v2, snapshot_diff_v2
from python.evaluation.edge_diff import group_meeting_buckets, market_probs
from python.evaluation.paper_sources import SourceError, finite_number, utc_time, validate_model_inputs
from python.evaluation.zq_contracts import load_fixture

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
REPORT_DIR = Path(__file__).resolve().parent / "reports"


def validated_diffs(snapshots, historical_fixture=None):
    """Never silently join historical prices to newly collected snapshots.

    Default analysis uses only timestamped sources saved in the same snapshot.
    Explicit fixture mode additionally requires an explicitly identified daily
    EFFR observation; legacy FEDFUNDS is not a substitute. Daily fixture closes
    are only admitted on the following UTC date to prevent same-day hindsight.
    """
    closes = load_fixture(historical_fixture) if historical_fixture else None
    diffs, skipped = [], []
    for snap in snapshots:
        try:
            as_of = utc_time(snap["date"])
            if closes is None:
                inputs = snap.get("model_inputs")
                if not inputs:
                    raise SourceError("No timestamped EFFR/contract inputs in snapshot")
                validate_model_inputs(inputs, as_of)
                meeting = inputs["meeting"]
                buckets = group_meeting_buckets(snap.get("markets", []))
                if meeting not in buckets:
                    raise SourceError("No corresponding Kalshi meeting")
                required = {"H0", "C25", "C26", "H25", "H26"}
                if not required.issubset(buckets[meeting]):
                    raise SourceError("Incomplete Kalshi outcome buckets")
                quotes = [m for m in snap.get("markets", [])
                          if m.get("source") == "kalshi" and m.get("end_date") == meeting]
                if any(not m.get("fetched_at") for m in quotes):
                    raise SourceError("Kalshi snapshot lacks provider-fetch timestamps")
                if any(not timedelta(0) <= as_of - utc_time(m["fetched_at"]) <= timedelta(seconds=60)
                       for m in quotes):
                    raise SourceError("Future/stale Kalshi provider fetch")
                market = market_probs(buckets[meeting])
                if (any(not isinstance(value, (int, float)) or not 0 <= value <= 1
                        for value in market.values()) or abs(sum(market.values()) - 1) > 0.10):
                    raise SourceError("Invalid/incomplete Kalshi probability mass")
                probs = inputs["probabilities"]
                diff = {"date": as_of.date().isoformat(), "meeting": meeting,
                        "contract": inputs["zq"]["symbol"],
                        "contract_price": inputs["zq"]["price"],
                        "rate_before": inputs["effr"]["rate"], "rate_after": inputs["rate_after"]}
                for outcome in ("cut", "hold", "hike"):
                    diff[f"market_{outcome}"] = market[outcome]
                    diff[f"zq_{outcome}"] = probs[outcome]
                    diff[f"diff_{outcome}"] = market[outcome] - probs[outcome]
                diffs.append(diff)
            else:
                effr = snap.get("effr")
                if (not effr or not effr.get("source") or "effective_date" not in effr
                        or effr.get("series") != "EFFR"):
                    raise SourceError("Historical snapshot has no identified daily EFFR baseline")
                effective = datetime.fromisoformat(effr["effective_date"]).replace(tzinfo=timezone.utc)
                if not timedelta(0) <= as_of - effective <= timedelta(hours=96):
                    raise SourceError("Future/stale historical EFFR")
                cutoff = (as_of.date() - timedelta(days=1)).isoformat()
                safe_closes = {}
                for symbol, series in closes.items():
                    safe_closes[symbol] = {
                        day: value for day, value in series.items()
                        if day <= cutoff and (as_of.date() - datetime.fromisoformat(day).date()).days <= 4
                    }
                historical_snap = dict(snap, fed_funds_rate=finite_number(effr["rate"], "historical EFFR"))
                diff, reason = snapshot_diff_v2(historical_snap, safe_closes)
                if diff is None:
                    raise SourceError(reason)
                diffs.append(diff)
        except (SourceError, KeyError, TypeError, ValueError) as exc:
            skipped.append({"date": snap.get("date"), "reason": str(exc)})
    result = analyze_edge_diff_v2(diffs)
    if result is not None:
        result["skipped"] = skipped
        result["mode"] = "HISTORICAL_FIXTURE_ONLY" if closes is not None else "RECORDED_LIVE_INPUTS"
        result["interpretation"] = "Price divergence diagnostic; no proof of forecasting edge or profit."
    return result, skipped


def load_snapshots():
    # Backward compat: read both legacy fomc_*.json and new macro_*.json.
    files = sorted(SNAPSHOT_DIR.glob("fomc_*.json")) + sorted(SNAPSHOT_DIR.glob("macro_*.json"))
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Analyze recorded Pythia snapshots without fixture fallback")
    parser.add_argument("--historical-fixture", type=Path,
                        help="Explicit offline historical reconstruction only; never a live forecast")
    args = parser.parse_args(argv)
    snapshots = load_snapshots()
    if not snapshots:
        print("No snapshots found.")
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        report_file = REPORT_DIR / f"analysis_{now.date().isoformat()}.json"
        report_file.write_text(json.dumps({
            "generated": now.isoformat(), "status": "UNAVAILABLE",
            "reason": "No snapshots found", "edge_diff_v2": None,
            "analysis_mode": "HISTORICAL_FIXTURE_ONLY" if args.historical_fixture else "RECORDED_LIVE_INPUTS",
        }, indent=2))
        return 2

    n_days = len(snapshots)
    snapshots.sort(key=lambda snapshot: snapshot["date"])
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
    print(f"Markets with volatility/drift signal (informational): {len(edges)}")
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
            price_text = f"{price:.3f}" if price is not None else "N/A"
            print(f"  P={price_text} vol=${vol/1e6:.1f}M | {q}")

    edge_diff = analyze_edge_diff(snapshots)
    print()
    print("=== STAGE 1 VERDICT v1 (DEPRECATED, informational only) ===")
    if edge_diff is None:
        print("No ZQ+Kalshi data in snapshots — edge-diff unavailable.")
    else:
        for key, s in edge_diff["stats"].items():
            print(
                f"  {key}: mean={s['mean']:+.4f} std={s['std']:.4f} "
                f"pos_ratio={s['pos_ratio']:.2f} n={s['n']}"
            )
        print(
            f"Verdict v1: {edge_diff['verdict']} "
            f"({edge_diff['days_with_diff']} days with ZQ+Kalshi data)"
        )

    edge_diff_v2 = None
    print()
    print("=== STAGE 1 VERDICT v2 (AUTHORITATIVE, per-meeting ZQ) ===")
    if args.historical_fixture is None or args.historical_fixture.exists():
        edge_diff_v2, skipped = validated_diffs(snapshots, args.historical_fixture)
        mode = "HISTORICAL FIXTURE ONLY" if args.historical_fixture else "RECORDED LIVE INPUTS"
        print(f"Mode: {mode}; divergence is not proof of predictive edge.")
        if edge_diff_v2 is None:
            print("No per-meeting ZQ+Kalshi overlap — v2 unavailable.")
        else:
            for key, s in edge_diff_v2["stats"].items():
                print(
                    f"  {key}: mean={s['mean']:+.4f} std={s['std']:.4f} "
                    f"pos_ratio={s['pos_ratio']:.2f} n={s['n']}"
                )
            print(f"Meetings: {', '.join(edge_diff_v2['meetings'])}")
            print(f"Skipped days: {len(skipped)}")
            print(
                f"Verdict v2: {edge_diff_v2['verdict']} "
                f"({edge_diff_v2['days_with_diff']} days with per-meeting data)"
            )
    else:
        print(f"Explicit historical fixture missing ({args.historical_fixture}) — v2 skipped.")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "status": "AVAILABLE" if edge_diff_v2 is not None else "UNAVAILABLE",
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
        "edge_diff": edge_diff,
        "edge_diff_v2": edge_diff_v2,
        "analysis_mode": "HISTORICAL_FIXTURE_ONLY" if args.historical_fixture else "RECORDED_LIVE_INPUTS",
        "v2_skipped": skipped if args.historical_fixture is None or args.historical_fixture.exists() else [],
    }
    report_file = REPORT_DIR / f"analysis_{last}.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved to {report_file}")
    return 0 if edge_diff_v2 is not None else 2


if __name__ == "__main__":
    sys.exit(main())
