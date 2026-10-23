#!/usr/bin/env python3
"""Pythia R6: independent forecast scorer.

Reads forecast records (pythia-forecast/1, see probability_model.py)
and resolutions, and computes Brier score and log loss for the model
distribution and for the recorded market baseline, per horizon. The
scorer shares no code with the model beyond the outcome-grid constant:
it re-derives everything from the archived records.

It also audits the records: a forecast is rejected from scoring when
its input close postdates its horizon date (look-ahead) or its
forecast timestamp is not before the meeting date.

Resolution format: {"<event_id>": {"actual": "<outcome key>",
"meeting_date": "YYYY-MM-DD"}} with outcome keys from
probability_model.OUTCOME_KEYS.

Usage:
    python -m evaluation.score_forecasts forecasts.json resolutions.json
"""

import json
import math
import sys
from datetime import date

from .probability_model import OUTCOME_KEYS

_EPS = 1e-15


def brier_score(distribution, actual):
    return sum(
        (distribution.get(k, 0.0) - (1.0 if k == actual else 0.0)) ** 2
        for k in OUTCOME_KEYS
    )


def log_loss(distribution, actual):
    p = min(max(distribution.get(actual, 0.0), _EPS), 1.0)
    return -math.log(p)


def audit_record(record):
    """Return a list of audit violations (empty = record is clean)."""
    problems = []
    inputs = record.get("inputs", {})
    close_date = inputs.get("close_date")
    horizon_date = record.get("horizon_date")
    if close_date and horizon_date and close_date > horizon_date:
        problems.append(
            f"look-ahead: input close {close_date} after horizon {horizon_date}")
    forecast_ts = record.get("forecast_ts", "")
    meeting_date = record.get("meeting_date", "")
    if forecast_ts and meeting_date and forecast_ts[:10] >= meeting_date:
        problems.append(
            f"forecast timestamp {forecast_ts} not before meeting {meeting_date}")
    total = sum(record.get("distribution", {}).values())
    if abs(total - 1.0) > 1e-6:
        problems.append(f"distribution sums to {total}, not 1")
    return problems


def score_records(forecasts, resolutions):
    rows = []
    for record in forecasts:
        event_id = record["event_id"]
        resolution = resolutions.get(event_id)
        if resolution is None:
            continue
        actual = resolution["actual"]
        problems = audit_record(record)
        row = {
            "event_id": event_id,
            "horizon": record["horizon"],
            "actual": actual,
            "audit_violations": problems,
        }
        if not problems:
            row["model"] = {
                "brier": round(brier_score(record["distribution"], actual), 6),
                "log_loss": round(log_loss(record["distribution"], actual), 6),
            }
            baseline = record.get("baseline")
            if baseline and baseline.get("distribution"):
                bd = baseline["distribution"]
                row["baseline"] = {
                    "source": baseline.get("source"),
                    "brier": round(brier_score(bd, actual), 6),
                    "log_loss": round(log_loss(bd, actual), 6),
                }
        rows.append(row)
    return rows


def main(argv):
    with open(argv[1]) as f:
        forecasts = json.load(f)
    with open(argv[2]) as f:
        resolutions = json.load(f)
    rows = score_records(forecasts, resolutions)
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 1 if any(r["audit_violations"] for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
