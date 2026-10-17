from datetime import datetime, timezone

from python.evaluation import paper_cycle as cycle
from python.evaluation.paper_ledger import PaperLedger
from python.evaluation.paper_telegram import Outbox

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc).timestamp()


def inputs(meeting):
    return {"meeting":meeting,"model":"zq-effr-25bp-v1", "probabilities":{"hold":0.8,"hike":0.2,"cut":0},
            "effr":{"effective_date":"2026-10-15"}, "zq":{"asof":"2026-10-16T20:00:00Z"}}


def market(slug, meeting):
    return {"event_id":"event1","market_id":"market1","conditionId":"cond1", "tokens_by_side":{"YES":"20","NO":"10"},
            "url":"https://polymarket.com/event/test","fee_rate":0.05}


def book(token, condition_id, now=None):
    return {"asks":[(0.51,1000)],"bids":[(0.49,1000)],"midpoint":0.5,"quoted_at":NOW,
            "min_order_size":5,"raw":{"asset_id":token,"market":condition_id}}


def prepare(ledger, **kw):
    return cycle.make_forecast(ledger,slug="test",meeting="2026-10-28",now=NOW,
                               get_inputs=inputs,market_loader=market,book_loader=book,**kw)


def test_forecast_fill_and_retry_are_one_position(tmp_path):
    ledger=PaperLedger(tmp_path/"ledger.db")
    assert prepare(ledger)["trade"]["status"] == "filled"
    assert prepare(ledger)["state"] == "already_recorded"
    assert ledger.report()["open"] == 1
    assert float(ledger.report()["cash"]) < 1000
    assert "P1" in cycle.public_forecast(ledger,ledger.get_forecast(1))


def test_depth_failure_is_reported_and_does_not_spend(tmp_path):
    ledger=PaperLedger(tmp_path/"ledger.db")
    def thin(token,condition_id,now=None):
        result=book(token,condition_id,now);result["asks"]=[(0.51,1)];return result
    result=cycle.make_forecast(ledger,slug="test",meeting="2026-10-28",now=NOW,
                               get_inputs=inputs,market_loader=market,book_loader=thin)
    assert result["trade"]["reason"] == "insufficient_depth"
    assert ledger.report()["cash"] == "1000"
    text=cycle.public_forecast(ledger,ledger.get_forecast(1))
    assert "недостаточный объём" in text
    assert "Учебная сумма: $0" in text


def test_crash_recovery_after_durable_fill_reconstructs_same_post(tmp_path):
    ledger=PaperLedger(tmp_path/"ledger.db")
    prepare(ledger)
    box=Outbox(tmp_path/"outbox.db",destination="channel",clock=lambda:NOW)
    cycle.recover_publications(ledger,box,NOW)
    before=box.get("forecast:1")
    cycle.recover_publications(ledger,box,NOW+1)
    assert len(box.list_states()) == 1
    assert box.get("forecast:1")["text"] == before["text"]


class Response:
    status_code=200
    def json(self): return {"ok":True,"result":{"message_id":123}}


class Client:
    def post(self,*args,**kwargs): return Response()


def test_losing_result_after_confirmed_forecast_is_published_once(tmp_path):
    ledger=PaperLedger(tmp_path/"ledger.db")
    prepare(ledger)
    env={"PYTHIA_PAPER_TELEGRAM_ENABLED":"1","PYTHIA_PAPER_TELEGRAM_CHAT_ID":"channel","TELEGRAM_BOT_TOKEN":"fake-test-token"}
    box=Outbox(tmp_path/"outbox.db",destination="channel",env=env,clock=lambda:NOW)
    cycle.recover_publications(ledger,box,NOW)
    box.deliver(Client(),enabled=True)
    assert cycle.settle_and_publish(ledger,box,NOW+100,resolve=lambda _:"NO") == []
    post=box.get("result:1")
    assert "УБЫТОК" in post["text"]
    assert post["reply_to"] == 123
    assert float(ledger.report()["realised_pnl"]) < 0
    cycle.settle_and_publish(ledger,box,NOW+200,resolve=lambda _:"NO")
    assert len(box.list_states()) == 2
    assert ledger.report()["independent_events"] == 1


def test_no_result_while_original_post_is_unconfirmed(tmp_path):
    ledger=PaperLedger(tmp_path/"ledger.db");prepare(ledger)
    box=Outbox(tmp_path/"outbox.db",destination="channel",clock=lambda:NOW)
    cycle.recover_publications(ledger,box,NOW)
    cycle.settle_and_publish(ledger,box,NOW+100,resolve=lambda _:"NO")
    assert box.get("result:1") is None
    assert ledger.report()["closed"] == 1
    assert ledger.report()["losses"] == 1


def test_unconfigured_preview_cannot_poison_production_outbox(tmp_path,monkeypatch):
    monkeypatch.delenv("PYTHIA_PAPER_TELEGRAM_CHAT_ID",raising=False)
    result=cycle.run_cycle(tmp_path,slug="",announce=True,now=NOW)
    assert result["state"] == "ok"
    assert not (tmp_path/"outbox.sqlite3").exists()
    assert (tmp_path/"preview"/"outbox.sqlite3").exists()


def test_send_requires_all_explicit_credentials(tmp_path,monkeypatch):
    import pytest
    monkeypatch.setenv("PYTHIA_PAPER_TELEGRAM_CHAT_ID","channel")
    monkeypatch.setenv("PYTHIA_PAPER_TELEGRAM_ENABLED","1")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN",raising=False)
    with pytest.raises(ValueError,match="opt-in"):
        cycle.run_cycle(tmp_path,slug="",send=True,now=NOW)


def test_no_universe_is_an_explicit_failure(tmp_path,monkeypatch):
    monkeypatch.delenv("PYTHIA_PAPER_TELEGRAM_CHAT_ID",raising=False)
    result=cycle.run_cycle(tmp_path,slug="",now=NOW)
    assert result["state"] == "degraded"
    assert "paper_event_not_configured" in result["errors"]
