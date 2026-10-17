"""Mock HTTP/text tests. Run only on amper; never sends a Telegram request."""

import json

import pytest
import requests

from python.evaluation.paper_telegram import (
    LEASE_SECONDS, Outbox, render_forecast, render_result, render_status, send,
)


ENV = {
    "TELEGRAM_BOT_TOKEN": "synthetic-test-token",
    "PYTHIA_PAPER_TELEGRAM_CHAT_ID": "-1000000001",
    "PYTHIA_PAPER_TELEGRAM_ENABLED": "1",
}
TEXT = "PAPER — учебная публикация\nПрогноз: test-event-001"


class Response:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.body = {"ok": True, "result": {"message_id": 123}} if body is None else body

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class HTTP:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def box(tmp_path, env=None, clock=lambda: 1000):
    return Outbox(tmp_path / "outbox.sqlite", env=dict(ENV if env is None else env), clock=clock)


def test_render_forecast_contains_paper_identity_inputs_and_dates():
    forecast = {
        "forecast_id": "forecast-1", "event": "GDP release", "market_url": "https://example.test/market",
        "side": "NO", "model_probability": "65%", "market_price": "$0.40",
        "paper_stake": "$10", "source_as_of": "2026-10-17T09:00:00Z",
        "forecast_at": "2026-10-17T09:01:00Z", "expires_at": "2026-10-17T12:00:00Z",
        "reason": "Documented independent baseline",
    }
    text = render_forecast(forecast)
    assert text.startswith("PAPER — ")
    for value in forecast.values():
        assert value in text
    with pytest.raises(KeyError):
        render_forecast({"forecast_id": "incomplete"})
    with pytest.raises(ValueError, match="length"):
        render_forecast({**forecast, "reason": "x" * 4000})


def test_result_publishes_losses_costs_and_resolution_source():
    result = {
        "result_id": "result-1", "forecast_id": "forecast-1", "event": "GDP release",
        "market_url": "https://example.test/market", "status": "ПРОИГРЫШ", "outcome": "YES",
        "fill_summary": "NO 25 × $0.40", "costs": "$0.20", "net_pnl": "−$10.20",
        "resolution_source": "https://example.test/official-release",
        "source_as_of": "2026-10-17T12:00:00Z", "settled_at": "2026-10-17T12:02:00Z",
    }
    text = render_result(result)
    assert text.startswith("PAPER — ")
    for value in result.values():
        assert value in text
    assert "PAPER" in render_status({"status_id": "s1", "as_of": "now", "status": "NO TRADE", "details": "stale data"})


def test_default_and_each_missing_opt_in_are_dry_run(tmp_path):
    b = box(tmp_path)
    b.enqueue("forecast:f1", TEXT, expires_at=2000)
    http = HTTP()
    assert b.deliver(http)[0]["state"] == "dry_run"
    b.env["PYTHIA_PAPER_TELEGRAM_ENABLED"] = "true"
    assert b.deliver(http, enabled=True)[0]["state"] == "dry_run"
    assert b.get("forecast:f1")["attempts"] == 0
    assert http.calls == []


def test_confirmed_receipt_survives_restart_no_duplicate_and_reply_payload(tmp_path):
    b = box(tmp_path)
    b.enqueue("result:r1", TEXT, reply_to=42)
    http = HTTP(Response())
    assert b.deliver(http, enabled=True)[0]["message_id"] == 123
    b.close()
    b = box(tmp_path)
    assert b.get("result:r1")["state"] == "confirmed"
    b.enqueue("result:r1", TEXT, reply_to=42)
    assert b.deliver(http, enabled=True) == []
    assert len(http.calls) == 1
    assert http.calls[0]["json"]["reply_parameters"] == {"message_id": 42}
    assert http.calls[0]["json"]["chat_id"] == ENV["PYTHIA_PAPER_TELEGRAM_CHAT_ID"]
    assert b.db.execute("SELECT state FROM outbox_attempts").fetchone()[0] == "confirmed"


