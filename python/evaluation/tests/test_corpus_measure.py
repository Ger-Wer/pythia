"""Tests for the R4 corpus measurement harness helpers, plus a skip-guarded
integration check against the real corpus on the build host."""

from __future__ import annotations

from pathlib import Path

import pytest

from python.evaluation.corpus_measure import (
    language_shares,
    measure,
    range_from_text,
    statement_manifest,
    visible_text,
)

CORPUS_MANIFEST = (
    Path(__file__).resolve().parents[3]
    / "docs/research/2026-10-22/r4/corpus_manifest.json"
)
CORPUS_ARCHIVE = Path("/home/pythia/pythia-evidence-corpus")


def test_range_plain() -> None:
    text = "The Committee decided to lower the target range for the federal funds rate by 1/2 percentage point to 4-3/4 to 5 percent."
    assert range_from_text(text) == ["4-3/4", "5"]


def test_range_unicode_hyphen_and_at() -> None:
    text = "The Committee decided to maintain the target range for the federal funds rate at 3\u20111/2 to 3\u20113/4 percent."
    assert range_from_text(text) == ["3-1/2", "3-3/4"]


def test_range_zero_bound() -> None:
    text = "decided to lower the target range for the federal funds rate by 1/2 percentage point, to 1 to 1\u20111/4 percent."
    assert range_from_text(text) == ["1", "1-1/4"]


def test_range_absent() -> None:
    assert range_from_text("No rate decision in this text.") is None


def test_language_shares() -> None:
    ru = language_shares("Банк России принял решение сохранить ключевую ставку на уровне двадцати процентов годовых.")
    assert ru["cyrillic"] > 0.8
    zh = language_shares("中国人民银行决定保持贷款市场报价利率不变，维护流动性合理充裕。")
    assert zh["cjk"] > 0.8
    en = language_shares("The Committee decided to maintain the target range for the federal funds rate.")
    assert en["latin"] > 0.9


def test_visible_text_strips_scripts() -> None:
    body = b"<html><head><script>var x = 'hidden';</script></head><body><p>Visible statement text.</p></body></html>"
    text = visible_text(body)
    assert "Visible statement text." in text
    assert "hidden" not in text


def test_statement_manifest_shapes() -> None:
    entry = {"url": "https://www.federalreserve.gov/x.htm"}
    body = b"<html/>"
    man_syn = statement_manifest(entry, body, None)
    assert man_syn["research_provenance"] == "synthetic_fixture"
    assert man_syn["synthetic_fixture"] is True
    assert "parent_feed_reference" not in man_syn
    lineage = {"archive_name": "a", "receipt_id": "r"}
    man_real = statement_manifest(entry, body, lineage)
    assert man_real["research_provenance"] == "research_fixture"
    assert man_real["parent_feed_reference"] == lineage


@pytest.mark.skipif(
    not (CORPUS_MANIFEST.exists() and CORPUS_ARCHIVE.exists()),
    reason="R4 corpus not present on this host",
)
def test_corpus_measurement_has_no_system_errors() -> None:
    report = measure(CORPUS_MANIFEST, CORPUS_ARCHIVE)
    for split in ("tuning", "control"):
        agg = report["splits"][split]
        assert agg["documents"] > 0
        assert agg["errors"] == {}, f"{split} errors: {agg['errors']}"
