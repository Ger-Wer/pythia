#!/usr/bin/env python3
"""
Stage 1 real edge test: ZQ-implied move probabilities vs Kalshi meeting-specific prices.
For each snapshot compares futures-implied P(cut)/P(hike) with market bucket prices
of the nearest upcoming FOMC meeting; the longitudinal mean gap is the hypothesis test.
DEPRECATED: Step-A diagnostic (edge_diff_diag.py) showed the cut leg saturates at
0.0 in 68/68 snapshots (front-month rate is structurally above the current fed
rate), so this module measures a structural mismatch, not a market signal. Kept
for history; the authoritative test is edge_diff_v2.py (per-meeting ZQ contract
with CME day-weighting).
ponytail: nearest-meeting only, binary-25bp ZQ approximation (same ceiling as fedwatch),
single front-month ZQ used for all meetings. Upgrade: contract-month ZQ per meeting +
full probability tree + Brier scoring.
"""

from python.evaluation.fedwatch import probability_of_cut, probability_of_hike

BUCKET_HOLD = "H0"
CUT_BUCKETS = ("C25", "C26")
HIKE_BUCKETS = ("H25", "H26")


def bucket_of(market_id: str) -> str:
    return market_id.rsplit("-", 1)[-1]


def group_meeting_buckets(markets):
    raw = {}
    for m in markets:
        if m.get("source") != "kalshi" or not m.get("end_date") or m.get("yes_price") is None:
            continue
        bucket = bucket_of(m["market_id"])
        raw.setdefault(m["end_date"], {}).setdefault(bucket, []).append(m["yes_price"])
    return {
        end_date: {b: sum(ps) / len(ps) for b, ps in buckets.items()}
        for end_date, buckets in raw.items()
    }


def nearest_meeting(meetings, as_of_date):
    upcoming = [d for d in meetings if d >= as_of_date]
    return min(upcoming) if upcoming else None


def market_probs(buckets):
    return {
        "hold": buckets.get(BUCKET_HOLD, 0.0),
        "cut": sum(buckets.get(b, 0.0) for b in CUT_BUCKETS),
        "hike": sum(buckets.get(b, 0.0) for b in HIKE_BUCKETS),
    }


def zq_probs(fed_rate, zq_rate):
    p_cut = probability_of_cut(fed_rate, zq_rate)
    p_hike = probability_of_hike(fed_rate, zq_rate)
    return {"hold": 1.0 - p_cut - p_hike, "cut": p_cut, "hike": p_hike}


def snapshot_diff(snap):
    zq = snap.get("zq_futures")
    fed_rate = snap.get("fed_funds_rate")
    if not zq or fed_rate is None:
        return None
    date = snap["date"][:10]
    meetings = group_meeting_buckets(snap.get("markets", []))
    end_date = nearest_meeting(meetings, date)
    if end_date is None:
        return None
    mkt = market_probs(meetings[end_date])
    z = zq_probs(fed_rate, zq["implied_rate"])
    return {
        "date": date,
        "meeting": end_date,
        "zq_rate": zq["implied_rate"],
        "market_cut": mkt["cut"], "zq_cut": z["cut"], "diff_cut": mkt["cut"] - z["cut"],
        "market_hike": mkt["hike"], "zq_hike": z["hike"], "diff_hike": mkt["hike"] - z["hike"],
        "market_hold": mkt["hold"], "zq_hold": z["hold"], "diff_hold": mkt["hold"] - z["hold"],
    }


def analyze_edge_diff(snapshots, min_days=20, mean_threshold=0.03, sign_ratio=0.7):
    diffs = [d for d in (snapshot_diff(s) for s in snapshots) if d is not None]
    if not diffs:
        return None
    n = len(diffs)
    stats = {}
    for key in ("diff_cut", "diff_hike", "diff_hold"):
        vals = [d[key] for d in diffs]
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        stats[key] = {
            "mean": round(mean, 4),
            "std": round(var ** 0.5, 4),
            "n": n,
            "pos_ratio": round(sum(1 for v in vals if v > 0) / n, 3),
        }
    systematic = []
    for key, s in stats.items():
        consistency = max(s["pos_ratio"], round(1 - s["pos_ratio"], 3))
        if n >= min_days and abs(s["mean"]) >= mean_threshold and consistency >= sign_ratio:
            systematic.append({"metric": key, **s})
    return {
        "days_with_diff": n,
        "stats": stats,
        "systematic": systematic,
        "verdict": "SYSTEMATIC_DIVERGENCE" if systematic else "NO_SYSTEMATIC_EDGE",
        "thresholds": {"min_days": min_days, "mean": mean_threshold, "sign_ratio": sign_ratio},
    }
