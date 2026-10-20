"""Deterministic figure check: every amount, percentage and date in a live answer must trace back.

On the live path the claims tools compute the labor, parts, total-loss and payout figures
(`backend/claims_tools.py`) and the model only explains them. A model can still restate a
figure wrongly -- a rounded cap, a transposed total, a date it invented. This check pulls
the money, percent and date figures out of the answer and looks for each one, after
normalization, against two tiers of evidence:

- `trusted`: the computed section and the claim dossier (structured claim data). A figure
  found here is `verified`.
- `sources`: retrieved document text. This is untrusted -- a shop-supplied PDF can plant a
  figure the model then repeats -- so a figure found only here is `source_only`, not
  verified, and gets its own notice line.

A figure found in neither tier is `unverified`. Nothing is rewritten.

The match is exact after normalization, with no tolerance: the computed figures are
cent-exact, so a figure that is close but not equal is exactly the error this exists to catch.

Normalization notes:
- Money: a leading `-`, a `-` directly after `$`, or wrapping the whole amount in parentheses
  (the accounting convention) are all treated as negative. Magnitude words/abbreviations
  (`k`, `m`/`mm`, `b`/`bn`, `thousand`, `million`, `billion`) scale the value. `USD` and the
  word `dollars` are accepted in place of `$`. A malformed thousands grouping (e.g. `$1,2345`)
  is never truncated to a plausible prefix -- the whole token is kept as an opaque value that
  cannot match a real figure, so it ends up unverified rather than silently matching the wrong
  amount. A parse that overflows `Decimal` is handled the same way: it is still extracted and
  counted, never silently dropped.
- Dates: two-digit years follow the common convention (00-69 -> 20xx, 70-99 -> 19xx). Named
  dates may be month-first ("March 14, 2026") or day-first ("22 October 2026" / "22 Oct 2026").
  Numeric `n/n/n` dates are ambiguous between day-first and month-first; this module resolves
  that the same way as most read-first heuristics: if the first field is > 12 it cannot be a
  month, so it is read as the day (day-first); otherwise it is read month-first (US convention).
  Impossible dates (`2026-02-30`) are not figures and are dropped, not flagged.

Pure like `backend/conflict_check.py`: standard library only, no backend imports, no I/O.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Dict, Iterable, List, Literal, Set, Tuple

FigureKind = Literal["money", "percent", "date"]

NOTICE_MAX_LISTED = 8

# Comma groups must be followed by digits, so a sentence comma ("$1,000, and") is not part of
# the number; a malformed group ("$1,2345") is still taken whole and kept opaque below.
_RAW_NUM = r"\d+(?:,\d+)*(?:\.\d+)?"
_SCALE = r"(?:thousand|million|billion|bn|mm|k|m|b)"
# Abbreviations must be attached and end at a word boundary ("$2k", "$1.2m"), so "$865 more"
# or "$500 by check" never scale; the spelled-out words may follow a space.
_SCALE_SUFFIX = r"(?:(?:bn|mm|k|m|b)\b|\s?(?:thousand|million|billion)\b)"
# A leading "-" is a sign only when it doesn't follow a word, digit or symbol: in "$500-$800"
# it is a range dash.
_SIGN = r"(?:(?<![\w.,$%)-])-)?"
_MONEY = re.compile(
    rf"""
    \(\s*\$\s?-?{_RAW_NUM}{_SCALE_SUFFIX}?\s*\)
    | {_SIGN}\$\s?-?{_RAW_NUM}{_SCALE_SUFFIX}?
    | {_RAW_NUM}{_SCALE_SUFFIX}?\s+dollars\b
    | \bUSD\s?{_RAW_NUM}{_SCALE_SUFFIX}?
    | {_RAW_NUM}{_SCALE_SUFFIX}?\s?USD\b
    """,
    re.VERBOSE | re.IGNORECASE,
)
_SCALE_MULT = {
    "k": 1_000, "m": 1_000_000, "mm": 1_000_000, "b": 1_000_000_000, "bn": 1_000_000_000,
    "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000,
}
_MONEY_TOKEN = re.compile(rf"({_RAW_NUM})\s?({_SCALE})?$", re.IGNORECASE)
_PERCENT = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)\s?(?:%|percent\b)", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\b")
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_MONTH_NAMES = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_NAMED_DATE = re.compile(
    rf"\b({_MONTH_NAMES})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.IGNORECASE,
)
_NAMED_DATE_DAY_FIRST = re.compile(
    rf"\b(\d{{1,2}})\s+({_MONTH_NAMES})\.?,?\s+(\d{{4}})\b", re.IGNORECASE,
)
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class Figure:
    kind: FigureKind
    text: str
    value: str

    @property
    def key(self) -> Tuple[str, str]:
        return self.kind, self.value


@dataclass
class FigureCheck:
    checked: int = 0
    unverified: List[Figure] = field(default_factory=list)
    source_only: List[Figure] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return bool(self.unverified)

    def to_dict(self) -> Dict[str, object]:
        if self.checked == 0:
            action = "unchecked"
        elif self.unverified:
            action = "flagged"
        elif self.source_only:
            action = "source_only"
        else:
            action = "none"
        return {
            "action": action,
            "checked": self.checked,
            "unverified": [{"kind": f.kind, "text": f.text} for f in self.unverified],
            "source_only": [{"kind": f.kind, "text": f.text} for f in self.source_only],
        }


def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


def _valid_grouping(num: str) -> bool:
    integer_part = num.split(".")[0]
    parts = integer_part.split(",")
    if len(parts) == 1:
        return True
    if not (1 <= len(parts[0]) <= 3):
        return False
    return all(len(p) == 3 for p in parts[1:])


def _parse_money(raw: str) -> Decimal | None:
    """Normalize one matched money token, or None if it cannot be parsed cleanly."""
    text = raw.strip()
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    if "-" in text:
        negative = True
        text = text.replace("-", "")
    text = re.sub(r"\bUSD\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bdollars?\b", "", text, flags=re.IGNORECASE)
    text = text.replace("$", "").strip()
    match = _MONEY_TOKEN.match(text)
    if not match:
        return None
    digits, scale = match.group(1), match.group(2)
    if not _valid_grouping(digits):
        return None
    try:
        amount = Decimal(digits.replace(",", ""))
    except InvalidOperation:
        return None
    if scale:
        amount *= _SCALE_MULT[scale.lower()]
    if negative:
        amount = -amount
    try:
        return amount.quantize(_CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def _expand_year(year_str: str) -> int:
    year = int(year_str)
    if len(year_str) == 2:
        year += 2000 if year < 70 else 1900
    return year


def _numeric_date_fields(match: re.Match[str]) -> Tuple[int, int, int]:
    first, second = int(match.group(1)), int(match.group(2))
    year = _expand_year(match.group(3))
    if first > 12:
        return year, second, first
    return year, first, second


def extract_figures(text: str) -> List[Figure]:
    """Money, percent and date figures in `text`, in order of appearance."""
    found: List[Tuple[int, Figure]] = []
    for match in _MONEY.finditer(text):
        amount = _parse_money(match.group(0))
        value = str(amount) if amount is not None else f"malformed:{match.group(0)}"
        found.append((match.start(), Figure("money", match.group(0), value)))
    for match in _PERCENT.finditer(text):
        try:
            value = format(Decimal(match.group(1)).normalize(), "f")
        except InvalidOperation:
            value = f"malformed:{match.group(0)}"
        found.append((match.start(), Figure("percent", match.group(0), value)))
    dates = [(m, int(m.group(1)), int(m.group(2)), int(m.group(3))) for m in _ISO_DATE.finditer(text)]
    dates += [(m, *_numeric_date_fields(m)) for m in _NUMERIC_DATE.finditer(text)]
    dates += [(m, int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
              for m in _NAMED_DATE.finditer(text)]
    dates += [(m, int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
              for m in _NAMED_DATE_DAY_FIRST.finditer(text)]
    for match, year, month, day in dates:
        iso = _iso(year, month, day)
        if iso is not None:  # impossible dates (2026-02-30) are not figures
            found.append((match.start(), Figure("date", match.group(0), iso)))
    found.sort(key=lambda item: item[0])
    return [figure for _, figure in found]


def _known_keys(texts: Iterable[str] | str) -> Set[Tuple[str, str]]:
    texts = [texts] if isinstance(texts, str) else texts
    known: Set[Tuple[str, str]] = set()
    for text in texts:
        known.update(f.key for f in extract_figures(text or ""))
    return known


def check_figures(answer: str, *, trusted: Iterable[str] | str, sources: Iterable[str]) -> FigureCheck:
    """Tier answer figures: verified against `trusted`, source_only against `sources`, else unverified."""
    trusted_known = _known_keys(trusted)
    source_known = _known_keys(sources)
    seen: Set[Tuple[str, str]] = set()
    unverified: List[Figure] = []
    source_only: List[Figure] = []
    for figure in extract_figures(answer):
        if figure.key in seen:
            continue
        seen.add(figure.key)
        if figure.key in trusted_known:
            continue
        if figure.key in source_known:
            source_only.append(figure)
        else:
            unverified.append(figure)
    return FigureCheck(checked=len(seen), unverified=unverified, source_only=source_only)


def _listed(figures: List[Figure]) -> str:
    listed = ", ".join(f"`{f.text}`" for f in figures[:NOTICE_MAX_LISTED])
    extra = len(figures) - NOTICE_MAX_LISTED
    if extra > 0:
        listed += f", and {extra} more"
    return listed


def notice(check: FigureCheck) -> str:
    """Markdown line(s) naming unverified/source-only figures, or "" when nothing was flagged."""
    lines = []
    if check.unverified:
        lines.append(
            f"> **Figure check:** {len(check.unverified)} figure(s) in this answer do not match the "
            f"computed figures or the cited sources: {_listed(check.unverified)}. "
            f"Rely on the computed figures below."
        )
    if check.source_only:
        lines.append(
            f"> **Figure check:** {len(check.source_only)} figure(s) come only from the claim documents, "
            f"not the computed figures or the claim file: {_listed(check.source_only)}. "
            f"Verify them against the source."
        )
    return "\n".join(lines)
