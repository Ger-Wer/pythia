#!/usr/bin/env python3
"""Tests for edge_diff.py — ZQ vs Kalshi meeting-price divergence."""

from python.evaluation.edge_diff import (
    bucket_of,
    snapshot_diff,
    analyze_edge_diff,
)


def make_kalshi_market(suffix, price, end_date="2026-09-16", meeting="26SEP"):
    return {
        "market_id": f"KXFEDDECISION-{meeting}-{suffix}",
        "end_date": end_date,
        "yes_price": price,
        "source": "kalshi",
    }


def make_snapshot(date, fed_rate, zq_price, cut, hold, hike, end_date="2026-09-16"):
    markets = [
        make_kalshi_market("C25", cut, end_date),
        make_kalshi_market("C26", 0.0, end_date),
        make_kalshi_market("H0", hold, end_date),
        make_kalshi_market("H25", hike, end_date),
        make_kalshi_market("H26", 0.0, end_date),
    ]
    zq = None
    if zq_price is not None:
        zq = {"price": zq_price, "implied_rate": 100.0 - zq_price, "source": "yahoo"}
    return {"date": date, "fed_funds_rate": fed_rate, "zq_futures": zq, "markets": markets}


def test_bucket_of():
    assert bucket_of("KXFEDDECISION-26SEP-C25") == "C25"
    assert bucket_of("KXFEDDECISION-27JAN-H0") == "H0"


def test_snapshot_diff_cut_gap():
    snap = make_snapshot("2026-08-01", fed_rate=3.75, zq_price=96.50, cut=0.30, hold=0.60, hike=0.10)
    d = snapshot_diff(snap)
    assert d["zq_cut"] == 1.0
    assert d["market_cut"] == 0.30
    assert abs(d["diff_cut"] + 0.70) < 1e-9
    assert d["meeting"] == "2026-09-16"


def test_snapshot_diff_no_zq():
    snap = make_snapshot("2026-08-01", fed_rate=3.75, zq_price=None, cut=0.3, hold=0.6, hike=0.1)
    assert snapshot_diff(snap) is None


def test_nearest_meeting_selected():
    snap = make_snapshot("2026-09-20", fed_rate=3.75, zq_price=96.50, cut=0.3, hold=0.6, hike=0.1)
    snap["markets"] += [
        make_kalshi_market("C25", 0.80, end_date="2026-10-28", meeting="26OCT"),
        make_kalshi_market("H0", 0.15, end_date="2026-10-28", meeting="26OCT"),
        make_kalshi_market("H25", 0.05, end_date="2026-10-28", meeting="26OCT"),
    ]
    d = snapshot_diff(snap)
    assert d["meeting"] == "2026-10-28"
    assert abs(d["market_cut"] - 0.80) < 1e-9


def test_systematic_divergence():
    snaps = [
        make_snapshot(f"2026-07-{day:02d}", fed_rate=3.75, zq_price=96.50, cut=0.30, hold=0.60, hike=0.10)
        for day in range(1, 26)
    ]
    result = analyze_edge_diff(snaps)
    assert result["verdict"] == "SYSTEMATIC_DIVERGENCE"
    assert result["stats"]["diff_cut"]["mean"] == -0.7
    assert result["days_with_diff"] == 25


def test_no_edge_when_aligned():
    snaps = [
        make_snapshot(f"2026-07-{day:02d}", fed_rate=3.75, zq_price=96.50, cut=1.0, hold=0.0, hike=0.0)
        for day in range(1, 26)
    ]
    result = analyze_edge_diff(snaps)
    assert result["verdict"] == "NO_SYSTEMATIC_EDGE"


def test_no_edge_insufficient_days():
    snaps = [
        make_snapshot(f"2026-07-{day:02d}", fed_rate=3.75, zq_price=96.50, cut=0.30, hold=0.60, hike=0.10)
        for day in range(1, 6)
    ]
    result = analyze_edge_diff(snaps)
    assert result["verdict"] == "NO_SYSTEMATIC_EDGE"
    assert result["days_with_diff"] == 5
