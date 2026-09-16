#!/usr/bin/env python3
"""Tests for edge_diff_v2.py + zq_contracts.py — per-meeting ZQ edge test."""

from pathlib import Path

from python.evaluation.edge_diff_v2 import (
    analyze_edge_diff_v2,
    reconstruct,
    snapshot_diff_v2,
)
from python.evaluation.zq_contracts import (
    close_on_or_before,
    contract_symbol,
    load_fixture,
    meeting_contract,
    move_probs,
    rate_after_meeting,
)

FIXTURE = Path(__file__).parent / "fixtures" / "zq_monthly_sep2026.json"


def make_kalshi_market(suffix, price, end_date="2026-09-16", meeting="26SEP"):
    return {
        "market_id": f"KXFEDDECISION-{meeting}-{suffix}",
        "end_date": end_date,
        "yes_price": price,
        "source": "kalshi",
    }


def make_snapshot(date, fed_rate, cut, hold, hike, end_date="2026-09-16"):
    return {
        "date": date,
        "fed_funds_rate": fed_rate,
        "markets": [
            make_kalshi_market("C25", cut, end_date),
            make_kalshi_market("C26", 0.0, end_date),
            make_kalshi_market("H0", hold, end_date),
            make_kalshi_market("H25", hike, end_date),
            make_kalshi_market("H26", 0.0, end_date),
        ],
    }


def closes(price_u=96.2625):
    # 2026-08-22 entry covers August snapshots via previous-close rollback.
    return {
        "ZQU26.CBT": {
            "2026-08-22": price_u,
            "2026-09-01": price_u,
            "2026-09-15": price_u,
            "2026-09-16": price_u,
        }
    }


def test_contract_symbol():
    assert contract_symbol(2026, 9) == "ZQU26.CBT"
    assert contract_symbol(2027, 3) == "ZQH27.CBT"
    assert contract_symbol(2026, 12) == "ZQZ26.CBT"
    assert contract_symbol(2026, 1) == "ZQF26.CBT"


def test_meeting_contract():
    assert meeting_contract("2026-09-16") == "ZQU26.CBT"
    assert meeting_contract("2026-10-28") == "ZQV26.CBT"


def test_rate_after_meeting_sep16():
    # Live cross-check 2026-09-16: ZQU26=96.2625 -> implied 3.7375,
    # fed 3.63, dim=30, d=16 -> R_after ~= 3.8604.
    r = rate_after_meeting(96.2625, 3.63, "2026-09-16")
    assert abs(r - 3.8604) < 1e-3


def test_rate_after_meeting_last_day_returns_none():
    assert rate_after_meeting(96.0, 3.63, "2026-09-30") is None


def test_move_probs_clamp():
    p = move_probs(3.63, 4.50)  # +0.87 move -> hike saturates
    assert p["hike"] == 1.0
    assert p["cut"] == 0.0
    p = move_probs(3.63, 3.63)
    assert p == {"hold": 1.0, "cut": 0.0, "hike": 0.0}


def test_close_on_or_before_rolls_back_weekend():
    series = {"2026-09-11": 96.0, "2026-09-14": 96.1}
    assert close_on_or_before(series, "2026-09-13") == 96.0
    assert close_on_or_before(series, "2026-09-14") == 96.1
    assert close_on_or_before(series, "2026-09-10") is None


def test_snapshot_diff_v2_sep_meeting():
    snap = make_snapshot("2026-09-16", fed_rate=3.63, cut=0.20, hold=0.55, hike=0.25)
    d, reason = snapshot_diff_v2(snap, closes())
    assert reason is None
    assert d["meeting"] == "2026-09-16"
    assert d["contract"] == "ZQU26.CBT"
    assert abs(d["rate_after"] - 3.8604) < 1e-3
    assert abs(d["zq_hike"] - 0.9214) < 1e-3
    assert abs(d["diff_hike"] - (0.25 - d["zq_hike"])) < 1e-9


def test_snapshot_diff_v2_missing_contract_skipped():
    snap = make_snapshot("2026-07-20", fed_rate=3.63, cut=0.2, hold=0.6, hike=0.2,
                         end_date="2026-07-29")
    d, reason = snapshot_diff_v2(snap, closes())
    assert d is None
    assert "ZQN26.CBT" in reason


def test_snapshot_diff_v2_no_meeting_skipped():
    snap = {"date": "2026-07-05", "fed_funds_rate": 3.63, "markets": []}
    d, reason = snapshot_diff_v2(snap, closes())
    assert d is None
    assert reason == "no upcoming Kalshi meeting"


def test_analyze_v2_systematic():
    # 25 eligible days, all on/before the 2026-09-16 meeting (nearest_meeting
    # only looks forward, so post-meeting dates would be skipped).
    dates = [f"2026-08-{day:02d}" for day in range(23, 32)]
    dates += [f"2026-09-{day:02d}" for day in range(1, 17)]
    assert len(dates) == 25
    snaps = [
        make_snapshot(d, fed_rate=3.63, cut=0.05, hold=0.90, hike=0.05)
        for d in dates
    ]
    diffs = [snapshot_diff_v2(s, closes())[0] for s in snaps]
    result = analyze_edge_diff_v2(diffs)
    assert result["verdict"] == "SYSTEMATIC_DIVERGENCE"
    assert result["meetings"] == ["2026-09-16"]
    assert result["days_with_diff"] == 25


def test_reconstruct_tracks_skipped():
    snaps = [
        make_snapshot("2026-09-10", fed_rate=3.63, cut=0.1, hold=0.8, hike=0.1),
        {"date": "2026-07-05", "fed_funds_rate": 3.63, "markets": []},
    ]
    result, skipped = reconstruct(snaps, closes())
    assert result["days_with_diff"] == 1
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "no upcoming Kalshi meeting"


def test_fixture_covers_window():
    data = load_fixture(FIXTURE)
    assert "ZQU26.CBT" in data
    assert "2026-09-16" in data["ZQU26.CBT"]
    assert "2026-07-30" in data["ZQU26.CBT"]
