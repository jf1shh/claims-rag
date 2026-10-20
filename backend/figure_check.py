"""Deterministic figure check: every amount, percentage and date in a live answer must trace back.

On the live path the claims tools compute the labor, parts, total-loss and payout figures
(`backend/claims_tools.py`) and the model only explains them. A model can still restate a
figure wrongly -- a rounded cap, a transposed total, a date it invented. This check pulls
the money, percent and date figures out of the answer and looks for each one, after
normalization, in the computed section (trusted) or in the evidence the model saw (sources
and claim dossier). Anything found in neither is flagged; nothing is rewritten.

The match is exact after normalization, with no tolerance: the computed figures are
cent-exact, so a figure that is close but not equal is exactly the error this exists to catch.

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

_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_MONEY = re.compile(rf"-?\$\s?({_NUMBER})")
_PERCENT = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)\s?(?:%|percent\b)", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_NAMED_DATE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE,
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

    @property
    def flagged(self) -> bool:
        return bool(self.unverified)

    def to_dict(self) -> Dict[str, object]:
        return {"action": "flagged" if self.flagged else "none", "checked": self.checked,
                "unverified": [{"kind": f.kind, "text": f.text} for f in self.unverified]}


def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


def extract_figures(text: str) -> List[Figure]:
    """Money, percent and date figures in `text`, in order of appearance."""
    found: List[Tuple[int, Figure]] = []
    for match in _MONEY.finditer(text):
        try:
            amount = Decimal(match.group(1).replace(",", "")).quantize(_CENT, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            continue
        found.append((match.start(), Figure("money", match.group(0), str(amount))))
    for match in _PERCENT.finditer(text):
        try:
            rate = Decimal(match.group(1)).normalize()
        except InvalidOperation:
            continue
        found.append((match.start(), Figure("percent", match.group(0), format(rate, "f"))))
    dates = [(m, int(m.group(1)), int(m.group(2)), int(m.group(3))) for m in _ISO_DATE.finditer(text)]
    dates += [(m, int(m.group(3)), int(m.group(1)), int(m.group(2))) for m in _US_DATE.finditer(text)]
    dates += [(m, int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
              for m in _NAMED_DATE.finditer(text)]
    for match, year, month, day in dates:
        iso = _iso(year, month, day)
        if iso is not None:  # impossible dates (2026-02-30) are not figures
            found.append((match.start(), Figure("date", match.group(0), iso)))
    found.sort(key=lambda item: item[0])
    return [figure for _, figure in found]


def check_figures(answer: str, *, trusted: str, evidence: Iterable[str]) -> FigureCheck:
    """Flag answer figures that appear neither in the trusted computed section nor in any evidence."""
    known: Set[Tuple[str, str]] = {f.key for f in extract_figures(trusted)}
    for text in evidence:
        known.update(f.key for f in extract_figures(text or ""))
    seen: Set[Tuple[str, str]] = set()
    unverified: List[Figure] = []
    for figure in extract_figures(answer):
        if figure.key in seen:
            continue
        seen.add(figure.key)
        if figure.key not in known:
            unverified.append(figure)
    return FigureCheck(checked=len(seen), unverified=unverified)


def notice(check: FigureCheck) -> str:
    """One Markdown line naming the unverified figures, or "" when nothing was flagged."""
    if not check.flagged:
        return ""
    listed = ", ".join(f"`{f.text}`" for f in check.unverified[:NOTICE_MAX_LISTED])
    extra = len(check.unverified) - NOTICE_MAX_LISTED
    if extra > 0:
        listed += f", and {extra} more"
    return (f"> **Figure check:** {len(check.unverified)} figure(s) in this answer do not match the "
            f"computed figures or the cited sources: {listed}. Rely on the computed figures below.")
