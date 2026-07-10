#!/usr/bin/env python3
"""Tests for zq_futures.py — ZQ futures fetcher with source fallback."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from python.backtest.zq_futures import zq_price_to_rate, get_zq_price, _fetch_yahoo


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_zq_price_to_rate():
    """ZQ price 96.375 → implied rate 3.625%."""
    assert zq_price_to_rate(96.375) == 3.625
    assert zq_price_to_rate(94.50) == 5.50
    assert zq_price_to_rate(100.0) == 0.0


def test_fetch_yahoo_from_fixture():
    """Parse Yahoo fixture → dict with source='yahoo'."""
    fixture = FIXTURES_DIR / "yahoo_zq.json"
    with open(fixture) as f:
        mock_response = MagicMock()
        mock_response.json.return_value = json.load(f)
        mock_response.raise_for_status = MagicMock()
        mock_response.ok = True

    with patch("python.backtest.zq_futures.requests.get", return_value=mock_response):
        result = _fetch_yahoo()
        assert result is not None
        assert result["source"] == "yahoo"
        assert result["price"] == 96.375


def test_get_zq_price_with_fixture():
    """get_zq_price returns full dict from Yahoo fixture."""
    # Clear cache first so mock is used
    cache_file = Path(__file__).parent.parent / "data" / "zq_cache.json"
    if cache_file.exists():
        cache_file.unlink()

    fixture = FIXTURES_DIR / "yahoo_zq.json"
    with open(fixture) as f:
        mock_response = MagicMock()
        mock_response.json.return_value = json.load(f)
        mock_response.raise_for_status = MagicMock()
        mock_response.ok = True

    with patch("python.backtest.zq_futures.requests.get", return_value=mock_response):
        result = get_zq_price()
        assert result is not None
        assert result["price"] == 96.375
        assert result["implied_rate"] == 3.625
        assert result["source"] == "yahoo"
        assert "date" in result
        assert "ts" in result


def test_all_fail_returns_none():
    """When all sources fail, returns None."""
    # Clear cache first
    cache_file = Path(__file__).parent.parent / "data" / "zq_cache.json"
    if cache_file.exists():
        cache_file.unlink()
    
    with patch("python.backtest.zq_futures._fetch_yahoo", return_value=None), \
         patch("python.backtest.zq_futures._fetch_barchart", return_value=None):
        result = get_zq_price()
        assert result is None
