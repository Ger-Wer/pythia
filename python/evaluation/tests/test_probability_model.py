"""Tests for the R6 probability model and the independent scorer."""

import pytest

from evaluation.probability_model import (
    ForecastRefused,
    build_forecast_record,
    distribution_from_expected_move,
    fomc_distribution,
)
from evaluation.score_forecasts import audit_record, brier_score, log_loss, score_records


def test_distribution_zero_move_is_all_hold():
    d = distribution_from_expected_move(0.0)
    assert d == {"le_-50": 0.0, "-25": 0.0, "0": 1.0, "+25": 0.0, "ge_+50": 0.0}


def test_distribution_exact_grid_points():
    assert distribution_from_expected_move(-25.0)["-25"] == 1.0
    assert distribution_from_expected_move(25.0)["+25"] == 1.0
    assert distribution_from_expected_move(-50.0)["le_-50"] == 1.0
    assert distribution_from_expected_move(50.0)["ge_+50"] == 1.0


def test_distribution_interpolates_between_grid_points():
    d = distribution_from_expected_move(-37.5)
    assert d["-25"] == pytest.approx(0.5)
    assert d["le_-50"] == pytest.approx(0.5)
    d = distribution_from_expected_move(12.5)
    assert d["0"] == pytest.approx(0.5)
    assert d["+25"] == pytest.approx(0.5)


def test_distribution_beyond_grid_lands_in_edge_bucket():
    assert distribution_from_expected_move(-80.0)["le_-50"] == 1.0
    assert distribution_from_expected_move(63.0)["ge_+50"] == 1.0


def test_distribution_always_sums_to_one():
    for move in (-49.9, -25.1, -0.1, 0.1, 24.9, 49.9, 7.3, -13.7):
        assert sum(distribution_from_expected_move(move).values()) == pytest.approx(1.0)


def test_fomc_distribution_recovers_known_move():
    # Meeting on 2026-09-16 in a 30-day month: the zq_contracts
    # day-weighting uses the meeting day-of-month (16) as the
    # pre-meeting weight and the remaining 14 days as post-meeting.
    # A post-meeting rate of 3.875 (a +25bp move from 3.625) implies a
    # September contract price of 100 - (16*3.625 + 14*3.875)/30.
    implied = (16 * 3.625 + 14 * 3.875) / 30
    dist, diag = fomc_distribution(
        rate_before=3.625, meeting_iso="2026-09-16",
        meeting_month_close=100.0 - implied,
        close_date="2026-09-15", horizon_date="2026-09-15")
    assert diag["expected_move_bp"] == pytest.approx(25.0, abs=0.01)
    assert dist["+25"] == pytest.approx(1.0, abs=0.01)


def test_fomc_distribution_refuses_look_ahead():
    with pytest.raises(ForecastRefused):
        fomc_distribution(
            rate_before=3.625, meeting_iso="2026-09-16",
            meeting_month_close=96.3,
            close_date="2026-09-16", horizon_date="2026-09-15")


def test_fomc_distribution_refuses_stale_close():
    with pytest.raises(ForecastRefused):
        fomc_distribution(
            rate_before=3.625, meeting_iso="2026-09-16",
            meeting_month_close=96.3,
            close_date="2026-09-01", horizon_date="2026-09-09")


def _record(**overrides):
    base = dict(
        event_id="fomc-2026-09-16", question="FOMC decision 2026-09-16",
        meeting_iso="2026-09-16", horizon_label="T-1d",
        horizon_date="2026-09-15", forecast_ts="2026-09-15T08:00:00Z",
        rate_before=3.625, meeting_month_close=96.243,
        close_date="2026-09-15",
        baseline={"source": "test", "distribution":
                  {"le_-50": 0.0, "-25": 0.1, "0": 0.5, "+25": 0.35, "ge_+50": 0.05}})
    base.update(overrides)
    return build_forecast_record(**base)


def test_build_record_shape():
    rec = _record()
    assert rec["model_version"] == "pythia-probability/1.0"
    assert rec["usable_for_forecast"] is False
    assert sum(rec["distribution"].values()) == pytest.approx(1.0)
    assert audit_record(rec) == []


def test_scorer_penalizes_wrong_confidence():
    good = {"0": 0.9, "+25": 0.1, "-25": 0.0, "le_-50": 0.0, "ge_+50": 0.0}
    bad = {"0": 0.1, "+25": 0.9, "-25": 0.0, "le_-50": 0.0, "ge_+50": 0.0}
    assert brier_score(good, "0") < brier_score(bad, "0")
    assert log_loss(good, "0") < log_loss(bad, "0")


def test_score_records_compares_model_and_baseline():
    rows = score_records([_record()], {"fomc-2026-09-16": {"actual": "+25"}})
    assert len(rows) == 1
    assert rows[0]["audit_violations"] == []
    assert "model" in rows[0] and "baseline" in rows[0]
    assert rows[0]["baseline"]["source"] == "test"


def test_audit_flags_look_ahead_record():
    rec = _record()
    rec["inputs"]["close_date"] = "2026-09-16"  # after the T-1d horizon
    assert any("look-ahead" in p for p in audit_record(rec))
