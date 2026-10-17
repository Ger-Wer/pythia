#!/usr/bin/env python3
"""
Per-meeting ZQ (fed funds futures) contracts.

v1 (zq_futures.py) fetches only the continuous front-month contract (ZQ=F),
which is a monthly average rate, not a meeting probability. This module maps
an FOMC meeting to its delivery-month contract (Yahoo `.CBT` symbols, free,
no key) and extracts the post-meeting rate with the standard CME
day-weighting:

    implied(M) = (d * R_before + (dim - d) * R_after) / dim

where d = decision day-of-month, dim = days in month. Solved for R_after.

ponytail: binary-25bp move model kept (same ceiling as fedwatch); only the
rate extraction is fixed. Upgrade: full probability tree across C25/C26/H25/H26.
"""

import calendar
import json
import time
from datetime import datetime, timezone
from pathlib import Path

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={days}&interval=1d"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

CACHE_DIR = Path(__file__).resolve().parent / "data"
CACHE_TTL = 3600

# CME month codes.
MONTH_CODES = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}
CODE_BY_MONTH = {m: c for c, m in MONTH_CODES.items()}


def contract_symbol(year: int, month: int) -> str:
    """Yahoo symbol for the ZQ contract delivering in year/month."""
    return f"ZQ{CODE_BY_MONTH[month]}{year % 100:02d}.CBT"


def meeting_contract(meeting_iso: str) -> str:
    """Contract symbol for the month containing the FOMC meeting."""
    d = datetime.strptime(meeting_iso, "%Y-%m-%d").date()
    return contract_symbol(d.year, d.month)


def rate_after_meeting(contract_price: float, rate_before: float, meeting_iso: str):
    """Post-meeting rate implied by the meeting-month contract.

    Returns None when the meeting falls on the last day of the month
    (no post-meeting days left in the contract to solve for).
    """
    d = datetime.strptime(meeting_iso, "%Y-%m-%d").date()
    dim = calendar.monthrange(d.year, d.month)[1]
    after = dim - d.day
    if after <= 0:
        return None
    implied = 100.0 - contract_price
    return (implied * dim - d.day * rate_before) / after


def move_probs(rate_before: float, rate_after: float, step: float = 0.25):
    """Binary-25bp P(cut)/P(hike) from the meeting rate move, clamped [0,1]."""
    diff = rate_after - rate_before
    p_hike = max(0.0, min(1.0, diff / step))
    p_cut = max(0.0, min(1.0, -diff / step))
    return {"hold": 1.0 - p_cut - p_hike, "cut": p_cut, "hike": p_hike}


def close_on_or_before(series: dict, date_iso: str, max_age_days=None):
    """Latest available close on or before date_iso (weekends/holidays roll back)."""
    candidates = [d for d in series if d <= date_iso]
    if not candidates:
        return None
    latest = max(candidates)
    if max_age_days is not None:
        age = (datetime.fromisoformat(date_iso).date() - datetime.fromisoformat(latest).date()).days
        if age > max_age_days:
            return None
    return series[latest]


def load_fixture(path) -> dict:
    """Load {symbol: {date: close}} fixture."""
    with open(path) as f:
        return json.load(f)


def fetch_daily_closes(symbol: str, span: str = "6mo") -> dict:
    """Live daily closes for a `.CBT` symbol from Yahoo (free, no key).

    Cached in data/ for CACHE_TTL. Used by the future daily cron; the
    historical test uses committed fixtures instead (past data is fixed).
    """
    try:
        import requests
    except ImportError:
        return {}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"zq_contracts_{symbol.replace('.', '_')}.json"
    now = time.time()
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if now - cached.get("ts", 0) < CACHE_TTL:
                return cached.get("series", {})
        except (json.JSONDecodeError, OSError):
            pass
    try:
        r = requests.get(
            YAHOO_CHART.format(symbol=symbol, days=span),
            headers=HEADERS, timeout=20,
        )
        r.raise_for_status()
        res = (r.json().get("chart") or {}).get("result") or []
        if not res:
            return {}
        res = res[0]
        ts = res.get("timestamp") or []
        closes = (res.get("indicators") or {}).get("quote", [{}])[0].get("close") or []
        series = {}
        for t, c in zip(ts, closes):
            if c is None:
                continue
            day = datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
            series[day] = round(float(c), 4)
    except Exception:
        return {}
    cache_file.write_text(json.dumps({"ts": now, "series": series}))
    return series
