"""Provider response timestamps must survive normalization and cached reads."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from python.evaluation import analyze_snapshots, fetch_kalshi, live_tracker


def raw_market():
    return {
        "ticker": "KXFEDDECISION-26OCT-H0", "title": "No change?",
        "last_price_dollars": "0.80", "yes_bid_dollars": "0.79",
        "yes_ask_dollars": "0.81", "close_time": "2026-10-28T18:00:00Z",
        "volume_fp": "10",
    }


def test_public_response_is_stamped_once_and_snapshot_retains_it(monkeypatch):
    response = Mock()
    response.json.return_value = {"markets": [raw_market()], "cursor": None}
    monkeypatch.setattr(fetch_kalshi.requests, "get", lambda *args, **kwargs: response)
    before = datetime.now(timezone.utc)
    raw, cursor = fetch_kalshi._fetch_markets_page()
    after = datetime.now(timezone.utc)
    fetched_at = raw[0]["_fetched_at"]
    timestamp = datetime.fromisoformat(fetched_at)
    assert timestamp.tzinfo is not None
    assert before <= timestamp <= after
    assert cursor is None
    normalized = fetch_kalshi._normalize_market(raw[0])
    snapshot = live_tracker.snapshot_kalshi_market(normalized)
    assert normalized["fetched_at"] == fetched_at
    assert snapshot["fetched_at"] == fetched_at
    assert snapshot["yes_bid"] == 0.79
    assert snapshot["yes_ask"] == 0.81


def test_fresh_provider_result_is_cached_with_original_timestamp(monkeypatch):
    original = "2026-10-17T08:00:00+00:00"
    monkeypatch.setattr(fetch_kalshi, "_load_cache", lambda: {})
    monkeypatch.setattr(fetch_kalshi, "_fetch_all_markets", lambda: [dict(raw_market(), _fetched_at=original)])
    saved = []
    monkeypatch.setattr(fetch_kalshi, "_save_cache", lambda value: saved.append(value))
    result = fetch_kalshi.get_kalshi_fomc_markets()
    assert result[0]["fetched_at"] == original
    assert saved[0]["kalshi_markets"][0]["fetched_at"] == original


def test_cached_access_does_not_relabel_provider_timestamp(monkeypatch):
    original = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    cached = [{"market_id": "test", "fetched_at": original}]
    monkeypatch.setattr(fetch_kalshi, "_load_cache", lambda: {
        "kalshi_markets": cached, "kalshi_ts": datetime.now(timezone.utc).timestamp(),
    })
    monkeypatch.setattr(fetch_kalshi, "_fetch_all_markets", lambda: (_ for _ in ()).throw(AssertionError("cache miss")))
    result = fetch_kalshi.get_kalshi_fomc_markets()
    assert result is cached
    assert result[0]["fetched_at"] == original


def test_failed_fetch_returns_original_stale_cache_timestamp(monkeypatch):
    original = "2026-10-15T08:00:00+00:00"
    cached = [{"market_id": "test", "fetched_at": original}]
    monkeypatch.setattr(fetch_kalshi, "_load_cache", lambda: {"kalshi_markets": cached, "kalshi_ts": 0})
    monkeypatch.setattr(fetch_kalshi, "_fetch_all_markets", lambda: [])
    result = fetch_kalshi.get_kalshi_fomc_markets()
    assert result[0]["fetched_at"] == original


def test_legacy_cache_has_no_invented_timestamp(monkeypatch):
    cached = [{"market_id": "legacy"}]
    monkeypatch.setattr(fetch_kalshi, "_load_cache", lambda: {
        "kalshi_markets": cached, "kalshi_ts": datetime.now(timezone.utc).timestamp(),
    })
    assert "fetched_at" not in fetch_kalshi.get_kalshi_fomc_markets()[0]


def snapshot(fetched_at):
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    markets = [
        {"source": "kalshi", "market_id": f"KXFEDDECISION-26OCT-{suffix}",
         "end_date": "2026-10-28", "yes_price": value, "fetched_at": fetched_at,
         "question": suffix, "volume": 10}
        for suffix, value in (("H0", 0.8), ("C25", 0.1), ("C26", 0.0), ("H25", 0.1), ("H26", 0.0))
    ]
    return {
        "date": now.isoformat(), "fed_funds_rate": 3.75, "markets": markets,
        "model_inputs": {
            "model": "zq-effr-25bp-v1", "meeting": "2026-10-28",
            "effr": {"rate": 3.88, "effective_date": "2026-10-15", "fetched_at": now.isoformat()},
            "zq": {"price": 96.12, "symbol": "ZQV26.CBT", "asof": "2026-10-16T20:00:00+00:00",
                   "fetched_at": now.isoformat()},
            "rate_after": 3.88, "probabilities": {"cut": 0.0, "hold": 1.0, "hike": 0.0},
        },
    }


def test_analysis_accepts_fresh_stamped_kalshi_and_rejects_old_or_missing():
    result, skipped = analyze_snapshots.validated_diffs([snapshot("2026-10-17T12:00:00+00:00")])
    assert result is not None
    assert skipped == []
    for fetched_at in (None, "2026-10-17T11:58:59+00:00"):
        result, skipped = analyze_snapshots.validated_diffs([snapshot(fetched_at)])
        assert result is None
        assert "Kalshi" in skipped[0]["reason"]


def test_analysis_main_unavailable_is_nonzero_and_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(analyze_snapshots, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(analyze_snapshots, "load_snapshots", lambda: [snapshot(None)])
    status = analyze_snapshots.main([])
    report = json.loads((tmp_path / "analysis_2026-10-17.json").read_text())
    assert status != 0
    assert report["status"] == "UNAVAILABLE"
    assert report["edge_diff_v2"] is None


def test_analysis_without_snapshots_is_nonzero_and_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(analyze_snapshots, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(analyze_snapshots, "load_snapshots", lambda: [])
    assert analyze_snapshots.main([]) != 0
    reports = list(tmp_path.glob("analysis_*.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text())["status"] == "UNAVAILABLE"
