"""R5: JEV evidence gate — two layers.

Layer A (screen_document): deterministic document screen implementing the
data-contract section 4 quality rules (challenge pages, JS shells, language
mismatch, implausible numbers, duplicate origin). No model, no network.

Layer B (verify_claim): narrow claim verification by a judge model behind an
injected transport. Verdicts: support / contradiction / insufficient /
context_only. The reply must satisfy a strict JSON schema; every number in
the claim must be checked against a cited fragment. Any schema violation is
converted into an abstention (insufficient + schema_valid=False) — never
into support. A JEV verdict is not an event probability.

Frozen version: JEV_GATE_VERSION and JEV_CONFIG are stamped into every
result. usable_for_forecast stays false everywhere by design.
"""

from __future__ import annotations

import hashlib
import json
import re
import warnings
from dataclasses import dataclass, field

from bs4 import XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

JEV_GATE_VERSION = "pythia-jev-gate/1.0"
JEV_CONFIG = {
    "shell_min_paragraph_chars": 200,
    "shell_max_total_chars": 3000,
    "language_min_share": 0.25,
    "max_plausible_percent": 300.0,
    "judge_temperature": 0,
}

_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "are you a robot",
    "enable javascript and cookies",
    "attention required! | cloudflare",
    "verify you are human",
    "please log in to continue",
    "sign in to continue",
)

_PERCENT_RE = re.compile(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:percent|%)", re.IGNORECASE)


# --------------------------------------------------------------------------
# Layer A — deterministic screen
# --------------------------------------------------------------------------


@dataclass
class ScreenFinding:
    rule_id: str
    severity: str  # "hard" (document unusable) or "review" (flagged)
    detail: str


@dataclass
class ScreenResult:
    passed: bool
    findings: list[ScreenFinding] = field(default_factory=list)
    fingerprint: str | None = None
    gate_version: str = JEV_GATE_VERSION

    def rule_ids(self) -> list[str]:
        return [f.rule_id for f in self.findings]

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "findings": [f.__dict__ for f in self.findings],
            "fingerprint": self.fingerprint,
            "gate_version": self.gate_version,
        }


def _visible_paragraphs(body: bytes) -> list[str]:
    soup = _soup(body)
    return [p.get_text(" ", strip=True) for p in soup.find_all("p")]


def _soup(body: bytes):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return soup


_SENTENCE_ENDERS = (".", "!", "?", "。", "！", "？")


def _has_prose_block(soup) -> bool:
    """A block of >= 200 chars containing at least two sentence enders —
    real article prose, as opposed to concatenated navigation chrome."""
    for el in soup.find_all(["p", "div", "td", "li", "article", "section"]):
        txt = el.get_text(" ", strip=True)
        if len(txt) >= JEV_CONFIG["shell_min_paragraph_chars"] and sum(txt.count(e) for e in _SENTENCE_ENDERS) >= 2:
            return True
    return False


def _has_data_table(soup) -> bool:
    return any(len(t.find_all("td")) >= 20 for t in soup.find_all("table"))


def _visible_text(body: bytes) -> str:
    return _soup(body).get_text(" ", strip=True)


def _shares(text: str) -> dict[str, float]:
    counts = {"cyrillic": 0, "cjk": 0, "latin": 0}
    total = 0
    for ch in text:
        o = ord(ch)
        if 0x0400 <= o <= 0x04FF:
            counts["cyrillic"] += 1
            total += 1
        elif 0x4E00 <= o <= 0x9FFF:
            counts["cjk"] += 1
            total += 1
        elif ch.isalpha() and o < 0x0250:
            counts["latin"] += 1
            total += 1
    if total == 0:
        return {"cyrillic": 0.0, "cjk": 0.0, "latin": 0.0}
    return {k: v / total for k, v in counts.items()}


def document_fingerprint(body: bytes) -> str:
    """Identity fingerprint for duplicate-origin detection.

    Built from the document's substantial paragraphs (>= 200 chars), so the
    same article inside a different syndication wrapper keeps the same
    fingerprint, while different articles (whose paragraph SETS differ) do
    not collide. Falls back to the full normalized text when the document
    has no substantial paragraph.
    """
    paragraphs = [re.sub(r"\s+", " ", p).strip().lower() for p in _visible_paragraphs(body)]
    substantial = sorted(p for p in paragraphs if len(p) >= JEV_CONFIG["shell_min_paragraph_chars"])
    basis = "\n".join(substantial) if substantial else re.sub(r"\s+", " ", _visible_text(body)).strip().lower()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _is_json_body(body: bytes) -> bool:
    head = body.lstrip(b"\xef\xbb\xbf \t\r\n")[:1]
    if head not in (b"{", b"["):
        return False
    try:
        json.loads(body.decode("utf-8"))
        return True
    except (ValueError, UnicodeDecodeError):
        return False


