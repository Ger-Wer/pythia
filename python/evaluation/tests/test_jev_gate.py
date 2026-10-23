"""Tests for the R5 JEV gate: Layer A deterministic screen and Layer B
strict-schema claim validation (fake judge transports)."""

from __future__ import annotations

import json

from python.evaluation.jev_gate import (
    screen_document,
    validate_jev_answers,
    verify_claim_jev,
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


# ---------------- Layer B (TypeSafe JEV protocol) ----------------


def _answers(choice="support", confidence=1.0, noul=0.98):
    return {
        "verdict": {"type": "choice", "choice": choice, "confidence": confidence},
        "supported": {"type": "noul", "noul": noul},
    }


def test_jev_support_valid() -> None:
    v = validate_jev_answers(_answers(), CLAIM_NUMBERS, FRAGMENTS)
    assert v.schema_valid and v.verdict == "support" and not v.escalated


def test_jev_contradiction_valid() -> None:
    v = validate_jev_answers(_answers(choice="contradiction", noul=0.01), CLAIM_NUMBERS, FRAGMENTS)
    assert v.schema_valid and v.verdict == "contradiction"


def test_jev_low_confidence_escalates() -> None:
    v = validate_jev_answers(_answers(confidence=0.6), CLAIM_NUMBERS, FRAGMENTS)
    assert v.schema_valid and v.escalated and v.verdict == "insufficient"


def test_jev_support_with_undecided_noul_escalates() -> None:
    v = validate_jev_answers(_answers(noul=0.5), CLAIM_NUMBERS, FRAGMENTS)
    assert v.escalated and v.verdict == "insufficient"


def test_jev_contradiction_with_high_noul_escalates() -> None:
    v = validate_jev_answers(_answers(choice="contradiction", noul=0.9), CLAIM_NUMBERS, FRAGMENTS)
    assert v.escalated and v.verdict == "insufficient"


def test_jev_support_blocked_by_code_number_check() -> None:
    v = validate_jev_answers(_answers(), ["9-1/2", "9-3/4"], FRAGMENTS)
    assert v.escalated and v.verdict == "insufficient"
    assert "code number check" in v.reason


def test_jev_unicode_hyphen_numbers_pass_code_check() -> None:
    frags = ["The Committee decided to lower the target range to 1 to 1\u20111/4 percent."]
    v = validate_jev_answers(_answers(noul=0.97), ["1", "1-1/4"], frags)
    assert v.schema_valid and v.verdict == "support"


def test_jev_bad_schema_abstains() -> None:
    assert not validate_jev_answers({"verdict": {"choice": "maybe", "confidence": 1.0}, "supported": {"noul": 0.9}}, CLAIM_NUMBERS, FRAGMENTS).schema_valid
    assert not validate_jev_answers({"supported": {"noul": 0.9}}, CLAIM_NUMBERS, FRAGMENTS).schema_valid
    assert not validate_jev_answers(_answers(confidence=1.5), CLAIM_NUMBERS, FRAGMENTS).schema_valid
    assert not validate_jev_answers(_answers(noul=float("nan")), CLAIM_NUMBERS, FRAGMENTS).schema_valid


def test_jev_transport_error_abstains() -> None:
    def boom(state: str, questions: dict):
        raise RuntimeError("network down")

    v = verify_claim_jev("claim", CLAIM_NUMBERS, FRAGMENTS, boom)
    assert not v.schema_valid and v.verdict == "insufficient"


def test_jev_verify_happy_path() -> None:
    def judge(state: str, questions: dict):
        assert "verdict" in questions and "supported" in questions
        return _answers(), {"input_tokens": 100, "output_tokens": 20}, "jev-1.13.0"

    v = verify_claim_jev("claim", CLAIM_NUMBERS, FRAGMENTS, judge)
    assert v.schema_valid and v.verdict == "support" and v.model == "jev-1.13.0"