def test_key_cannot_be_rebound_to_new_text_reply_or_destination(tmp_path):
    b = box(tmp_path)
    b.enqueue("forecast:f1", TEXT, expires_at=2000)
    with pytest.raises(ValueError, match="immutable"):
        b.enqueue("forecast:f1", TEXT + " changed", expires_at=2000)
    with pytest.raises(ValueError, match="immutable"):
        b.enqueue("forecast:f1", TEXT, reply_to=2, expires_at=2000)
    other = Outbox(tmp_path / "outbox.sqlite", destination="-1000000002", env=ENV)
    with pytest.raises(ValueError, match="immutable"):
        other.enqueue("forecast:f1", TEXT, expires_at=2000)


@pytest.mark.parametrize("failure", [
    requests.Timeout("url contains synthetic-test-token"),
    Response(503, {"ok": False}),
    Response(200, ValueError("bad receipt")),
    Response(200, {"ok": True, "result": {}}),
])
def test_uncertain_is_durable_and_never_automatically_resent(tmp_path, failure):
    b = box(tmp_path)
    b.enqueue("forecast:f1", TEXT, expires_at=2000)
    http = HTTP(failure)
    receipt = b.deliver(http, enabled=True)
    assert receipt[0]["state"] == "uncertain"
    assert "synthetic-test-token" not in json.dumps(receipt)
    b.close()
    b = box(tmp_path)
    assert b.deliver(http, enabled=True) == []
    assert len(http.calls) == 1
    assert b.get("forecast:f1")["attempts"] == 1
    b.reconcile("forecast:f1", 456)
    assert b.get("forecast:f1")["message_id"] == 456
    assert b.get("forecast:f1")["state"] == "confirmed"
    with pytest.raises(ValueError, match="uncertain"):
        b.reconcile("forecast:f1", 789)


def test_429_honors_retry_after_and_records_both_attempts(tmp_path):
    now = [1000]
    b = box(tmp_path, clock=lambda: now[0])
    b.enqueue("forecast:f1", TEXT, expires_at=2000)
    http = HTTP(Response(429, {"ok": False, "parameters": {"retry_after": 90}}), Response())
    assert b.deliver(http, enabled=True)[0]["state"] == "queued"
    assert b.get("forecast:f1")["next_attempt_at"] == 1090
    now[0] = 1089
    assert b.deliver(http, enabled=True) == []
    now[0] = 1090
    assert b.deliver(http, enabled=True)[0]["state"] == "confirmed"
    assert b.get("forecast:f1")["attempts"] == 2
    assert b.db.execute("SELECT COUNT(*) FROM outbox_attempts").fetchone()[0] == 2


def test_concurrent_start_does_not_recover_fresh_claim_but_expired_becomes_uncertain(tmp_path):
    now = [1000]
    b = box(tmp_path, clock=lambda: now[0])
    b.enqueue("forecast:f1", TEXT, expires_at=2000)
    with b._tx():
        b.db.execute("UPDATE outbox SET state='sending',owner='other',lease_until=?", (1000 + LEASE_SECONDS,))
        b.db.execute("INSERT INTO outbox_attempts(key,started_at,state) VALUES('forecast:f1',1000,'sending')")
    other = box(tmp_path, clock=lambda: now[0])
    assert other.get("forecast:f1")["state"] == "sending"
    http = HTTP()
    assert other.deliver(http, enabled=True) == []
    now[0] += LEASE_SECONDS + 1
    assert other.deliver(http, enabled=True) == []
    assert other.get("forecast:f1")["state"] == "uncertain"
    assert other.db.execute("SELECT state FROM outbox_attempts").fetchone()[0] == "uncertain"
    assert http.calls == []


def test_reentrant_delivery_claim_prevents_same_post_twice(tmp_path):
    first = box(tmp_path)
    first.enqueue("forecast:f1", TEXT, expires_at=2000)
    second = box(tmp_path)

    class ConcurrentHTTP(HTTP):
        def post(self, url, **kwargs):
            assert second.deliver(HTTP(), enabled=True) == []
            return super().post(url, **kwargs)

    http = ConcurrentHTTP(Response())
    assert first.deliver(http, enabled=True)[0]["state"] == "confirmed"
    assert len(http.calls) == 1