def screen_document(
    body: bytes | None,
    *,
    declared_language: str | None = None,
    seen_fingerprints: set[str] | None = None,
) -> ScreenResult:
    """Deterministic quality screen for one captured document body."""
    if body is None or len(body) == 0:
        return ScreenResult(
            passed=False,
            findings=[ScreenFinding("no_body", "hard", "no captured body (blocked or failed transport)")],
        )
    findings: list[ScreenFinding] = []
    text = _visible_text(body)
    low = text.lower()

    if any(m in low for m in _CHALLENGE_MARKERS):
        findings.append(ScreenFinding("blocked_or_challenge", "hard", "challenge/login marker in visible text"))

    paragraphs = _visible_paragraphs(body)
    longest = max((len(p) for p in paragraphs), default=0)
    soup = _soup(body)
    if (
        not _is_json_body(body)
        and longest < JEV_CONFIG["shell_min_paragraph_chars"]
        and len(text) < JEV_CONFIG["shell_max_total_chars"]
        and not _has_data_table(soup)
    ):
        if _has_prose_block(soup):
            # Chrome can contain prose-like boilerplate (addresses, legal
            # footers). The page carries no article paragraph and no data
            # table, so it is suspect — flagged, not hard-rejected: a short
            # genuine release in an unusual layout must not be killed by a
            # text statistic.
            findings.append(
                ScreenFinding(
                    "shell_suspect",
                    "review",
                    "no article paragraph and no data table; only boilerplate-like prose blocks",
                )
            )
        else:
            findings.append(
                ScreenFinding(
                    "unverified_shell",
                    "hard",
                    f"no paragraph >= {JEV_CONFIG['shell_min_paragraph_chars']} chars; total visible {len(text)} chars",
                )
            )

    if declared_language:
        sh = _shares(text)
        need = {"zh": "cjk", "ru": "cyrillic", "en": "latin"}.get(declared_language)
        if need and sh[need] < JEV_CONFIG["language_min_share"]:
            findings.append(
                ScreenFinding(
                    "language_mismatch",
                    "hard",
                    f"declared {declared_language} but {need} share is {sh[need]:.2f}",
                )
            )

    absurd = [m.group(0) for m in _PERCENT_RE.finditer(text) if float(m.group(1).replace(",", ".")) > JEV_CONFIG["max_plausible_percent"]]
    if absurd:
        findings.append(ScreenFinding("implausible_numbers", "review", f"percent values above plausible bound: {absurd[:3]}"))

    fp = document_fingerprint(body)
    if seen_fingerprints is not None and fp in seen_fingerprints:
        findings.append(ScreenFinding("duplicate_origin", "review", "same normalized-text fingerprint as an earlier document"))

    passed = not any(f.severity == "hard" for f in findings)
    return ScreenResult(passed=passed, findings=findings, fingerprint=fp)


# --------------------------------------------------------------------------
# Layer B — claim verification with a strict schema
# --------------------------------------------------------------------------

VERDICTS = ("support", "contradiction", "insufficient", "context_only")

JUDGE_SYSTEM = (
    "You are a strict evidence verifier inside a forecasting research system. "
    "You judge ONLY from the evidence fragments provided. You never use outside knowledge. "
    "Definitions: 'support' = the fragments directly establish the claim as written, including its numbers. "
    "'contradiction' = the fragments directly establish that the claim as written is false (including a different number). "
    "'insufficient' = the fragments do not contain the information needed to decide the claim. "
    "'context_only' = the fragments are on the claim's topic and give background, but neither establish nor refute the specific assertion "
    "(for example the claim attributes words to a different document or speaker). "
    "Check every number in the claim against the fragments: copy numbers verbatim. "
    "Reply with ONLY a JSON object, no markdown fences, with exactly these keys: "
    "verdict (one of support|contradiction|insufficient|context_only), "
    "cited_fragments (array of fragment indices you relied on), "
    "numbers_checked (array with ONE object per number in the claim: {claim_value, evidence_value, match} — "
    "claim_value is exactly that one number from the claim, e.g. \"4-3/4\"; evidence_value is the corresponding single number copied from a cited fragment, or null when the number is absent from the fragments; "
    "match is true exactly when the two numbers are the same value), "
    "reason (one short sentence)."
)


def build_judge_prompt(claim_text: str, fragments: list[str]) -> tuple[str, str]:
    frag_block = "\n".join(f"[{i}] {frag}" for i, frag in enumerate(fragments))
    user = f"CLAIM:\n{claim_text}\n\nEVIDENCE FRAGMENTS:\n{frag_block}\n\nReturn the JSON verdict now."
    return JUDGE_SYSTEM, user


def _norm_num(value: str | None) -> str | None:
    """Structure-preserving number normalization: '4-3/4' stays '4-3/4';
    decimal trailing zeros are canonicalized ('2.40' -> '2.4', '5.0' -> '5')."""
    if value is None:
        return None
    out = str(value).strip().replace("\u2011", "-").replace("\u2013", "-").replace(" ", "").replace(",", "")
    if re.fullmatch(r"\d+\.\d+", out):
        out = out.rstrip("0").rstrip(".")
    return out or None


_NUMBER_TOKEN_RE = re.compile(r"\d+-\d+/\d+|\d+/\d+|\d+(?:\.\d+)?")


