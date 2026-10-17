import json

from python.evaluation import fetch_polymarket, live_tracker
from python.evaluation.analyze_snapshots import validated_diffs


def market(prices=None, closed=False, status=None):
    return {
        "id": "gamma-1", "conditionId": "condition-1", "question": "Fed decision?",
        "outcomes": '["No", "Yes"]', "outcomePrices": json.dumps(prices or ["0.05", "0.95"]),
        "clobTokenIds": '["no-token", "yes-token"]', "closed": closed,
        "umaResolutionStatus": status, "endDate": "2026-10-28T18:00:00Z",
    }


def test_live_snapshot_uses_yes_outcome_token_even_when_no_is_first():
    result = live_tracker.snapshot_polymarket_market(market())
    assert result["yes_price"] == 0.95
    assert result["clob_token_id"] == "yes-token"


def test_bad_price_logs_without_attribute_error():
    result = live_tracker.snapshot_polymarket_market(market(["0.5", "invalid"]))
    assert result["yes_price"] is None
    assert result["clob_token_id"] == "yes-token"


def test_active_95_percent_market_has_no_resolved_outcome(monkeypatch):
    monkeypatch.setattr(fetch_polymarket, "fetch_prices_history", lambda token: [])
    monkeypatch.setattr(fetch_polymarket.time, "sleep", lambda seconds: None)
    result = fetch_polymarket.build_dataset([{"markets": [market()]}])[0]
    assert result["resolved_outcome"] is None


def test_closed_but_unresolved_market_has_no_settlement(monkeypatch):
    monkeypatch.setattr(fetch_polymarket, "fetch_prices_history", lambda token: [])
    monkeypatch.setattr(fetch_polymarket.time, "sleep", lambda seconds: None)
    result = fetch_polymarket.build_dataset([{"markets": [market(["0", "1"], closed=True)]}])[0]
    assert result["resolved_outcome"] is None


def test_authoritative_final_terminal_market_can_settle(monkeypatch):
    monkeypatch.setattr(fetch_polymarket, "fetch_prices_history", lambda token: [])
    monkeypatch.setattr(fetch_polymarket.time, "sleep", lambda seconds: None)
    result = fetch_polymarket.build_dataset([
        {"markets": [market(["0", "1"], closed=True, status="resolved")]}
    ])[0]
    assert result["resolved_outcome"] == "YES"
    assert result["yes_token_id"] == "yes-token"


def test_slug_fallback_preserves_token_order_and_yes_price(monkeypatch):
    monkeypatch.setattr(fetch_polymarket, "_get", lambda url, params: [{"markets": [market()]}])
    monkeypatch.setattr(fetch_polymarket, "FOMC_SLUGS", ["example"])
    monkeypatch.setattr(fetch_polymarket.time, "sleep", lambda seconds: None)
    result = fetch_polymarket.fetch_fomc_by_slug()[0]
    assert result["yes_price"] == 0.95
    assert result["clob_token_ids"] == ["no-token", "yes-token"]
    assert result["gamma_market_id"] == "gamma-1"


def test_analysis_has_no_implicit_fixture_fallback(tmp_path):
    legacy = {"date": "2026-10-17T08:00:00Z", "fed_funds_rate": 3.75, "markets": []}
    result, skipped = validated_diffs([legacy])
    assert result is None
    assert "No timestamped" in skipped[0]["reason"]
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"ZQV26.CBT": {"2026-10-16": 96.115}}')
    result, skipped = validated_diffs([legacy], fixture)
    assert result is None
    assert "daily EFFR" in skipped[0]["reason"]
