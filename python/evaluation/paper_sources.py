"""Timestamped public inputs for the conditional, nearest-FOMC paper model.

This is an explicitly restricted hypothesis, not a calibrated probability
distribution or a claim of trading edge. No fixture or stale-cache fallback.
"""

import calendar
import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

NYFED = "https://markets.newyorkfed.org/api/rates/unsecured/effr"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
CALENDAR_SOURCE = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
# Scheduled decision dates, verified against the official 2026 calendar.
MEETINGS_2026 = (
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
)
MAX_OBSERVATION_AGE = timedelta(hours=96)
MAX_FETCH_AGE = timedelta(seconds=60)
MONTH_CODES = "FGHJKMNQUVXZ"


class SourceError(ValueError):
    """An unavailable, stale, ambiguous, or incompatible public input."""


def utc_time(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SourceError("Invalid source timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SourceError("Timestamp must have a timezone")
    return value.astimezone(timezone.utc)


def finite_number(value, name):
    if isinstance(value, bool):
        raise SourceError(f"Invalid {name}")
    try:
        value = float(value)
    except (ValueError, TypeError) as exc:
        raise SourceError(f"Invalid {name}") from exc
    if not math.isfinite(value):
        raise SourceError(f"Non-finite {name}")
    return value


def _fetch_json(url, params=None):
    try:
        response = requests.get(
            url, params=params, timeout=15, headers={"User-Agent": "Pythia-paper/1.0"}
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise SourceError("Public model input response is not an object")
        return payload, datetime.now(timezone.utc)
    except (requests.RequestException, ValueError) as exc:
        # Do not include a response body, credentials, or request URL in errors.
        raise SourceError("Public model input request failed") from exc


def _fresh(timestamp, now, max_age, name):
    timestamp = utc_time(timestamp)
    age = now - timestamp
    if age < timedelta(0) or age > max_age:
        raise SourceError(f"Future or stale {name}")
    return timestamp


def next_meeting(now=None):
    now = utc_time(now or datetime.now(timezone.utc))
    if now.year != 2026:
        raise SourceError("Official meeting calendar needs an update")
    for meeting in MEETINGS_2026:
        # No forecasts at or after the scheduled 14:00 New York statement.
        cutoff = datetime.combine(
            date.fromisoformat(meeting), time(14), ZoneInfo("America/New_York")
        ).astimezone(timezone.utc)
        if now < cutoff:
            return meeting
    raise SourceError("No supported upcoming FOMC meeting")


def validate_model_inputs(inputs, as_of):
    """Validate stored inputs relative to their containing snapshot, not today."""
    as_of = utc_time(as_of)
    if inputs.get("model") != "zq-effr-25bp-v1":
        raise SourceError("Unsupported model inputs")
    meeting = inputs.get("meeting")
    if meeting != next_meeting(as_of):
        raise SourceError("Inputs do not describe the nearest scheduled meeting")
    effr, zq = inputs["effr"], inputs["zq"]
    for name, value in (("EFFR", effr), ("ZQ", zq)):
        _fresh(value["fetched_at"], as_of, MAX_FETCH_AGE, f"{name} fetch")
    _fresh(zq["asof"], as_of, MAX_OBSERVATION_AGE, "ZQ observation")
    if utc_time(zq["asof"]) > utc_time(zq["fetched_at"]):
        raise SourceError("ZQ observation postdates its retrieval")
    effective = date.fromisoformat(effr["effective_date"])
    _fresh(datetime.combine(effective, time.min, timezone.utc), as_of,
           MAX_OBSERVATION_AGE, "EFFR observation")
    rate = finite_number(effr["rate"], "EFFR")
    price = finite_number(zq["price"], "ZQ price")
    rate_after = finite_number(inputs["rate_after"], "post-meeting rate")
    if not 0 <= rate <= 30 or not 50 <= price <= 110:
        raise SourceError("Implausible rate or futures price")
    if abs(rate_after - rate) > 0.25 + 1e-9:
        raise SourceError("Post-meeting rate exceeds the conditional 25bp model")
    probs = inputs["probabilities"]
    values = [finite_number(probs[key], f"probability {key}") for key in ("cut", "hold", "hike")]
    if any(value < 0 or value > 1 for value in values) or abs(sum(values) - 1) > 1e-8:
        raise SourceError("Invalid conditional probabilities")
    expected_move = 0.25 * (values[2] - values[0])
    if abs(rate_after - rate - expected_move) > 1e-8:
        raise SourceError("Probabilities do not match the stated conditional rate")
    return inputs


def fetch_model_inputs(meeting_iso, now=None):
    """Fetch fresh data; fail closed outside the narrowly supported model.

    Only the current calendar month is supported. All observed EFFR values
    in that month must be identical; the remaining pre-meeting days are
    assumed to retain that rate. Both ±25bp are not simultaneously inferred:
    the futures mean only identifies a conditional hold/one-direction model.
    """
    requested_now = utc_time(now) if now is not None else None
    start = requested_now or datetime.now(timezone.utc)
    if meeting_iso != next_meeting(start):
        raise SourceError("Only the nearest scheduled FOMC meeting is supported")
    try:
        meeting = date.fromisoformat(meeting_iso)
    except (ValueError, TypeError) as exc:
        raise SourceError("Invalid meeting date") from exc
    if (meeting.year, meeting.month) != (start.year, start.month):
        raise SourceError("Future delivery month not supported before it begins")
    previous = max((d for d in MEETINGS_2026 if d < meeting_iso), default=None)
    if previous is None or date.fromisoformat(previous).month == meeting.month:
        raise SourceError("Unsupported previous-meeting schedule")

    raw, effr_fetched = _fetch_json(f"{NYFED}/last/5.json")
    records = raw.get("refRates", [])
    if not records:
        raise SourceError("No NYFed EFFR observations")
    try:
        latest = max(records, key=lambda item: item["effectiveDate"])
        effective = date.fromisoformat(latest["effectiveDate"])
        rate = finite_number(latest["percentRate"], "EFFR")
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError("Malformed NYFed EFFR observation") from exc
    if effective <= date.fromisoformat(previous) or effective >= meeting:
        raise SourceError("EFFR does not describe the post-previous/pre-next meeting regime")

    month_start = meeting.replace(day=1)
    history, history_fetched = _fetch_json(
        f"{NYFED}/search.json",
        {"startDate": month_start.isoformat(), "endDate": effective.isoformat()},
    )
    observations = history.get("refRates", [])
    if not observations:
        raise SourceError("No month-to-date EFFR history")
    historical = {}
    try:
        for observation in observations:
            day = date.fromisoformat(observation["effectiveDate"])
            if not month_start <= day <= effective:
                raise SourceError("Out-of-window EFFR history")
            value = finite_number(observation["percentRate"], "historical EFFR")
            if day in historical and historical[day] != value:
                raise SourceError("Conflicting EFFR history")
            historical[day] = value
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError("Malformed month-to-date EFFR history") from exc
    first_weekday = month_start
    while first_weekday.weekday() >= 5:
        first_weekday += timedelta(days=1)
    if min(historical) > first_weekday or historical.get(effective) != rate:
        raise SourceError("Incomplete EFFR history")
    historical_days = sorted(historical)
    if any((b - a).days > 4 for a, b in zip(historical_days, historical_days[1:])):
        raise SourceError("Gap in month-to-date EFFR history")
    # NYFed does not publish weekends/holidays. A known stable regime is a
    # prerequisite, rather than treating a monthly average as today's rate.
    if any(abs(value - rate) > 1e-9 for value in historical.values()):
        raise SourceError("Changing month-to-date EFFR requires a daily weighted model")

    symbol = f"ZQ{MONTH_CODES[meeting.month - 1]}{meeting.year % 100:02d}.CBT"
    quote, zq_fetched = _fetch_json(YAHOO.format(symbol=symbol), {"range": "5d", "interval": "1d"})
    try:
        result = quote["chart"]["result"][0]
        meta = result["meta"]
        if meta.get("symbol") != symbol:
            raise SourceError("Yahoo returned a different contract")
        price = finite_number(meta["regularMarketPrice"], "ZQ price")
        market_time = finite_number(meta["regularMarketTime"], "ZQ timestamp")
        asof = datetime.fromtimestamp(market_time, timezone.utc)
    except (KeyError, IndexError, TypeError, OverflowError, OSError) as exc:
        raise SourceError("Malformed contract-specific ZQ quote") from exc
    # `now` is the start of collection. Requests finish after it; the forecast
    # is created at the last retrieval, never backdated to collection start.
    checked_at = max(start, effr_fetched, history_fetched, zq_fetched)
    _fresh(history_fetched, checked_at, MAX_FETCH_AGE, "EFFR history fetch")
    days = calendar.monthrange(meeting.year, meeting.month)[1]
    after_days = days - meeting.day
    if after_days <= 0:
        raise SourceError("No post-meeting days in this delivery contract")
    rate_after = ((100 - price) * days - meeting.day * rate) / after_days
    move = rate_after - rate
    if abs(move) > 0.25 + 1e-9:
        raise SourceError("Implied move exceeds the conditional binary 25bp model")
    probability = abs(move) / 0.25
    probabilities = {"cut": probability if move < 0 else 0.0,
                     "hold": 1.0 - probability,
                     "hike": probability if move > 0 else 0.0}
    inputs = {
        "model": "zq-effr-25bp-v1", "meeting": meeting_iso,
        "generated_at": checked_at.isoformat(), "calendar_source": CALENDAR_SOURCE,
        "effr": {"rate": rate, "effective_date": effective.isoformat(), "series": "EFFR",
                 "fetched_at": effr_fetched.isoformat(), "source": f"{NYFED}/last/5.json",
                 "history_source": f"{NYFED}/search.json", "month_observations": len(historical)},
        "zq": {"symbol": symbol, "price": price, "asof": asof.isoformat(),
               "fetched_at": zq_fetched.isoformat(), "source": "yahoo-regular-market-quote"},
        "rate_after": rate_after, "probabilities": probabilities,
        "assumptions": [
            "Futures-implied mean is not a uniquely identified probability distribution.",
            "Conditional hold versus one 25bp move; simultaneous cut/hike and larger moves excluded.",
            "Observed month-to-date EFFR is constant and remains constant until the scheduled meeting.",
            "Target-range move is assumed to pass through one-for-one to EFFR after decision day.",
            "Futures risk premium and intraday quote/settlement differences are not calibrated.",
            "Research paper hypothesis; profitability and forecasting calibration are unproven.",
        ],
    }
    return validate_model_inputs(inputs, checked_at)