def _hyphen_norm(text: str) -> str:
    return text.replace("\u2011", "-").replace("\u2013", "-").replace("\u2212", "-")


def _fragment_numbers(fragment: str) -> set[str]:
    return {_norm_num(tok) for tok in _NUMBER_TOKEN_RE.findall(_hyphen_norm(fragment))}


@dataclass
class ClaimVerdict:
    verdict: str
    schema_valid: bool
    cited_fragments: list[int] = field(default_factory=list)
    numbers_checked: list[dict] = field(default_factory=list)
    reason: str = ""
    model: str | None = None
    usage: dict | None = None
    gate_version: str = JEV_GATE_VERSION

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "schema_valid": self.schema_valid,
            "cited_fragments": self.cited_fragments,
            "numbers_checked": self.numbers_checked,
            "reason": self.reason,
            "model": self.model,
            "usage": self.usage,
            "gate_version": self.gate_version,
        }


def _abstain(why: str) -> ClaimVerdict:
    return ClaimVerdict(verdict="insufficient", schema_valid=False, reason=f"schema_invalid: {why}")


def parse_and_validate(reply_text: str, claim_numbers: list[str], fragments: list[str]) -> ClaimVerdict:
    """Validate a judge reply against the strict schema and the fragments.

    Any violation becomes an abstention. 'support' additionally requires
    every claim number to be checked with match=true against a cited
    fragment.
    """
    text = reply_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return _abstain("no JSON object in reply")
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        return _abstain(f"JSON parse error: {exc}")
    if not isinstance(obj, dict):
        return _abstain("reply is not a JSON object")
    verdict = obj.get("verdict")
    if verdict not in VERDICTS:
        return _abstain(f"verdict not in enum: {verdict!r}")
    cited = obj.get("cited_fragments")
    if not isinstance(cited, list) or any(not isinstance(i, int) or i < 0 or i >= len(fragments) for i in cited):
        return _abstain("cited_fragments invalid")
    if verdict != "insufficient" and not cited:
        return _abstain("non-abstaining verdict cites no fragment")
    checked = obj.get("numbers_checked")
    if not isinstance(checked, list):
        return _abstain("numbers_checked missing")
    cited_numbers: set[str] = set()
    for i in cited:
        cited_numbers |= _fragment_numbers(fragments[i])

    def _tokens(value) -> set[str]:
        if value is None:
            return set()
        return {_norm_num(tok) for tok in _NUMBER_TOKEN_RE.findall(_hyphen_norm(str(value)))}

    covered: dict[str, bool] = {}
    for item in checked:
        if not isinstance(item, dict) or "claim_value" not in item or "match" not in item:
            return _abstain("numbers_checked item malformed")
        claim_tokens = _tokens(item["claim_value"])
        if not claim_tokens:
            claim_tokens = {_norm_num(item["claim_value"]) or str(item["claim_value"])}
        ev = item.get("evidence_value")
        ev_tokens = _tokens(ev)
        if ev is not None and not ev_tokens <= cited_numbers:
            return _abstain(f"evidence_value {ev!r} not found in cited fragments")
        # Per-token truth is computed here, not taken from the judge: a claim
        # token matches when it appears among the evidence entry's tokens
        # (an evidence value may be a whole phrase, e.g. a range). The
        # judge's own match flag must agree with "all claim tokens found".
        expected_match = ev is not None and all(tok in ev_tokens for tok in claim_tokens)
        if bool(item["match"]) != expected_match:
            bad = _abstain(
                f"match flag inconsistent with values: claim_value={item['claim_value']!r} "
                f"evidence_value={ev!r} judge_match={item['match']} computed={expected_match}"
            )
            bad.numbers_checked = checked
            return bad
        for tok in claim_tokens:
            covered[tok] = ev is not None and tok in ev_tokens
    for num in claim_numbers:
        if (_norm_num(num) or num) not in covered:
            return _abstain(f"claim number {num!r} was not checked")
    if verdict == "support":
        for num in claim_numbers:
            if not covered[_norm_num(num) or num]:
                return _abstain("support verdict with an unmatched claim number")
    reason = obj.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return _abstain("reason missing")
    return ClaimVerdict(
        verdict=verdict,
        schema_valid=True,
        cited_fragments=cited,
        numbers_checked=checked,
        reason=reason.strip()[:400],
    )


def verify_claim(claim_text: str, claim_numbers: list[str], fragments: list[str], judge_fn) -> ClaimVerdict:
    """Run one claim through the judge transport and validate the reply.

    judge_fn(system, user) -> (reply_text, usage_dict | None, model_id).
    Transport exceptions become abstentions, never crashes.
    """
    system, user = build_judge_prompt(claim_text, fragments)
    try:
        reply, usage, model = judge_fn(system, user)
    except Exception as exc:  # transport failure = abstain
        v = _abstain(f"transport error: {type(exc).__name__}")
        return v
    v = parse_and_validate(reply, claim_numbers, fragments)
    v.usage = usage
    v.model = model
    return v
