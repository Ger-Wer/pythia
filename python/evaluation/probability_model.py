#!/usr/bin/env python3
"""Pythia R6: verifiable FOMC probability model.

Frozen version: pythia-probability/1.0

Method (nearest meeting only). The meeting-month ZQ contract price is a
day-weighted average of the pre- and post-meeting rates within the
delivery month. Given the pre-meeting rate (midpoint of the target
range in force, public information), the post-meeting rate is solved
exactly with the CME day-weighting already implemented in
zq_contracts.rate_after_meeting. The expected move (post minus pre, in
basis points) is then spread over a fixed outcome grid
{<=-50, -25, 0, +25, >=+50} by linear interpolation between adjacent
grid points; mass beyond the grid lands in the edge buckets. This is
the standard FedWatch-style reading of the futures curve, with the
interpolation rule stated instead of hidden.

The following month's contract is fetched and recorded as a diagnostic
only: it prices the combined path of two meetings and does not
identify the nearest meeting's move on its own.

Refusals (ForecastRefused, never a fabricated distribution):
- no close on or before the horizon date;
- the close is stale (more than MAX_CLOSE_AGE_DAYS before the horizon);
- the close is dated after the horizon (look-ahead in the inputs);
- the meeting falls on the last day of its month (the day-weighting
  has no post-meeting days to solve for).

Every forecast is an immutable record (build_forecast_record) scored
later by the independent scorer in score_forecasts.py. Records carry
usable_for_forecast=False: this stage validates machinery, it does
not publish forecasts.
"""

from datetime import date

from .zq_contracts import rate_after_meeting

MODEL_VERSION = "pythia-probability/1.0"

OUTCOME_KEYS = ("le_-50", "-25", "0", "+25", "ge_+50")
_GRID_BP = (-50.0, -25.0, 0.0, 25.0, 50.0)
MAX_CLOSE_AGE_DAYS = 3


class ForecastRefused(Exception):
    """The model refuses to issue a distribution for these inputs."""


def distribution_from_expected_move(delta_bp):
    """Spread an expected move (bp) over the fixed outcome grid.

    Linear interpolation between the two adjacent grid points; moves
    beyond the grid put all mass in the nearest edge bucket.
    """
    dist = {k: 0.0 for k in OUTCOME_KEYS}
    if delta_bp <= _GRID_BP[0]:
        dist[OUTCOME_KEYS[0]] = 1.0
        return dist
    if delta_bp >= _GRID_BP[-1]:
        dist[OUTCOME_KEYS[-1]] = 1.0
        return dist
    for i in range(len(_GRID_BP) - 1):
        lo, hi = _GRID_BP[i], _GRID_BP[i + 1]
        if lo <= delta_bp <= hi:
            frac = (delta_bp - lo) / (hi - lo)
            dist[OUTCOME_KEYS[i]] = 1.0 - frac
            dist[OUTCOME_KEYS[i + 1]] = frac
            return dist
    raise AssertionError("unreachable: grid covers all finite moves")


def fomc_distribution(*, rate_before, meeting_iso, meeting_month_close,
                      close_date, horizon_date):
    """Distribution over the meeting outcome from one contract close.

    rate_before: midpoint of the target range in force (percent).
    meeting_month_close: ZQ price (e.g. 96.35) of the meeting-month
    contract, observed on close_date (ISO). horizon_date (ISO) is the
    forecast's point in time; the close must not postdate it.
    Returns (distribution, diagnostics).
    """
    close_d = date.fromisoformat(close_date)
    horizon_d = date.fromisoformat(horizon_date)
    if close_d > horizon_d:
        raise ForecastRefused(
            f"look-ahead: close {close_date} is after horizon {horizon_date}")
    if (horizon_d - close_d).days > MAX_CLOSE_AGE_DAYS:
        raise ForecastRefused(
            f"stale close: {close_date} is more than {MAX_CLOSE_AGE_DAYS} "
            f"days before horizon {horizon_date}")
    rate_after = rate_after_meeting(meeting_month_close, rate_before, meeting_iso)
    if rate_after is None:
        raise ForecastRefused(
            "meeting on the last day of its month: day-weighting cannot "
            "solve for the post-meeting rate")
    delta_bp = (rate_after - rate_before) * 100.0
    diagnostics = {
        "rate_before_pct": rate_before,
        "rate_after_pct": round(rate_after, 6),
        "expected_move_bp": round(delta_bp, 3),
        "meeting_month_close": meeting_month_close,
        "close_date": close_date,
    }
    return distribution_from_expected_move(delta_bp), diagnostics


def build_forecast_record(*, event_id, question, meeting_iso, horizon_label,
                          horizon_date, forecast_ts, rate_before,
                          meeting_month_close, close_date,
                          next_month_close=None, baseline=None):
    """Assemble the immutable forecast record (schema pythia-forecast/1).

    baseline, when given, is {"source": ..., "distribution": {...}} —
    the market's distribution for the same question at the same
    instant, recorded alongside so the scorer never has to trust a
    number it cannot recompute.
    """
    distribution, diagnostics = fomc_distribution(
        rate_before=rate_before, meeting_iso=meeting_iso,
        meeting_month_close=meeting_month_close, close_date=close_date,
        horizon_date=horizon_date)
    record = {
        "schema": "pythia-forecast/1",
        "model_version": MODEL_VERSION,
        "usable_for_forecast": False,
        "event_id": event_id,
        "question": question,
        "meeting_date": meeting_iso,
        "horizon": horizon_label,
        "horizon_date": horizon_date,
        "forecast_ts": forecast_ts,
        "inputs": {
            "rate_before_pct": rate_before,
            "meeting_month_close": meeting_month_close,
            "close_date": close_date,
            "next_month_close_diagnostic": next_month_close,
        },
        "diagnostics": diagnostics,
        "distribution": distribution,
        "baseline": baseline,
    }
    return record
