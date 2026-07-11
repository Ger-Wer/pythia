import json
from pathlib import Path

import requests

from python.evaluation import cpi_nowcast


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_fixture_contains_tooltext_values_and_empty_data() -> None:
    raw = json.loads((FIXTURES_DIR / "cpi_nowcast_sample.json").read_text())

    point = next(
        point
        for graph in raw
        for dataset in graph["dataset"]
        for point in dataset["data"]
        if point.get("value") == ""
    )

    assert point["value"] == ""
    assert "tooltext" in point


def test_parse_nowcast_extracts_four_required_series() -> None:
    raw = json.loads((FIXTURES_DIR / "cpi_nowcast_sample.json").read_text())
    raw[1]["dataset"][0]["data"][0]["value"] = "999"

    result = cpi_nowcast._parse_nowcast(raw)

    assert list(result) == cpi_nowcast.SERIES
    assert result["CPI Inflation"][0] == {"date": "08/20", "data": 0.1622798409}
    assert result["Core CPI Inflation"][0] == {"date": "08/20", "data": 0.1404224952}
    assert result["PCE Inflation"][0] == {"date": "08/20", "data": 0.1332665448}
    assert result["Core PCE Inflation"][0] == {"date": "08/20", "data": 0.1252425572}


def test_latest_cpi_nowcast_extracts_float_data(monkeypatch) -> None:
    raw = json.loads((FIXTURES_DIR / "cpi_nowcast_sample.json").read_text())
    monkeypatch.setattr(cpi_nowcast, "_fetch_raw", lambda: raw)
    monkeypatch.setattr(cpi_nowcast, "_load_cache", lambda: {})
    monkeypatch.setattr(cpi_nowcast, "_save_cache", lambda cache: None)

    result = cpi_nowcast.get_latest_cpi_nowcast()

    assert result is not None
    assert result["date"] == "09/16"
    assert result["data"] == {
        "CPI Inflation": 0.1248922461,
        "Core CPI Inflation": 0.1404224952,
        "PCE Inflation": 0.0874538778,
        "Core PCE Inflation": 0.0992484737,
    }
    assert isinstance(result["ts"], float)


def test_network_failure_returns_none(monkeypatch) -> None:
    def raise_timeout(*args, **kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr(cpi_nowcast.requests, "get", raise_timeout)
    monkeypatch.setattr(cpi_nowcast, "_load_cache", lambda: {})

    result = cpi_nowcast.get_latest_cpi_nowcast()

    assert result is None
