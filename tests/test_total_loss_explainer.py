"""P4 total-loss explainer: backend/claims_tools.total_loss_explain and its simulated-agent answer."""
import re
import time
from decimal import Decimal
from pathlib import Path

from backend import claims_tools as ct
from backend.agentic_router import CLAIM_EVIDENCE, CLAIMS_DATA, AgenticRAGRouter

ROOT = Path(__file__).resolve().parents[1]


def claim(claim_id):
    return next(c for c in CLAIMS_DATA if c["id"] == claim_id)


def explain(claim_id, acv=None, salvage=None):
    c = claim(claim_id)
    return ct.total_loss_explain(original=c["estimate"], supplements=c.get("supplements", []), vehicle=c["vehicle"],
                                 loss_year=2026, endorsements=c["endorsements"],
                                 state=ct.facility_state(c["facility"]), acv=acv, salvage=salvage,
                                 valuations=CLAIM_EVIDENCE[claim_id].get("valuations", ()))


def flat(op, total):
    return {"cat": "Body", "op": op, "rate": "Flat", "qty": "1 Unit", "total": total}


def run_tool(*, state="TX", original="$9,000", supplement=None, acv=Decimal("10000"), salvage=None, valuations=()):
    supplements = [] if supplement is None else [
        {"id": "S1", "received": "2026-01-14", "photos": True, "lines": [flat("Hidden damage", supplement)]}]
    return ct.total_loss_explain(original=[flat("Repair", original)], supplements=supplements,
                                 vehicle="2022 Ford Escape", loss_year=2026, endorsements=[], state=state,
                                 acv=acv, salvage=salvage, valuations=valuations)


class TestRulesMatchCorpus:
    def test_valuation_salvage_and_storage_rules_in_generators(self):
        sop = (ROOT / "generate_auto_pdfs.py").read_text()
        assert ct.MIN_VALUATION_SOURCES == 2
        assert "a minimum of two independent valuation sources" in re.sub(r'"\s*\n\s*"', "", sop)
        assert "Kelley Blue Book is secondary" in sop
        assert "the salvage value estimate is deducted from their final settlement payout" in re.sub(r'"\s*\n\s*"', "", sop)
        storage = re.sub(r'"\s*\n\s*"', "", (ROOT / "scripts" / "rebuild_golden_source_docs.py").read_text())
        assert f"up to ${ct.STORAGE_DAILY_CAP} per day" in storage
        assert f"more than {ct.STORAGE_NOTICE_DAYS} calendar days after" in storage
        assert f"within {ct.SALVAGE_TRANSFER_DAYS} business days of the total loss declaration" in storage


