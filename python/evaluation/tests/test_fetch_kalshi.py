#!/usr/bin/env python3
"""Tests for fetch_kalshi.py — Kalshi FOMC market fetcher."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from python.evaluation.fetch_kalshi import (
    get_kalshi_fomc_markets,
    _parse_strike_info,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_parse_strike_info_hike():
    """Parse H0 suffix → Hike direction."""
    result = _parse_strike_info("KXFEDDECISION-26JUL-H0")
    assert result == {"direction": "Hike", "bucket": "0"}


def test_parse_strike_info_cut():
    """Parse C25 suffix → Cut direction."""
    result = _parse_strike_info("KXFEDDECISION-26JUL-C25")
    assert result == {"direction": "Cut", "bucket": "25"}


def test_parse_strike_info_invalid():
    """Parse invalid suffix → dict with raw_suffix."""
    result = _parse_strike_info("INVALID-TICKER")
    assert result == {"raw_suffix": "TICKER"}


def test_get_kalshi_markets_from_fixture():
    """Parse fixture → list of standardized dicts."""
    fixture = FIXTURES_DIR / "kalshi_markets.json"
    with open(fixture) as f:
        mock_response = MagicMock()
        mock_response.json.return_value = json.load(f)
        mock_response.raise_for_status = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200

    with patch("python.evaluation.fetch_kalshi.requests.get", return_value=mock_response):
        result = get_kalshi_fomc_markets()

    assert len(result) == 2
    assert result[0]["source"] == "kalshi"
    assert result[0]["market_id"] == "KXFEDDECISION-26JUL-H0"
    assert result[0]["yes_price"] == 0.64
    assert result[0]["end_date"] == "2026-07-29"
    assert result[0]["event_ticker"] == "KXFEDDECISION-26JUL"
    assert result[0]["strike_info"]["direction"] == "Hike"
    assert result[0]["strike_info"]["bucket"] == "0"


def test_get_kalshi_markets_empty_response():
    """Handle empty API response gracefully."""
    cache_file = Path(__file__).parent.parent / "data" / "kalshi_cache.json"
    if cache_file.exists():
        cache_file.unlink()

    mock_response = MagicMock()
    mock_response.json.return_value = {"markets": [], "cursor": ""}
    mock_response.raise_for_status = MagicMock()
    mock_response.ok = True
    mock_response.status_code = 200

    with patch("python.evaluation.fetch_kalshi.requests.get", return_value=mock_response):
        result = get_kalshi_fomc_markets()

    assert result == []


def test_get_kalshi_markets_api_error():
    """Handle API error gracefully."""
    cache_file = Path(__file__).parent.parent / "data" / "kalshi_cache.json"
    if cache_file.exists():
        cache_file.unlink()

    import requests
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.RequestException("API error")

    with patch("python.evaluation.fetch_kalshi.requests.get", return_value=mock_response):
        result = get_kalshi_fomc_markets()

    assert result == []