def test_wrong_destination_and_missing_credentials_never_send(tmp_path):
    b = box(tmp_path)
    b.enqueue("forecast:f1", TEXT, expires_at=2000)
    http = HTTP()
    b.env["PYTHIA_PAPER_TELEGRAM_CHAT_ID"] = "-1000000002"
    assert b.deliver(http, enabled=True)[0]["state"] == "blocked"
    b.env["PYTHIA_PAPER_TELEGRAM_CHAT_ID"] = ENV["PYTHIA_PAPER_TELEGRAM_CHAT_ID"]
    del b.env["TELEGRAM_BOT_TOKEN"]
    assert b.deliver(http, enabled=True)[0]["state"] == "blocked"
    assert b.get("forecast:f1")["attempts"] == 0
    assert http.calls == []


def test_explicit_rejection_is_failed_not_retried(tmp_path):
    b = box(tmp_path)
    b.enqueue("forecast:f1", TEXT, expires_at=2000)
    http = HTTP(Response(403, {"ok": False, "description": "contains synthetic-test-token"}))
    receipts = b.deliver(http, enabled=True)
    assert receipts[0]["state"] == "failed"
    assert "synthetic-test-token" not in json.dumps(receipts)
    assert b.deliver(http, enabled=True) == []
    assert len(http.calls) == 1


def test_send_direct_also_requires_both_opt_ins():
    http = HTTP()
    assert send(http, TEXT, env=ENV)["state"] == "dry_run"
    assert send(http, TEXT, enabled=True, env={})["state"] == "dry_run"
    with pytest.raises(ValueError, match="label"):
        send(http, "Real-money advice", enabled=True, env=ENV)
    assert http.calls == []


def test_forecast_requires_expiry_and_expired_enqueue_is_persistent(tmp_path):
    b = box(tmp_path)
    with pytest.raises(ValueError, match="expiry"):
        b.enqueue("forecast:no-expiry", TEXT)
    b.enqueue("forecast:already-expired", TEXT, expires_at=999)
    assert b.get("forecast:already-expired")["state"] == "expired"
    assert b.deliver(HTTP(), enabled=True) == []
    assert b.get("forecast:already-expired")["attempts"] == 0


def test_forecast_that_expires_after_429_is_not_sent_late(tmp_path):
    now = [1000]
    b = box(tmp_path, clock=lambda: now[0])
    b.enqueue("forecast:short-ttl", TEXT, expires_at=1010)
    http = HTTP(Response(429, {"parameters": {"retry_after": 90}}))
    assert b.deliver(http, enabled=True)[0]["state"] == "queued"
    now[0] = 1090
    assert b.deliver(http, enabled=True) == []
    assert b.get("forecast:short-ttl")["state"] == "expired"
    assert len(http.calls) == 1


def test_expiry_and_body_limit_are_immutable_and_not_silently_truncated(tmp_path):
    b = box(tmp_path)
    b.enqueue("forecast:f1", TEXT, expires_at=2000)
    with pytest.raises(ValueError, match="immutable"):
        b.enqueue("forecast:f1", TEXT, expires_at=2001)
    with pytest.raises(ValueError, match="length"):
        b.enqueue("status:too-long", "PAPER — " + "😀" * 2000)
    assert b.get("status:too-long") is None


def test_queued_forecast_expiring_during_earlier_delivery_never_sends(tmp_path):
    now = [1000]
    b = box(tmp_path, clock=lambda: now[0])
    b.enqueue("status:first", TEXT)
    now[0] = 1001
    b.enqueue("forecast:expires-in-batch", TEXT, expires_at=1010)

    class SlowHTTP(HTTP):
        def post(self, url, **kwargs):
            now[0] = 1011
            return super().post(url, **kwargs)

    http = SlowHTTP(Response())
    assert b.deliver(http, enabled=True)[0]["state"] == "confirmed"
    assert b.get("forecast:expires-in-batch")["state"] == "expired"
    assert b.get("forecast:expires-in-batch")["attempts"] == 0
    assert len(http.calls) == 1
