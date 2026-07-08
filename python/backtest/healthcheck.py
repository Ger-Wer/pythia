#!/usr/bin/env python3
"""
Stage 1 healthcheck: verifies container runtime health and snapshot freshness.

Checks:
1. Is the expected number of snapshots accumulating? (no silent outage)
2. Are FRED baselines non-null in the latest snapshot?

Alerts via Telegram if TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are set.
Designed to run from the host via cron (NOT inside the container) so it can
detect a crashed container.

Usage: python3 python/backtest/healthcheck.py
Exit codes: 0 healthy, 1 degraded, 2 critical.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
STALE_HOURS = 26  # daily cron + margin
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def alert(msg: str):
    """Send Telegram alert if configured, always log."""
    log(f"ALERT: {msg}")
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": f"⚠️ Pythia v2: {msg}"},
                timeout=10,
            )
        except requests.RequestException:
            pass  # best-effort; healthcheck must not fail on alert delivery


def latest_snapshot():
    files = sorted(SNAPSHOT_DIR.glob("*.json"))
    files = [f for f in files if not f.name.startswith("analysis")]
    if not files:
        return None, None
    f = files[-1]
    with open(f) as fh:
        return f, json.load(fh)


def main():
    # Check 1: snapshot freshness
    f, snap = latest_snapshot()
    if not f:
        alert("No snapshots found — possible fresh install or tracker never ran.")
        return 2

    snap_time = datetime.fromisoformat(snap["date"])
    age_hours = (datetime.now(timezone.utc) - snap_time).total_seconds() / 3600

    if age_hours > STALE_HOURS:
        alert(f"Latest snapshot is {age_hours:.1f}h old (>{STALE_HOURS}h). Tracker cron may be down.")
        return 2

    # Check 2: FRED baselines present
    missing = []
    for key in ("fed_funds_rate", "gdp_growth"):
        if snap.get(key) is None:
            missing.append(key)
    if missing:
        alert(f"Latest snapshot {f.name} missing FRED baseline(s): {', '.join(missing)}")
        return 1

    # Check 3: markets captured
    n = snap.get("total_markets", 0)
    if n < 10:
        alert(f"Only {n} markets in latest snapshot (expected 50+). Gamma API may be degraded.")
        return 1

    log(f"OK: latest={f.name} age={age_hours:.1f}h markets={n} ffr={snap.get('fed_funds_rate')} gdp={snap.get('gdp_growth')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
