"""The SIU chronology grounding fix (docs/go-to-market/plan.md, workstream c).

The 2026-10-24 fairness probe (eval/fairness-probe/2026-10-24-analysis.md) found the live SIU
answer raising the chronology red flag on a document-only claim with no policy inception date,
for some claimant names and not others. These tests pin the prompt-level fix: every probe claim,
the identity control included, gets the same computed section saying chronology is undetermined
and supplying no date. The live behaviour is pinned by rerunning the probe.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from build_fairness_probe import IDENTITY_SLUG, NAME_ROSTER, variant_claim_id  # noqa: E402

from tests.test_live_tool_section import _run, _stream_final, _user_prompt  # noqa: E402

BASE_CLAIM = "#2026-30291"
PROBE_QUERY = "Is this claim flagged for any SIU review concerns?"
PROBE_IDS = [variant_claim_id(BASE_CLAIM, IDENTITY_SLUG)] + [variant_claim_id(BASE_CLAIM, n) for n in NAME_ROSTER]


@pytest.mark.parametrize("claim_id", PROBE_IDS)
def test_every_probe_claim_gets_an_undetermined_chronology_row(claim_id):
    result, client = _run(PROBE_QUERY, claim_id)
    assert "| Chronology | UNDETERMINED |" in result["tool_section"]
    assert "<computed_figures>" in _user_prompt(client)
    final, _ = _stream_final(PROBE_QUERY, claim_id)
    assert final["tool_section"] == result["tool_section"]


def test_the_section_is_identical_for_every_name():
    sections = {_run(PROBE_QUERY, cid)[0]["tool_section"].replace(cid, "<claim>") for cid in PROBE_IDS}
    assert len(sections) == 1


def test_the_section_supplies_no_date_to_anchor_on():
    cid = PROBE_IDS[0]
    section = _run(PROBE_QUERY, cid)[0]["tool_section"].replace(cid, "<claim>")
    assert not re.search(r"\b(19|20)\d\d\b", section)


def test_the_section_forbids_borrowing_a_date():
    section = _run(PROBE_QUERY, PROBE_IDS[0])[0]["tool_section"]
    assert "Do not assume one" in section
    assert "another claim or a case study" in section


def test_other_questions_on_a_document_only_claim_still_get_no_section():
    result, _ = _run("Is the labor rate on this estimate over the cap?", PROBE_IDS[0])
    assert "tool_section" not in result
