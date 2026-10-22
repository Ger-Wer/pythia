"""Tests for the R5 JEV gate: Layer A deterministic screen and Layer B
strict-schema claim validation (fake judge transports)."""

from __future__ import annotations

import json

from python.evaluation.jev_gate import (
    parse_and_validate,
    screen_document,
    verify_claim,
)

LONG_P = "The Committee decided to maintain the target range for the federal funds rate at 3-1/2 to 3-3/4 percent. " * 3
FRAGMENTS = [
    "The Committee decided to lower the target range for the federal funds rate by 1/4 percentage point to 4-3/4 to 5 percent.",
    "The Committee will continue reducing its holdings of Treasury securities.",
]
CLAIM_NUMBERS = ["4-3/4", "5"]


def _page(*paragraphs: str) -> bytes:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f"<html><body>{body}</body></html>".encode()


def _reply(**kw) -> str:
    base = {
        "verdict": "support",
        "cited_fragments": [0],
        "numbers_checked": [
            {"claim_value": "4-3/4", "evidence_value": "4-3/4", "match": True},
            {"claim_value": "5", "evidence_value": "5", "match": True},
        ],
        "reason": "The decision paragraph states the range.",
    }
    base.update(kw)
    return json.dumps(base)


# ---------------- Layer A ----------------


def test_screen_normal_document_passes() -> None:
    r = screen_document(_page(LONG_P), declared_language="en")
    assert r.passed and r.findings == []


def test_screen_shell_fails() -> None:
    r = screen_document(b"<html><body><nav>Home News About</nav><p>Loading</p></body></html>", declared_language="ru")
    assert not r.passed and "unverified_shell" in r.rule_ids()


def test_screen_challenge_fails() -> None:
    r = screen_document(_page("Just a moment... Checking your browser before accessing the site. " + LONG_P))
    assert not r.passed and "blocked_or_challenge" in r.rule_ids()


def test_screen_language_mismatch() -> None:
    r = screen_document(_page(LONG_P), declared_language="zh")
    assert not r.passed and "language_mismatch" in r.rule_ids()
    zh = "中国人民银行决定保持货币政策稳定，维护流动性合理充裕，促进经济平稳健康发展。" * 8
    r2 = screen_document(_page(zh), declared_language="zh")
    assert "language_mismatch" not in r2.rule_ids()


def test_screen_implausible_numbers_flagged_not_hard() -> None:
    r = screen_document(_page(LONG_P + " Inflation suddenly hit 450 percent, officials said, in a fabricated scenario."))
    assert "implausible_numbers" in r.rule_ids()
    assert r.passed  # review severity: claim layer must still refuse those numbers


def test_screen_duplicate_origin() -> None:
    body = _page(LONG_P)
    seen: set[str] = set()
    r1 = screen_document(body, seen_fingerprints=seen)
    seen.add(r1.fingerprint)
    r2 = screen_document(body, seen_fingerprints=seen)
    assert "duplicate_origin" in r2.rule_ids()


def test_screen_duplicate_despite_wrapper() -> None:
    original = _page(LONG_P)
    wrapped = (
        "<html><body><header>Syndication Portal — Markets News</header>"
        f"<div class='ad'>Subscribe now</div><article>{LONG_P}</article>"
        "<footer>Copyright wire service</footer></body></html>"
    ).encode()
    wrapped = wrapped.replace(LONG_P.encode(), f"<p>{LONG_P}</p>".encode())
    seen: set[str] = set()
    r1 = screen_document(original, seen_fingerprints=seen)
    seen.add(r1.fingerprint)
    r2 = screen_document(wrapped, seen_fingerprints=seen)
    assert "duplicate_origin" in r2.rule_ids()


def test_screen_json_document_is_not_a_shell() -> None:
    body = b'{"data": [{"id": "org/model", "downloads": 12345}], "page": 1}'
    r = screen_document(body, declared_language="en")
    assert "unverified_shell" not in r.rule_ids() and r.passed


def test_screen_no_body() -> None:
    r = screen_document(None)
    assert not r.passed and "no_body" in r.rule_ids()


# ---------------- Layer B ----------------


def test_valid_support() -> None:
    v = parse_and_validate(_reply(), CLAIM_NUMBERS, FRAGMENTS)
    assert v.schema_valid and v.verdict == "support"


def test_fenced_reply_parses() -> None:
    v = parse_and_validate("```json\n" + _reply() + "\n```", CLAIM_NUMBERS, FRAGMENTS)
    assert v.schema_valid and v.verdict == "support"


def test_support_with_unmatched_number_abstains() -> None:
    bad = _reply(numbers_checked=[
        {"claim_value": "4-3/4", "evidence_value": "4-3/4", "match": True},
        {"claim_value": "5", "evidence_value": None, "match": False},
    ])
    v = parse_and_validate(bad, CLAIM_NUMBERS, FRAGMENTS)
    assert not v.schema_valid and v.verdict == "insufficient"


