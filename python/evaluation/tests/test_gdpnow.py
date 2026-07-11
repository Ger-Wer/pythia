from pathlib import Path

import feedparser

from python.evaluation import gdpnow


def test_parse_entry_extracts_first_estimate() -> None:
    description = "The GDPNow estimate is 1.2 percent, down from 2.5 percent."

    result = gdpnow._parse_entry(description)

    assert result == 1.2


def test_gdpnow_vintage_returns_multiple_entries(fixtures_dir: Path, monkeypatch) -> None:
    feed = feedparser.parse((fixtures_dir / "gdpnow.rss").read_text())
    monkeypatch.setattr(gdpnow.feedparser, "parse", lambda _: feed)

    result = gdpnow.get_gdpnow_vintage(limit=3)

    assert result == [
        {"data": 1.2, "date": "Wed, 01 Jul 2026 12:11:00 EST"},
        {"data": 2.5, "date": "Thu, 25 Jun 2026 09:51:00 EST"},
        {"data": 3.0, "date": "Wed, 17 Jun 2026 10:55:00 EST"},
    ]


def test_latest_gdpnow_uses_first_feed_entry(fixtures_dir: Path, monkeypatch) -> None:
    feed = feedparser.parse((fixtures_dir / "gdpnow.rss").read_text())
    monkeypatch.setattr(gdpnow.feedparser, "parse", lambda _: feed)
    monkeypatch.setattr(gdpnow, "_load_cache", lambda: {})
    monkeypatch.setattr(gdpnow, "_save_cache", lambda cache: None)

    result = gdpnow.get_latest_gdpnow()

    assert result is not None
    assert result["data"] == 1.2
    assert result["date"] == "Wed, 01 Jul 2026 12:11:00 EST"
    assert isinstance(result["ts"], float)


def test_malformed_feed_returns_none(monkeypatch) -> None:
    malformed_feed = feedparser.parse("<rss><channel></rss>")
    monkeypatch.setattr(gdpnow.feedparser, "parse", lambda _: malformed_feed)
    monkeypatch.setattr(gdpnow, "_load_cache", lambda: {})

    result = gdpnow.get_latest_gdpnow()

    assert result is None
