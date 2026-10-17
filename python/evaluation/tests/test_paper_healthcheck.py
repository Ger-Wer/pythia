import json
from datetime import datetime, timezone

import pytest

from python.evaluation.healthcheck import check_paper_run

NOW=datetime(2026,10,17,12,tzinfo=timezone.utc)


def report():
    return {"started_at":"2026-10-17 08:15:00 UTC","mode":"paper_only","state":"ok",
            "forecasts_total":1,"publication_states":{"confirmed":1}}


def test_enabled_paper_requires_actual_work_report(tmp_path,monkeypatch):
    monkeypatch.setenv("PYTHIA_PAPER_ENABLED","1")
    assert "no valid" in check_paper_run(NOW,tmp_path/"missing.json")


@pytest.mark.parametrize("change",[{"state":"degraded"},{"started_at":"2026-10-15 08:15:00 UTC"},
                                     {"started_at":"2026-10-18 08:15:00 UTC"},{"mode":"live"}])
def test_broken_work_is_not_healthy(tmp_path,monkeypatch,change):
    monkeypatch.setenv("PYTHIA_PAPER_ENABLED","1")
    data=report();data.update(change)
    p=tmp_path/"run.json";p.write_text(json.dumps(data))
    assert check_paper_run(NOW,p) is not None


def test_completed_work_is_healthy(tmp_path,monkeypatch):
    monkeypatch.setenv("PYTHIA_PAPER_ENABLED","1")
    p=tmp_path/"run.json";p.write_text(json.dumps(report()))
    assert check_paper_run(NOW,p) is None


def test_disabled_paper_does_not_break_existing_install(tmp_path,monkeypatch):
    monkeypatch.delenv("PYTHIA_PAPER_ENABLED",raising=False)
    assert check_paper_run(NOW,tmp_path/"missing.json") is None
