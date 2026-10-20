"""P5 golden rows: every figure in a tool-backed reference must match the computed section.

The P5 rows in eval/golden_queries.py restate claims_tools output in prose. If a tool changes
(as the 2026-10-20 deductible cap did for Webb), the eval would silently score answers against a
stale reference; this pins each reference to what the live path would attach today.
"""
import pytest

from backend.agentic_router import CLAIMS_DATA, AgenticRAGRouter
from backend.figure_check import check_figures
from eval.golden_queries import GOLDEN_QUERIES

TOOL_ROWS = [q for q in GOLDEN_QUERIES if q["claim_id"] and AgenticRAGRouter._detect_live_audit_type(q["query"])
             in {"coverage", "fraud", "subrogation"}]

# The computed section's verdict line for each row, so a flipped verdict fails even with no figures.
VERDICTS = {
    "sterling-coverage-verification": "All coverage checks pass",
    "chen-siu-checklist": "Indicators present: refer to SIU",
    "brooks-siu-checklist": "Indicators present: refer to SIU",
    "rostova-siu-flag-unexplained": "No SOP indicator is present",
    "webb-subrogation-triage": "Refer to the Subrogation Unit",
    "jenkins-subrogation-no-indicator": "No subrogation indicator on file",
}


def computed(row):
    router = AgenticRAGRouter.__new__(AgenticRAGRouter)
    claim = next(c for c in CLAIMS_DATA if c["id"] == row["claim_id"])
    audit_type = router._detect_live_audit_type(row["query"])
    section = "\n".join(router._computed_claim_answer(claim, audit_type, row["query"], []))
    return section, router._get_claim_context_markdown(row["claim_id"])


def test_every_p5_row_is_tool_backed():
    assert {q["id"] for q in TOOL_ROWS} == set(VERDICTS)


@pytest.mark.parametrize("row", TOOL_ROWS, ids=lambda q: q["id"])
def test_reference_figures_match_the_computed_section(row):
    section, dossier = computed(row)
    check = check_figures(row["reference"], trusted=[section, dossier], sources=[])
    assert not check.unverified, [f.text for f in check.unverified]
    assert not check.source_only, [f.text for f in check.source_only]


@pytest.mark.parametrize("row", TOOL_ROWS, ids=lambda q: q["id"])
def test_reference_verdict_matches_the_computed_section(row):
    section, _ = computed(row)
    assert VERDICTS[row["id"]] in section
