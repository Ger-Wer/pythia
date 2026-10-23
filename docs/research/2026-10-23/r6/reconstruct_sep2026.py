#!/usr/bin/env python3
"""R6 reconstruction: the resolved FOMC meeting of 2026-09-16.

Rebuilds point-in-time forecasts at the fixed horizons T-7d / T-3d /
T-1d from archived inputs only:

- ZQ closes: the committed fixture
  python/evaluation/tests/fixtures/zq_monthly_sep2026.json
  (the live Yahoo history of the expired contract no longer exists —
  verified 2026-10-23 — so the fixture is the honest archive);
- market baseline: the tracker's daily snapshots in the main
  checkout's python/evaluation/snapshots/ (meeting-bucket questions);
- pre-meeting rate: 3.625, the midpoint of the 3.50-3.75 target range
  maintained by the 2026-07-29 statement (archived, R4 corpus);
- resolution: +25bp — the 2026-09-16 statement raised the range to
  3.75-4.00 (archived, R4 corpus).

One resolved meeting validates the machinery, not an edge; the scores
are reported as exactly that.

Run from the repo root:
    PYTHONPATH=python python docs/research/2026-10-23/r6/reconstruct_sep2026.py
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "python"))

from evaluation.probability_model import build_forecast_record  # noqa: E402
from evaluation.score_forecasts import brier_score, log_loss, score_records  # noqa: E402
from evaluation.zq_contracts import close_on_or_before, load_fixture, move_probs  # noqa: E402

SNAPSHOTS_DIR = Path("/home/pythia/pythia/python/evaluation/snapshots")
MEETING = "2026-09-16"
RATE_BEFORE = 3.625
HORIZONS = (("T-7d", "2026-09-09"), ("T-3d", "2026-09-13"), ("T-1d", "2026-09-15"))
RESOLUTION = {"fomc-2026-09-16": {"actual": "+25", "meeting_date": MEETING}}

_BUCKET_RULES = (
    (re.compile(r"Hike rates by >25bps"), "ge_+50"),
    (re.compile(r"Hike rates by 25bps"), "+25"),
    (re.compile(r"Hike rates by 0bps"), "0"),
    (re.compile(r"Cut rates by >25bps"), "le_-50"),
    (re.compile(r"Cut rates by 25bps"), "-25"),
)


def baseline_from_snapshot(snapshot):
    """Normalised market distribution for the September 2026 meeting."""
    dist = {k: 0.0 for k in ("le_-50", "-25", "0", "+25", "ge_+50")}
    for market in snapshot.get("markets", []):
        question = market.get("question", "")
        if "September 2026 meeting" not in question:
            continue
        for pattern, key in _BUCKET_RULES:
            if pattern.search(question):
                dist[key] = float(market.get("yes_price") or 0.0)
                break
    total = sum(dist.values())
    if total <= 0:
        return None
    return {k: round(v / total, 6) for k, v in dist.items()}


def main():
    closes = load_fixture(
        REPO / "python/evaluation/tests/fixtures/zq_monthly_sep2026.json")
    records = []
    references = []
    for label, horizon_date in HORIZONS:
        close = close_on_or_before(closes["ZQU26.CBT"], horizon_date)
        close_date = max(d for d in closes["ZQU26.CBT"] if d <= horizon_date)
        next_close = close_on_or_before(closes["ZQV26.CBT"], horizon_date)
        snapshot_path = SNAPSHOTS_DIR / f"macro_{horizon_date.replace('-', '')}_0800.json"
        snapshot = json.loads(snapshot_path.read_text())
        baseline_dist = baseline_from_snapshot(snapshot)
        record = build_forecast_record(
            event_id="fomc-2026-09-16",
            question="FOMC rate decision at the 2026-09-16 meeting (change in bp)",
            meeting_iso=MEETING, horizon_label=label,
            horizon_date=horizon_date,
            forecast_ts=f"{horizon_date}T08:00:00Z",
            rate_before=RATE_BEFORE, meeting_month_close=close,
            close_date=close_date, next_month_close=next_close,
            baseline={"source": f"tracker snapshot {snapshot_path.name}",
                      "distribution": baseline_dist})
        records.append(record)
        # Reference rows: the incumbent binary model and naive hold,
        # scored with the same functions (not forecast records).
        from evaluation.zq_contracts import rate_after_meeting
        rate_after = rate_after_meeting(close, RATE_BEFORE, MEETING)
        binary = move_probs(RATE_BEFORE, rate_after)
        binary_dist = {"le_-50": 0.0, "-25": binary["cut"], "0": binary["hold"],
                       "+25": binary["hike"], "ge_+50": 0.0}
        references.append({
            "horizon": label,
            "incumbent_binary": {
                "distribution": binary_dist,
                "brier": round(brier_score(binary_dist, "+25"), 6),
                "log_loss": round(log_loss(binary_dist, "+25"), 6),
            },
            "naive_hold": {
                "brier": round(brier_score(
                    {"le_-50": 0.0, "-25": 0.0, "0": 1.0, "+25": 0.0,
                     "ge_+50": 0.0}, "+25"), 6),
                "log_loss": round(log_loss(
                    {"le_-50": 0.0, "-25": 0.0, "0": 1.0, "+25": 0.0,
                     "ge_+50": 0.0}, "+25"), 6),
            },
        })
    out_dir = Path(__file__).resolve().parent
    (out_dir / "sep2026_forecasts.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False))
    scores = score_records(records, RESOLUTION)
    (out_dir / "sep2026_scores.json").write_text(json.dumps(
        {"scores": scores, "references": references},
        indent=2, ensure_ascii=False))
    print(json.dumps({"scores": scores, "references": references},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
