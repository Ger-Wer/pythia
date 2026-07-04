#!/usr/bin/env python3
"""
FedWatch-style probability calculator from ZQ (fed funds futures).
ponytail: uses simplified FedWatch formula — assumes fixed meeting dates, no path dependency.
Upgrade: integrate CME FedWatch official probabilities when available via API.
"""

from datetime import datetime
from typing import Optional

from python.backtest.fred_data import get_latest_fed_funds


FOMC_MEETINGS_2026 = [
    "2026-01-28",
    "2026-03-18",
    "2026-04-29",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
    "2026-10-28",
    "2026-12-16",
]

BASIS_POINTS_PER_CUT = 0.25


def get_current_rate() -> float:
    rate = get_latest_fed_funds()
    return rate if rate is not None else 3.63


def meeting_index(date_str: str) -> int:
    for i, m in enumerate(FOMC_MEETINGS_2026):
        if m == date_str:
            return i
    return -1


def zq_price_to_rate(price: float) -> float:
    return 100.0 - price


def probability_of_cut(
    current_rate: float,
    zq_implied_rate: float,
    cut_size: float = BASIS_POINTS_PER_CUT,
) -> float:
    diff = current_rate - zq_implied_rate
    prob = diff / cut_size
    return max(0.0, min(1.0, prob))


def probability_of_hike(
    current_rate: float,
    zq_implied_rate: float,
    hike_size: float = BASIS_POINTS_PER_CUT,
) -> float:
    diff = zq_implied_rate - current_rate
    prob = diff / hike_size
    return max(0.0, min(1.0, prob))


def next_meeting_after(date: datetime) -> Optional[str]:
    for m in FOMC_MEETINGS_2026:
        meeting_dt = datetime.strptime(m, "%Y-%m-%d")
        if meeting_dt > date:
            return m
    return None


def days_to_meeting(meeting_date_str: str, from_date: datetime) -> int:
    meeting = datetime.strptime(meeting_date_str, "%Y-%m-%d")
    return (meeting - from_date).days


if __name__ == "__main__":
    print("FedWatch calculator for FOMC 2026 meetings")
    current_rate = get_current_rate()
    print(f"Current fed funds target: {current_rate}%")
    print()
    print("Meetings:")
    for m in FOMC_MEETINGS_2026:
        print(f"  {m}")
    print()
    example_zq = 94.60
    implied = zq_price_to_rate(example_zq)
    p_cut = probability_of_cut(current_rate, implied)
    print(f"Example: ZQ={example_zq} -> implied rate={implied:.2f}% -> P(cut)={p_cut:.1%}")