def test_evidence_number_not_in_fragment_abstains() -> None:
    bad = _reply(numbers_checked=[
        {"claim_value": "4-3/4", "evidence_value": "9-1/2", "match": False},
        {"claim_value": "5", "evidence_value": "5", "match": True},
    ], verdict="contradiction")
    v = parse_and_validate(bad, CLAIM_NUMBERS, FRAGMENTS)
    assert not v.schema_valid


def test_valid_contradiction() -> None:
    reply = _reply(
        verdict="contradiction",
        numbers_checked=[
            {"claim_value": "5-1/4", "evidence_value": "4-3/4", "match": False},
            {"claim_value": "5-1/2", "evidence_value": "5", "match": False},
        ],
    )
    v = parse_and_validate(reply, ["5-1/4", "5-1/2"], FRAGMENTS)
    assert v.schema_valid and v.verdict == "contradiction"


def test_bad_verdict_enum_abstains() -> None:
    v = parse_and_validate(_reply(verdict="probably_true"), CLAIM_NUMBERS, FRAGMENTS)
    assert not v.schema_valid and v.verdict == "insufficient"


def test_insufficient_without_citations_is_valid() -> None:
    reply = _reply(verdict="insufficient", cited_fragments=[], numbers_checked=[
        {"claim_value": "4-3/4", "evidence_value": None, "match": False},
        {"claim_value": "5", "evidence_value": None, "match": False},
    ])
    v = parse_and_validate(reply, CLAIM_NUMBERS, FRAGMENTS)
    assert v.schema_valid and v.verdict == "insufficient"


def test_missing_claim_number_check_abstains() -> None:
    reply = _reply(numbers_checked=[{"claim_value": "4-3/4", "evidence_value": "4-3/4", "match": True}])
    v = parse_and_validate(reply, CLAIM_NUMBERS, FRAGMENTS)
    assert not v.schema_valid


def test_transport_error_abstains() -> None:
    def boom(system: str, user: str):
        raise RuntimeError("network down")

    v = verify_claim("claim", CLAIM_NUMBERS, FRAGMENTS, boom)
    assert not v.schema_valid and v.verdict == "insufficient"


def test_verify_claim_happy_path() -> None:
    def judge(system: str, user: str):
        assert "support" in system
        return _reply(), {"prompt_tokens": 10, "completion_tokens": 5}, "fake/model"

    v = verify_claim("claim", CLAIM_NUMBERS, FRAGMENTS, judge)
    assert v.schema_valid and v.verdict == "support" and v.model == "fake/model"


def test_grouped_number_entry_is_valid() -> None:
    reply = _reply(numbers_checked=[
        {"claim_value": "4-3/4 to 5 percent", "evidence_value": "4-3/4 to 5 percent", "match": True},
    ])
    v = parse_and_validate(reply, CLAIM_NUMBERS, FRAGMENTS)
    assert v.schema_valid and v.verdict == "support"


def test_trailing_zero_decimal_matches() -> None:
    reply = _reply(
        verdict="insufficient",
        cited_fragments=[0],
        numbers_checked=[
            {"claim_value": "2.4", "evidence_value": None, "match": False},
            {"claim_value": "5", "evidence_value": "5.0", "match": True},
        ],
    )
    # '5' vs evidence '5.0' canonicalize equal; claim number '2.4' checked as absent
    reply2 = _reply(numbers_checked=[
        {"claim_value": "4-3/4", "evidence_value": "4-3/4", "match": True},
        {"claim_value": "5", "evidence_value": "5.0", "match": True},
    ])
    v = parse_and_validate(reply2, CLAIM_NUMBERS, FRAGMENTS)
    assert v.schema_valid and v.verdict == "support"


def test_single_number_with_range_phrase_evidence() -> None:
    reply = _reply(numbers_checked=[
        {"claim_value": "4-3/4", "evidence_value": "4-3/4 to 5 percent", "match": True},
        {"claim_value": "5", "evidence_value": "4-3/4 to 5 percent", "match": True},
    ])
    v = parse_and_validate(reply, CLAIM_NUMBERS, FRAGMENTS)
    assert v.schema_valid and v.verdict == "support"


def test_unicode_hyphen_range_matches() -> None:
    frags = ["The Committee decided to lower the target range for the federal funds rate to 1 to 1\u20111/4 percent."]
    reply = _reply(numbers_checked=[
        {"claim_value": "1 to 1-1/4 percent", "evidence_value": "1 to 1\u20111/4 percent", "match": True},
    ])
    v = parse_and_validate(reply, ["1", "1-1/4"], frags)
    assert v.schema_valid and v.verdict == "support"
