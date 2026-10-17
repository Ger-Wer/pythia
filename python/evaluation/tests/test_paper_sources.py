"""Contract tests for the fail-closed paper inputs (run on amper only)."""

from datetime import datetime, timedelta, timezone

import pytest

from python.evaluation import paper_sources

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


def payloads(price=96.115, rate=3.88):
    history = [
        {"effectiveDate": f"2026-10-{day:02d}", "percentRate": rate}
        for day in range(1, 16)
        if datetime(2026, 10, day).weekday() < 5
    ]
    return [
        {"refRates": [history[-1]]},
        {"refRates": history},
        {"chart": {"result": [{"meta": {
            "symbol": "ZQV26.CBT", "regularMarketPrice": price,
            "regularMarketTime": datetime(2026, 10, 16, 20, tzinfo=timezone.utc).timestamp(),
        }}]}},
    ]


def install(monkeypatch, responses, fetched_at=NOW):
    remaining = iter(responses)
    monkeypatch.setattr(paper_sources, "_fetch_json", lambda *args, **kwargs: (next(remaining), fetched_at))


def test_valid_current_month_inputs_are_timestamped_and_conditional(monkeypatch):
    install(monkeypatch, payloads())
    result = paper_sources.fetch_model_inputs("2026-10-28", now=NOW)
    assert result["effr"]["rate"] == 3.88
    assert result["effr"]["series"] == "EFFR"
    assert result["zq"]["symbol"] == "ZQV26.CBT"
    assert result["model"] == "zq-effr-25bp-v1"
    assert result["probabilities"]["hike"] == pytest.approx(0.2066666667)
    assert sum(result["probabilities"].values()) == pytest.approx(1)
    assert any("not a uniquely identified" in text for text in result["assumptions"])


@pytest.mark.parametrize("price", [95.0, float("nan"), float("inf")])
def test_large_or_nonfinite_implied_move_is_rejected(monkeypatch, price):
    install(monkeypatch, payloads(price=price))
    with pytest.raises(ValueError):
        paper_sources.fetch_model_inputs("2026-10-28", now=NOW)


def test_stale_fetch_is_rejected(monkeypatch):
    install(monkeypatch, payloads(), NOW - timedelta(seconds=61))
    with pytest.raises(ValueError, match="fetch"):
        paper_sources.fetch_model_inputs("2026-10-28", now=NOW)


def test_unstable_month_effr_is_rejected(monkeypatch):
    responses = payloads()
    responses[1]["refRates"][0]["percentRate"] = 3.63
    install(monkeypatch, responses)
    with pytest.raises(ValueError, match="Changing"):
        paper_sources.fetch_model_inputs("2026-10-28", now=NOW)


def test_future_market_observation_is_rejected(monkeypatch):
    responses = payloads()
    responses[2]["chart"]["result"][0]["meta"]["regularMarketTime"] = (NOW + timedelta(seconds=1)).timestamp()
    install(monkeypatch, responses)
    with pytest.raises(ValueError):
        paper_sources.fetch_model_inputs("2026-10-28", now=NOW)


def test_future_delivery_month_is_rejected_before_requests(monkeypatch):
    monkeypatch.setattr(paper_sources, "_fetch_json", lambda *args, **kwargs: pytest.fail("network called"))
    with pytest.raises(ValueError, match="Future delivery"):
        paper_sources.fetch_model_inputs("2026-12-09", now=datetime(2026, 11, 3, tzinfo=timezone.utc))


def test_calendar_updates_nearest_at_statement_cutoff():
    assert paper_sources.next_meeting(datetime(2026, 10, 28, 17, 59, tzinfo=timezone.utc)) == "2026-10-28"
    assert paper_sources.next_meeting(datetime(2026, 10, 28, 18, tzinfo=timezone.utc)) == "2026-12-09"


def test_naive_timestamps_are_not_accepted():
    with pytest.raises(ValueError, match="timezone"):
        paper_sources.next_meeting(datetime(2026, 10, 17))
