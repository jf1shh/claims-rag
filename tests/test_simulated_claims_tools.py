"""The simulated agent's claim answers take their numbers from backend/claims_tools.py."""
import time

import pytest

from backend.agentic_router import AgenticRAGRouter


class _Embedder:
    def embed_query(self, text):
        return [0.0]


class _Store:
    def search_similarity(self, *args, **kwargs):
        return []


def run(query, claim_id):
    logs = []
    result = AgenticRAGRouter()._run_simulated_agent(query, claim_id, _Store(), _Embedder(), None, logs, time.time())
    return result["answer"], logs


def test_labor_query_flags_rates_over_the_sop_caps():
    answer, logs = run("Audit the labor rates on this estimate", "#2026-99382")
    assert "**Result: OVER CAP** ($272.00 billed above the SOP caps)" in answer
    assert "SOP_Auto_Repair_Labor_Rates.pdf, 1. Standard Repair Labor Rates" in answer
    assert "Regional_Labor_Rates_2026.xlsx" not in answer
    assert any("[Tool] labor_rate_check" in line for line in logs)


def test_oem_query_uses_rider_check():
    answer, logs = run("Are OEM parts allowed?", "#2026-55912")
    assert "LKQ recycled or certified aftermarket parts" in answer
    assert "Rider_OEM_Parts_Guarantee.pdf" in answer
    assert any("[Tool] oem_rider_check: lkq" in line for line in logs)


@pytest.mark.parametrize("claim_id, payout", [
    ("#2026-99382", "$1,633.00"),
    ("#2026-10492", "$4,994.00"),
    ("#2026-30291", "$4,730.00"),
    ("#2026-55912", "$0.00"),
])
def test_settlement_letter_payout_is_computed(claim_id, payout):
    answer, logs = run("Draft the settlement letter", claim_id)
    assert f"**Net settlement: {payout}**" in answer
    assert "#### Tool inputs and citations" in answer
    assert any("[Tool] deductible_apply" in line for line in logs)


def test_total_loss_needs_acv_then_decides():
    answer, _ = run("Is this a total loss?", "#2026-55912")
    assert "NEEDS INPUT" in answer and "ACV" in answer
    answer, _ = run("Is this a total loss? ACV $14,000", "#2026-55912")
    assert "**Result: TOTAL LOSS**" in answer and "80% of ACV" in answer
    answer, _ = run("Total loss check, ACV $30,000 and salvage $5,000", "#2026-99382")
    assert "**Result: REPAIRABLE**" in answer


def test_unknown_state_has_no_total_loss_rule():
    answer, _ = run("total loss? ACV $9,000", "#2026-30291")
    assert "NO RULE" in answer


def test_fraud_narrative_unchanged():
    answer, _ = run("Any SIU red flags?", "#2026-30291")
    assert "RED FLAG DETECTED" in answer


def test_global_scope_still_lists_references():
    answer, _ = run("labor rates", None)
    assert answer.startswith("### [Agentic RAG Assistant]")
