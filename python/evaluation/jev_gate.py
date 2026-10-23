"""R5: JEV evidence gate — two layers.

Layer A (screen_document): deterministic document screen implementing the
data-contract section 4 quality rules (challenge pages, JS shells, language
mismatch, implausible numbers, duplicate origin). No model, no network.

Layer B (verify_claim_jev): narrow claim verification by the real TypeSafe
JEV judge (System One, model pinned in JEV_CONFIG) behind an injected
transport. One batched call per claim: a Choice verdict (support /
contradiction / insufficient / context_only) plus a Noul support check.
Code enforces the R0 admission rules around it: strict answer schema,
the owner escalation rule (confidence < 0.75 or Noul in the undecided
band abstains), and a deterministic number check (numbers are verified
by code, not by the judge). Any violation becomes an abstention — never
a support. A JEV verdict is not an event probability.

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

JEV_GATE_VERSION = "pythia-jev-gate/2.0"
JEV_CONFIG = {
    "shell_min_paragraph_chars": 200,
    "shell_max_total_chars": 3000,
    "language_min_share": 0.25,
    "max_plausible_percent": 300.0,
    # Layer B judge: the real TypeSafe JEV (System One), version pinned as
    # in the R0 research batch. Escalation rule from the HermesTrader house
    # rules / R0: confidence below the floor, or a Noul inside the undecided
    # band, abstains instead of deciding.
    "judge_provider": "typesafe",
    "judge_endpoint": "https://api.typesafe.ai/v1/systemone",
    "judge_model": "jev-1.13.0",
    "confidence_floor": 0.75,
    "noul_undecided": (0.40, 0.60),
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
# Layer B — claim verification by the TypeSafe JEV judge
# --------------------------------------------------------------------------

VERDICTS = ("support", "contradiction", "insufficient", "context_only")

JEV_VERDICT_CRITERIA = {
    "support": "The evidence directly establishes the claim as written, including its numbers.",
    "contradiction": "The evidence directly establishes that the claim as written is false, including stating a different number.",
    "insufficient": "The evidence does not contain the information needed to decide the claim.",
    "context_only": "The evidence is on the claim's topic and gives background, but neither establishes nor refutes the specific assertion (for example, the claim concerns a different document or speaker).",
}


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
    confidence: float | None = None
    noul: float | None = None
    escalated: bool = False
    reason: str = ""
    model: str | None = None
    usage: dict | None = None
    gate_version: str = JEV_GATE_VERSION

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "schema_valid": self.schema_valid,
            "confidence": self.confidence,
            "noul": self.noul,
            "escalated": self.escalated,
            "reason": self.reason,
            "model": self.model,
            "usage": self.usage,
            "gate_version": self.gate_version,
        }


def _abstain(why: str) -> ClaimVerdict:
    return ClaimVerdict(verdict="insufficient", schema_valid=False, reason=f"schema_invalid: {why}")


def build_jev_questions(claim_text: str) -> dict:
    """The frozen per-claim JEV batch: one Choice verdict + one Noul check."""
    return {
        "verdict": {
            "type": "choice",
            "instructions": f"Judge ONLY from the evidence text. Claim: {claim_text}",
            "criteria": JEV_VERDICT_CRITERIA,
        },
        "supported": {
            "type": "noul",
            "instructions": f"The following claim is directly supported by the evidence text, including its numbers. Claim: {claim_text}",
        },
    }


def _finite01(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")) or not (0.0 <= out <= 1.0):
        return None
    return out


def validate_jev_answers(answers, claim_numbers: list[str], fragments: list[str]) -> ClaimVerdict:
    """Apply the R0 admission rules to one JEV batch response.

    Schema: both answers present and well-typed; Choice in the verdict enum;
    confidence and Noul finite in [0, 1]. Escalation: confidence below the
    floor, or the verdict and the Noul disagreeing across the undecided
    band, abstains. Numbers: code-verified — a support verdict requires
    every claim number to occur in the evidence fragments.
    """
    if not isinstance(answers, dict):
        return _abstain("answers is not an object")
    verdict_a = answers.get("verdict")
    noul_a = answers.get("supported")
    if not isinstance(verdict_a, dict) or not isinstance(noul_a, dict):
        return _abstain("missing verdict or supported answer")
    choice = verdict_a.get("choice")
    if choice not in VERDICTS:
        return _abstain(f"choice not in enum: {choice!r}")
    confidence = _finite01(verdict_a.get("confidence"))
    if confidence is None:
        return _abstain("confidence missing, non-finite, or out of [0,1]")
    noul = _finite01(noul_a.get("noul"))
    if noul is None:
        return _abstain("noul missing, non-finite, or out of [0,1]")
    floor = JEV_CONFIG["confidence_floor"]
    band_lo, band_hi = JEV_CONFIG["noul_undecided"]
    v = ClaimVerdict(verdict=choice, schema_valid=True, confidence=confidence, noul=noul)
    if confidence < floor:
        v.verdict = "insufficient"
        v.escalated = True
        v.reason = f"escalated: choice confidence {confidence} below floor {floor}"
        return v
    if choice == "support" and noul < band_hi:
        v.verdict = "insufficient"
        v.escalated = True
        v.reason = f"escalated: support choice but noul {noul} not above the undecided band"
        return v
    if choice == "contradiction" and noul > band_lo:
        v.verdict = "insufficient"
        v.escalated = True
        v.reason = f"escalated: contradiction choice but noul {noul} not below the undecided band"
        return v
    if choice == "support" and claim_numbers:
        evidence_numbers: set[str] = set()
        for frag in fragments:
            evidence_numbers |= _fragment_numbers(frag)
        missing = [n for n in claim_numbers if (_norm_num(n) or n) not in evidence_numbers]
        if missing:
            v.verdict = "insufficient"
            v.escalated = True
            v.reason = f"code number check: claim numbers absent from evidence: {missing}"
            return v
    v.reason = f"jev choice={choice} confidence={confidence} noul={noul}"
    return v


def verify_claim_jev(claim_text: str, claim_numbers: list[str], fragments: list[str], jev_judge_fn) -> ClaimVerdict:
    """Run one claim through the JEV transport and the admission rules.

    jev_judge_fn(state, questions) -> (answers_dict, usage_dict | None, model_id).
    Transport exceptions become abstentions, never crashes.
    """
    state = "\n".join(f"[{i}] {frag}" for i, frag in enumerate(fragments))
    try:
        answers, usage, model = jev_judge_fn(state, build_jev_questions(claim_text))
    except Exception as exc:  # noqa: BLE001 - transport failure = abstain
        return _abstain(f"transport error: {type(exc).__name__}")
    v = validate_jev_answers(answers, claim_numbers, fragments)
    v.usage = usage
    v.model = model
    return v
