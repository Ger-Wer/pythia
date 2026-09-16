#!/usr/bin/env python3
"""
Stage 1 edge test v2: per-meeting ZQ vs Kalshi meeting-specific prices.

v1 (edge_diff.py) compared meeting-specific Kalshi buckets against a
front-month ZQ rate through a binary-25bp clamp. Step-A diagnostic
(edge_diff_diag.py) showed the cut leg saturated at 0.0 in 68/68 snapshots,
so v1 measures a structural mismatch, not a market signal. v1 is kept for
history but demoted to informational; THIS module is the authoritative test.

v2 extracts the post-meeting rate from the meeting-month ZQ contract with
CME day-weighting (see zq_contracts.py) and compares like-for-like.

Coverage note: the July 2026 meeting is UNRECOVERABLE — ZQM26/ZQN26 are
delisted and Yahoo keeps no history for delisted contracts (verified
2026-09-16). v2 covers the September 2026 meeting onward (n=49 in the
current corpus: 2026-07-30 -> 2026-09-16).

ponytail: binary-25bp move model kept; nearest-meeting only. Upgrade: full
probability tree + Brier scoring.
"""

from python.evaluation.edge_diff import (
    group_meeting_buckets,
    market_probs,
    nearest_meeting,
)
from python.evaluation.zq_contracts import (
    close_on_or_before,
    meeting_contract,
    move_probs,
    rate_after_meeting,
)


def snapshot_diff_v2(snap, closes):
    """Per-meeting diff for one snapshot.

    Returns (diff_dict, None) on success or (None, skipped_reason).
    """
    fed_rate = snap.get("fed_funds_rate")
    if fed_rate is None:
        return None, "no fed rate"
    date = snap["date"][:10]
    meetings = group_meeting_buckets(snap.get("markets", []))
    end_date = nearest_meeting(meetings, date)
    if end_date is None:
        return None, "no upcoming Kalshi meeting"
    contract = meeting_contract(end_date)
    series = closes.get(contract)
    if not series:
        return None, f"no ZQ history for {contract}"
    price = close_on_or_before(series, date)
    if price is None:
        return None, f"no {contract} close on/before {date}"
    rate_after = rate_after_meeting(price, fed_rate, end_date)
    if rate_after is None:
        return None, "meeting on last day of month"
    z = move_probs(fed_rate, rate_after)
    mkt = market_probs(meetings[end_date])
    return {
        "date": date,
        "meeting": end_date,
        "contract": contract,
        "contract_price": price,
        "rate_before": fed_rate,
        "rate_after": round(rate_after, 4),
        "market_cut": mkt["cut"], "zq_cut": z["cut"], "diff_cut": mkt["cut"] - z["cut"],
        "market_hike": mkt["hike"], "zq_hike": z["hike"], "diff_hike": mkt["hike"] - z["hike"],
        "market_hold": mkt["hold"], "zq_hold": z["hold"], "diff_hold": mkt["hold"] - z["hold"],
    }, None


def analyze_edge_diff_v2(diffs, min_days=20, mean_threshold=0.03, sign_ratio=0.7):
    """Same verdict logic as v1, over per-meeting diffs."""
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
    meetings = sorted({d["meeting"] for d in diffs})
    return {
        "days_with_diff": n,
        "meetings": meetings,
        "stats": stats,
        "systematic": systematic,
        "verdict": "SYSTEMATIC_DIVERGENCE" if systematic else "NO_SYSTEMATIC_EDGE",
        "thresholds": {"min_days": min_days, "mean": mean_threshold, "sign_ratio": sign_ratio},
    }


def reconstruct(snapshots, closes):
    """Run v2 over stored snapshots. Returns (result_or_None, skipped_list)."""
    diffs = []
    skipped = []
    for snap in snapshots:
        d, reason = snapshot_diff_v2(snap, closes)
        if d is None:
            skipped.append({"date": snap["date"][:10], "reason": reason})
        else:
            diffs.append(d)
    result = analyze_edge_diff_v2(diffs)
    if result is not None:
        result["skipped"] = skipped
    return result, skipped
