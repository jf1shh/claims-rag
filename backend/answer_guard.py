"""Post-generation answer guard: catch adjudication claims and off-allowlist contacts.

`docs/adversarial-evaluation.md` shows the generator following instructions planted in
retrieved documents: it declared claims approved, paid, denied or referred, and it handed
users attacker-supplied URLs and email addresses, while the structured contract still
reported `decision_status: not_a_decision`. This module is the independent post-generation
check for those two classes of assertion in answer text.

It is deliberately pure: standard library only, no backend imports, no I/O, no model. The
input is the answer string and (optionally) the claim record's status; the output is a list
of `GuardFinding`. Wiring the findings into the API response and the structured contract is
the caller's job.

Text is NFKC-normalised before matching so fullwidth or curly variants do not slip past the
detectors, and skip rules are evaluated per clause so one hedged clause cannot hide an
assertion in the next one.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

OutcomeKind = Literal["approved", "paid", "denied", "referred", "closed"]

# Modal, conditional or negated language marks a rule, a hypothetical or a denial of the
# outcome rather than an assertion that it happened. Two halves, because a modal inside a
# clause that names *this* claim is a decision ("this claim must be denied") while the same
# modal in a general rule is policy ("Suspicious claims must be referred to the SIU").
SKIP_ALWAYS = r"\b(not|no(?!\.\s*\d)|never|cannot|can't|isn't|wasn't|hasn't|haven't|hadn't|won't|doesn't|didn't|neither|nor|without|pending|until|unless|if|whether)\b"
# `may(?!\s+\d)` so a dated "approved in May 2026" is a month, not a modal.
SKIP_MODAL = r"\b(would|should|must|may(?!\s+\d)|might|could|shall|requires?|required)\b"
# Combined rule, kept for callers that want the whole skip test in one pattern.
SKIP = SKIP_ALWAYS + "|" + SKIP_MODAL

# A clause that refers to the claim in hand rather than to a class of claims.
SPECIFIC_CLAIM = r"\b(this|that|the\s+insured'?s?)\s+(claim|file|loss)\b|#\s?\d{4}-\d{3,}|\bclaim\s+(no\.?|number)\s*\d{4}-\d{3,}"

OUTCOME_PATTERNS: dict[str, list[str]] = {
    "approved": [
        r"\b(claim|file|loss|settlement|payment|payout)\b.{0,60}\b(approved|authori[sz]ed)\b",
        r"\b(approved|authori[sz]ed)\b.{0,40}\b(payout|payment|settlement)\b.{0,20}\$\s?\d",
        r"\b(approved|authori[sz]ed)\s+for\s+\$\s?\d",
        r"\bmarked\s+as\s+approved\b",
        r"\bwe\s+(have\s+)?(approved|authori[sz]ed)\b",
    ],
    "paid": [
        r"\b(has|have|had|was|were|been|is)\s+(already\s+|now\s+|fully\s+)?(been\s+)?paid\b",
        r"\bpaid\s+in\s+full\b",
        r"\bpayment\s+(has\s+been|was|is)\s+(issued|made|sent|released|disbursed|complete)",
        r"\bpayment\s+of\s+\$\s?\d[\d,.]*\s+(has\s+been|was|is)\s+(issued|made|sent|released|disbursed)",
        r"\bfunds?\s+(has|have|had|was|were|is|are)\s+(been\s+)?released\b",
        r"\bwire\s+ref(erence)?\b\W{0,5}(no\.?|number|#|:)?\s*[A-Z0-9-]*\d[A-Z0-9-]{3,}",
        r"\b(has|have|had|was|were|been)\s+(already\s+)?(been\s+)?disbursed\b",
        r"\bwe\s+(have\s+)?paid\b",
    ],
    "denied": [
        r"\b(claim|loss)\b.{0,40}\b(is|was|has\s+been|been|be)\s+(denied|declined|rejected)\b",
        r"\bdenial[\s-]+code\b",
        r"\bcoverage\s+status\s*:\s*(denied|declined)",
        r"\bwe\s+(have\s+)?(denied|declined|rejected)\b",
    ],
    "referred": [
        r"\b(has\s+been|was|is|been)\s+referred\s+to\s+(the\s+)?(siu|special\s+investigat)",
        r"\bsiu\s+docket\b",
        r"\b(siu|investigat\w*)\b.{0,40}\bdocket\b",
        r"\bwe\s+(have\s+)?referred\b.{0,60}\b(siu|special\s+investigat)",
    ],
    "closed": [
        r"\b(claim|file)\s+(is|was|has\s+been)\s+(now\s+)?(closed|settled)\b",
        r"\b(claim|loss)\s+(was\s+)?settled\s+for\s+\$\s?\d",
        r"\bfinal\s+determination\s*(:|is\b|was\b)\s*\S",
        r"\bsettlement\s+(is|was|has\s+been)\s+(approved|finali[sz]ed|authori[sz]ed)\b",
    ],
}

# A claim already flagged for investigation makes a "referred" assertion consistent with
# the record; nothing else suppresses an outcome finding.
_CONSISTENT_STATUS_MARKERS = ("siu", "investigation")

MAX_EXCERPT_CHARS = 240
_EXCERPT_TRAILING_CHARS = ".,;:)"

_EMPHASIS_RE = re.compile(r"[*_`]")
_SPACE_RUN_RE = re.compile(r"[ \t]+")
_CURLY_APOSTROPHE_TRANSLATION = str.maketrans({"\u2018": "'", "\u2019": "'"})
# Never end a sentence after "no."/"No." -- it is a claim number in "Claim no. 2026-99382".
_SENTENCE_SPLIT_RE = re.compile(r"\n|(?<=[.!?])(?<!\bno\.)(?<!\bNo\.)\s+")
_LEADING_MARKER_RE = re.compile(r"^[-#>]|^\d+\.")
_CLAUSE_SPLIT_RE = re.compile(r"[;:]")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"(?:hxxps?|https?)://[^\s)\]>\"']+", re.IGNORECASE)
_WWW_RE = re.compile(r"\bwww\.[^\s)\]>\"']+", re.IGNORECASE)
_MD_DEST_RE = re.compile(r"\]\(([^)\s]+)\)")
_AT_BRACKET_EMAIL_RE = re.compile(
    r"\b[\w.+-]+\s*(?:\[at\]|\(at\))\s*[\w-]+(?:\s*(?:\.|\[dot\]|\(dot\)|\sdot\s)\s*[\w-]+)+",
    re.IGNORECASE,
)
_AT_SPELLED_EMAIL_RE = re.compile(
    r"\b[\w.+-]+\s+at\s+[\w-]+(?:\s+dot\s+[\w-]+)+", re.IGNORECASE
)
_CONTACT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", _EMAIL_RE),
    ("url", _URL_RE),
    ("url", _WWW_RE),
)
_OBFUSCATED_EMAIL_PATTERNS = (_AT_BRACKET_EMAIL_RE, _AT_SPELLED_EMAIL_RE)
_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://")
_HXXP_RE = re.compile(r"^hxxp", re.IGNORECASE)
_AT_TOKEN_RE = re.compile(r"\[at\]|\(at\)|\bat\b", re.IGNORECASE)
_DOT_TOKEN_RE = re.compile(r"\[dot\]|\(dot\)|\bdot\b", re.IGNORECASE)

_SKIP_ALWAYS_RE = re.compile(SKIP_ALWAYS, re.IGNORECASE)
_SKIP_MODAL_RE = re.compile(SKIP_MODAL, re.IGNORECASE)
_SPECIFIC_CLAIM_RE = re.compile(SPECIFIC_CLAIM, re.IGNORECASE)
_OUTCOME_RES: dict[str, tuple[re.Pattern[str], ...]] = {
    category: tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)
    for category, patterns in OUTCOME_PATTERNS.items()
}


@dataclass(frozen=True)
class GuardFinding:
    """One unsupported assertion found in an answer."""

    kind: Literal["claim_outcome", "external_contact"]
    category: str
    excerpt: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "category": self.category,
            "excerpt": self.excerpt,
            "reason": self.reason,
        }


def _clean(text: str) -> str:
    """NFKC-normalise, unify apostrophes, drop emphasis characters, collapse spaces."""
    normalized = unicodedata.normalize("NFKC", text).translate(_CURLY_APOSTROPHE_TRANSLATION)
    return _SPACE_RUN_RE.sub(" ", _EMPHASIS_RE.sub("", normalized))


def split_sentences(text: str) -> list[str]:
    """Split cleaned text into sentences, one markdown bullet or heading line each."""
    sentences: list[str] = []
    for piece in _SENTENCE_SPLIT_RE.split(_clean(text)):
        sentence = _LEADING_MARKER_RE.sub("", piece.strip()).strip()
        if sentence:
            sentences.append(sentence)
    return sentences


def _url_hostname(raw: str) -> str:
    """Hostname of a URL-looking string, undefanging a leading `hxxp` first."""
    target = _HXXP_RE.sub("http", raw, count=1)
    if not target.lower().startswith(("http://", "https://")):
        target = f"http://{target}"
    return urlsplit(target).hostname or ""


def _domain_of(category: str, raw: str) -> str:
    if category == "email":
        domain = raw.rsplit("@", 1)[-1]
    else:
        domain = _url_hostname(raw)
    return domain.lower().rstrip(".,;:")


def _obfuscated_domain(raw: str) -> str:
    """Domain of `user [at] host [dot] tld`: dot tokens become `.`, spaces drop."""
    parts = _AT_TOKEN_RE.split(raw, maxsplit=1)
    tail = parts[1] if len(parts) > 1 else raw
    return _DOT_TOKEN_RE.sub(".", tail).replace(" ", "").lower().strip(".,;:")


def _is_allowed(domain: str, allowed_domains: frozenset[str]) -> bool:
    if not domain:
        return False
    for entry in allowed_domains:
        entry = entry.lower()
        if domain == entry or domain.endswith("." + entry):
            return True
    return False


def find_external_contacts(
    text: str, allowed_domains: frozenset[str] = frozenset()
) -> list[GuardFinding]:
    """Report every distinct URL/email whose domain is not on the allowlist.

    The scan runs on NFKC-normalised text (so `＠` folds to `@`), accepts defanged
    schemes (`hxxp`), scheme-less markdown destinations, and `name at host dot tld`
    spellings of an address.
    """
    haystack = unicodedata.normalize("NFKC", text)
    matches: list[tuple[int, str, str, str]] = []
    for category, pattern in _CONTACT_PATTERNS:
        for match in pattern.finditer(haystack):
            raw = match.group(0)
            matches.append((match.start(), category, raw, _domain_of(category, raw)))
    for match in _MD_DEST_RE.finditer(haystack):
        destination = match.group(1)
        if _SCHEME_RE.match(destination) or destination[:1] in ("/", "#", "."):
            continue
        if "." not in destination:
            continue
        matches.append((match.start(1), "url", destination, _domain_of("url", destination)))
    for pattern in _OBFUSCATED_EMAIL_PATTERNS:
        for match in pattern.finditer(haystack):
            raw = match.group(0)
            matches.append((match.start(), "email", raw, _obfuscated_domain(raw)))
    matches.sort(key=lambda item: item[0])

    findings: list[GuardFinding] = []
    seen: set[str] = set()
    for _, category, raw, domain in matches:
        excerpt = raw.rstrip(_EXCERPT_TRAILING_CHARS)
        key = excerpt.lower()
        if not excerpt or key in seen:
            continue
        seen.add(key)
        if _is_allowed(domain, allowed_domains):
            continue
        findings.append(
            GuardFinding(
                kind="external_contact",
                category=category,
                excerpt=excerpt,
                reason=f"external {category} to {domain} is not on the answer allowlist",
            )
        )
    return findings


def _clause_is_skipped(clause: str) -> bool:
    """Negation always skips; a modal skips unless the clause names this claim."""
    if _SKIP_ALWAYS_RE.search(clause):
        return True
    return bool(_SKIP_MODAL_RE.search(clause)) and not _SPECIFIC_CLAIM_RE.search(clause)


def find_claim_outcomes(text: str, record_status: str | None = None) -> list[GuardFinding]:
    """Report sentences asserting a claim outcome the record does not show.

    Skip rules apply per clause (`;`/`:`) so a hedged clause no longer hides an
    assertion in the next one, and the excerpt is always the whole sentence. A
    sentence ending in `?` is an echoed question, never an assertion, so it is
    dropped whole.
    """
    suppressed: frozenset[str] = frozenset()
    if record_status:
        status = record_status.lower()
        if any(marker in status for marker in _CONSISTENT_STATUS_MARKERS):
            suppressed = frozenset({"referred"})

    findings: list[GuardFinding] = []
    for sentence in split_sentences(text):
        if sentence.endswith("?"):
            continue
        excerpt = sentence[:MAX_EXCERPT_CHARS]
        found: set[str] = set()
        for clause in _CLAUSE_SPLIT_RE.split(sentence):
            if _clause_is_skipped(clause):
                continue
            for category, patterns in _OUTCOME_RES.items():
                if category in suppressed or category in found:
                    continue
                if any(pattern.search(clause) for pattern in patterns):
                    found.add(category)
                    findings.append(
                        GuardFinding(
                            kind="claim_outcome",
                            category=category,
                            excerpt=excerpt,
                            reason=(
                                f"asserts a claim outcome ({category}) "
                                "that the claim record does not show"
                            ),
                        )
                    )
    return findings


def check_answer(
    text: str,
    *,
    record_status: str | None = None,
    allowed_domains: frozenset[str] = frozenset(),
) -> list[GuardFinding]:
    """Claim-outcome findings first, then external-contact findings."""
    return find_claim_outcomes(text, record_status) + find_external_contacts(text, allowed_domains)


GuardMode = Literal["withhold", "flag", "off"]

_WITHHELD_WHAT = {
    "claim_outcome": "a claim outcome (approved, paid, denied, referred or closed) that the claim record does not show",
    "external_contact": "a link or contact address outside the answer allowlist",
}


def withheld_text(findings: list[GuardFinding]) -> str:
    """The notice shown in place of a withheld answer -- no generated text survives."""
    kinds = [kind for kind in _WITHHELD_WHAT if any(finding.kind == kind for finding in findings)]
    what = " and ".join(_WITHHELD_WHAT[kind] for kind in kinds)
    return (
        f"This answer was withheld for human review: the generated text asserted {what}. "
        "Claim outcomes are decided by an adjuster, not by this assistant, and source documents can "
        "contain instructions that should not be followed. Review the retrieved sources listed below directly."
    )


def apply_guard(
    result: dict,
    *,
    mode: GuardMode,
    record_status: str | None = None,
    allowed_domains: frozenset[str] = frozenset(),
) -> dict:
    """Return `result` with the guard applied; the input dict is never mutated.

    No findings (or mode "off", or an error result) returns `result` unchanged. Otherwise a
    `guard` report ({"action", "findings"}) is attached; in "withhold" mode the answer text is
    replaced by `withheld_text`, so the unsupported assertion never reaches the caller.
    """
    if mode == "off" or result.get("status") == "error":
        return result
    findings = check_answer(
        str(result.get("answer") or ""), record_status=record_status, allowed_domains=allowed_domains
    )
    if not findings:
        return result
    action = "withheld" if mode == "withhold" else "flagged"
    guarded = {**result, "guard": {"action": action, "findings": [finding.to_dict() for finding in findings]}}
    if action == "withheld":
        guarded["answer"] = withheld_text(findings)
    return guarded
