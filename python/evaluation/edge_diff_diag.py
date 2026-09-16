#!/usr/bin/env python3
"""
Step A diagnostic for edge_diff: is SYSTEMATIC_DIVERGENCE a real market signal
or an artifact of the binary-25bp saturation / front-month ZQ roll?

Read-only. Does not change edge_diff verdicts; only reports how often the
pipeline saturates and how much the front-month rate jumps day-over-day.

ponytail: diagnostic only, nearest-meeting + single front-month ceiling stays.
Upgrade path is the same as edge_diff: per-meeting ZQ contracts.
"""

import json
from pathlib import Path

from python.evaluation.edge_diff import snapshot_diff

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"

# A day-over-day change in the front-month implied rate at or above this is a
# roll/repricing event, not a gradual market move. 0.10 on a 25bp model is
# enough to swing P(hike) by 0.4.
ROLL_THRESHOLD = 0.10


def _saturation(value: float) -> str:
    if value <= 0.0:
        return "floor"
    if value >= 1.0:
        return "ceil"
    return ""


def diagnose(diffs):
    n = len(diffs)
    # p_cut/p_hike saturate against the [0,1] clamp; hold is derived and can go
    # negative when both clamp at 1.0.
    cut_sat = [d for d in diffs if _saturation(d["zq_cut"])]
    hike_sat = [d for d in diffs if _saturation(d["zq_hike"])]
    either_sat = [d for d in diffs if _saturation(d["zq_cut"]) or _saturation(d["zq_hike"])]
    negative_hold = [d for d in diffs if d["zq_hold"] < 0.0]

    # Day-over-day jumps in the front-month implied rate.
    rolls = []
    for prev, cur in zip(diffs, diffs[1:]):
        delta = cur["zq_rate"] - prev["zq_rate"]
        if abs(delta) >= ROLL_THRESHOLD:
            rolls.append({"date": cur["date"], "delta": round(delta, 4)})

    def mean_of(key, subset):
        if not subset:
            return None
        return round(sum(d[key] for d in subset) / len(subset), 4)

    # Does diff_hike on roll days differ from non-roll days? If the artifact is
    # real, roll days should carry the extreme values.
    roll_dates = {r["date"] for r in rolls}
    on_roll = [d for d in diffs if d["date"] in roll_dates]
    off_roll = [d for d in diffs if d["date"] not in roll_dates]

    return {
        "n": n,
        "saturation": {
            "cut_floor_or_ceil": len(cut_sat),
            "hike_floor_or_ceil": len(hike_sat),
            "either": len(either_sat),
            "either_ratio": round(len(either_sat) / n, 3) if n else None,
            "negative_zq_hold": len(negative_hold),
        },
        "front_month_rolls": {
            "threshold": ROLL_THRESHOLD,
            "count": len(rolls),
            "dates": rolls,
        },
        "diff_hike_roll_vs_not": {
            "mean_on_roll_days": mean_of("diff_hike", on_roll),
            "mean_off_roll_days": mean_of("diff_hike", off_roll),
            "n_on_roll": len(on_roll),
            "n_off_roll": len(off_roll),
        },
        "saturated_examples": [
            {
                "date": d["date"],
                "zq_rate": d["zq_rate"],
                "zq_cut": d["zq_cut"],
                "zq_hike": d["zq_hike"],
                "zq_hold": d["zq_hold"],
                "diff_hike": round(d["diff_hike"], 4),
            }
            for d in either_sat[:10]
        ],
    }


def main():
    files = sorted(SNAPSHOT_DIR.glob("fomc_*.json")) + sorted(
        SNAPSHOT_DIR.glob("macro_*.json")
    )
    diffs = []
    for f in files:
        with open(f) as fh:
            snap = json.load(fh)
        d = snapshot_diff(snap)
        if d is not None:
            diffs.append(d)

    result = diagnose(diffs)
    print(json.dumps(result, indent=2))

    print()
    print("=== READ ===")
    sat = result["saturation"]
    print(f"snapshots with ZQ+Kalshi: {sat['either']}/{result['n']} saturated "
          f"({sat['either_ratio']})")
    print(f"negative zq_hold (clamp collision): {sat['negative_zq_hold']}")
    rv = result["diff_hike_roll_vs_not"]
    print(f"diff_hike on roll days:  {rv['mean_on_roll_days']} (n={rv['n_on_roll']})")
    print(f"diff_hike off roll days: {rv['mean_off_roll_days']} (n={rv['n_off_roll']})")


if __name__ == "__main__":
    main()