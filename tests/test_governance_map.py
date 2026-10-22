"""P6: the governance evidence map must not drift from the code it cites.

`docs/governance/*.md` pins each control as a link whose text is `` `path` `text` ``.
Every relative link must resolve, and every pinned text (a symbol, a quoted default,
an error message) must still appear in the linked file. Renaming a control or changing
a default it quotes fails this test until the map is updated in the same change.
"""
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
GOVERNANCE = ROOT / "docs" / "governance"

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_PINNED = re.compile(r"^`([^`]+)` `(.+)`$")


def _links():
    for doc in sorted(GOVERNANCE.glob("*.md")):
        for text, target in _LINK.findall(doc.read_text(encoding="utf-8")):
            yield doc, text, target


def test_given_governance_docs_when_listed_then_the_naic_map_exists():
    assert (GOVERNANCE / "naic-ai-bulletin-map.md").is_file()


def test_given_governance_links_when_resolved_then_every_relative_target_exists():
    missing = []
    for doc, _, target in _links():
        if target.startswith(("http://", "https://", "#")):
            continue
        path = (doc.parent / target.split("#", 1)[0]).resolve()
        if not path.exists():
            missing.append(f"{doc.name}: {target}")
    assert not missing, missing


def test_given_pinned_evidence_when_checked_then_each_text_is_still_in_its_file():
    stale = []
    pinned = 0
    for doc, text, target in _links():
        match = _PINNED.match(text)
        if not match:
            continue
        pinned += 1
        rel_path, needle = match.groups()
        path = (doc.parent / target).resolve()
        if path != (ROOT / rel_path).resolve():
            stale.append(f"{doc.name}: link text {rel_path} points at {target}")
        elif needle not in path.read_text(encoding="utf-8"):
            stale.append(f"{doc.name}: {needle!r} not found in {rel_path}")
    assert pinned >= 30, pinned
    assert not stale, stale