class TestTotalLossExplain:
    def test_rostova_florida_threshold_steps_and_overage(self):
        result = explain("#2026-55912", acv=Decimal("14000"), salvage=Decimal("2500"))
        assert result["status"] == "total_loss"
        assert result["repair_cost"] == Decimal("11940") and result["limit"] == Decimal("11200.00")
        assert result["margin"] == Decimal("-740.00")
        assert result["retained_payout"] == Decimal("11500")
        assert any("80% of ACV" in step for step in result["steps"])

    def test_rostova_valuations_flag_kbb_and_too_few_sources(self):
        flags = explain("#2026-55912", acv=Decimal("14000"))["valuation_flags"]
        assert any("Kelley Blue Book" in flag for flag in flags)
        assert any("two independent" in flag for flag in flags)

    def test_sterling_california_formula_includes_supported_supplement(self):
        result = explain("#2026-99382", acv=Decimal("30000"), salvage=Decimal("5000"))
        assert result["status"] == "repairable"
        assert result["original_cost"] == Decimal("2133") and result["supplement_cost"] == Decimal("1006")
        assert result["repair_cost"] == Decimal("3139")
        assert result["break_even_repair"] == Decimal("25000") and result["margin"] == Decimal("21861")

    def test_supplement_flips_the_decision(self):
        result = run_tool(supplement="$1,500")
        assert result["original_status"] == "repairable" and result["status"] == "total_loss"
        assert result["flipped_by_supplements"]

    def test_no_flip_without_supplements(self):
        result = run_tool()
        assert result["status"] == "repairable" and not result["flipped_by_supplements"]
        assert result["margin"] == Decimal("1000.00")

    def test_missing_inputs_are_reported(self):
        assert run_tool(acv=None)["missing"] == ["ACV"]
        assert run_tool(state="CA")["missing"] == ["salvage value"]

    def test_unknown_state_has_no_rule(self):
        assert run_tool(state="NV")["status"] == "no_rule"

    def test_retention_needs_salvage(self):
        assert run_tool()["retained_payout"] is None
        assert run_tool(salvage=Decimal("1200"))["retained_payout"] == Decimal("8800")

    def test_total_loss_lists_next_steps_with_citations(self):
        result = run_tool(supplement="$1,500")
        text = " ".join(result["next_steps"])
        assert "$45" in text and "3 calendar days" in text and "3 business days" in text and "Junk" in text
        sources = {item["source"] for item in result["inputs"]}
        assert {ct.TOTAL_LOSS_SOP, ct.STORAGE_SOP} <= sources

    def test_repairable_has_no_total_loss_steps(self):
        assert run_tool()["next_steps"] == []

    def test_two_acceptable_sources_pass(self):
        valuations = [{"source": "CCC ONE Market Valuation Report", "acv": "$10,100"},
                      {"source": "NADA Guides", "acv": "$9,900"}]
        assert run_tool(valuations=valuations)["valuation_flags"] == []

    def test_source_names_match_whole_words(self):
        valuations = [{"source": "Canada Auto Appraisal Group", "acv": "$10,000"},
                      {"source": "NADA Guides", "acv": "$9,900"}]
        assert any("1 acceptable" in flag for flag in run_tool(valuations=valuations)["valuation_flags"])

    def test_held_supplement_is_pending_not_counted(self):
        held = {"id": "S1", "received": "2026-01-14", "photos": False, "lines": [flat("Hidden damage", "$1,500")]}
        result = ct.total_loss_explain(original=[flat("Repair", "$9,000")], supplements=[held],
                                       vehicle="2022 Ford Escape", loss_year=2026, endorsements=[], state="TX",
                                       acv=Decimal("10000"))
        assert result["supplement_cost"] == ct.ZERO and result["pending_cost"] == Decimal("1500")
        assert result["status"] == "repairable" and result["pending_status"] == "total_loss"
        assert any("held for documentation" in step for step in result["steps"])


def run(query, claim_id):
    logs = []
    result = AgenticRAGRouter()._run_simulated_agent(query, claim_id, _Store(), _Embedder(), None, logs, time.time())
    return result["answer"], logs


class _Embedder:
    def embed_query(self, text):
        return [0.0]


class _Store:
    def search_similarity(self, *args, **kwargs):
        return []


class TestSimulatedTotalLossAnswer:
    def test_answer_walks_the_math_and_cites(self):
        answer, logs = run("Is this a total loss? ACV $14,000, salvage $2,500", "#2026-55912")
        assert "**Result: TOTAL LOSS**" in answer
        assert "$740.00 over the threshold" in answer
        assert "If the owner keeps the vehicle" in answer and "$11,500.00" in answer
        assert "Kelley Blue Book" in answer
        assert "SOP_Total_Loss_Valuation.pdf, 1. Total Loss Threshold Rule" in answer
        assert "not a coverage decision" in answer
        assert any("[Tool] total_loss_explain: total_loss" in line for line in logs)

    def test_supplement_question_mentioning_salvage_stays_a_supplement_review(self):
        answer, _ = run("Review the supplement: does the shop also recover salvage value on the part?", "#2026-99382")
        assert "Supplement Review" in answer

    def test_ui_button_queries_route_to_their_own_mode(self):
        # The UI's total-loss query mentions supplements; it must not route to the supplement review.
        frontend = (ROOT / "frontend" / "app.js").read_text()
        total_loss_query = re.search(r"case 'total_loss':\s*queryText = `([^`]*)`", frontend).group(1)
        supplement_query = re.search(r"case 'supplement':\s*queryText = `([^`]*)`", frontend).group(1)
        answer, _ = run(total_loss_query, "#2026-55912")
        assert "Total Loss Explainer" in answer and "NEEDS INPUT" in answer
        answer, _ = run(supplement_query, "#2026-99382")
        assert "Supplement Review" in answer

    def test_answer_includes_supplement_in_repair_cost(self):
        answer, _ = run("Total loss check, ACV $30,000 and salvage $5,000", "#2026-99382")
        assert "**Result: REPAIRABLE**" in answer
        assert "$1,006.00" in answer and "$21,861.00" in answer
