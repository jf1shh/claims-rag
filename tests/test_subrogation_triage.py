"""P4 subrogation triage: backend/claims_tools.subrogation_triage and its simulated-agent answer."""
import re
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from backend import claims_tools as ct
from backend.agentic_router import CLAIM_EVIDENCE, CLAIMS_DATA, AgenticRAGRouter

ROOT = Path(__file__).resolve().parents[1]
TWO_TYPES = [{"type": "police_report", "source": "police.pdf", "detail": "report"},
             {"type": "photos", "source": "photo.png", "detail": "damage"}]


def triage(*, state="TX", loss_date="2026-01-12", damages="10000", deductible="500", **liability):
    liability = {"third_party": "Other driver", "evidence": TWO_TYPES, "first_payment": None, **liability}
    return ct.subrogation_triage(state=state, loss_date=loss_date, damages=Decimal(damages),
                                 deductible=Decimal(deductible) if deductible is not None else None,
                                 liability=liability)


def claim_triage(claim_id):
    return run("Triage this claim for subrogation", claim_id)


class TestRulesMatchCorpus:
    def test_subrogation_and_liability_rules_in_generator(self):
        # Join the generator's implicitly concatenated string literals back into sentences.
        text = re.sub(r'"\s*\n\s*"', "", (ROOT / "scripts" / "rebuild_golden_source_docs.py").read_text())
        assert f"assessed at {ct.SUBRO_FAULT_THRESHOLD * 100:.0f}% or more fault" in text
        assert ", ".join(ct.SUBRO_CAUSES.values()).replace(", a commercial", ", or a commercial") in text
        assert f"within {ct.SUBRO_REFERRAL_DAYS} days of the first physical damage payment" in text
        assert f"within {ct.SUBRO_NOTICE_BUSINESS_DAYS} business days of any recovery" in text
        assert f"${ct.ARBITRATION_LIMIT:,} or less" in text and f"above ${ct.ARBITRATION_LIMIT:,}" in text
        assert "same percentage of the deductible as the percentage of total damages" in text
        assert f"at least {['zero', 'one', 'two'][ct.MIN_LIABILITY_EVIDENCE]} of: the police report" in text
        assert "dash-camera data" in text and "presumption that the following driver is at fault" in text
        assert "California applies pure comparative fault" in text and "even at 99%" in text
        bar = ct.COMPARATIVE_FAULT["TX"]["bar"]
        assert f"more than {bar * 100:.0f}% at fault recovers nothing" in text
        since = date.fromisoformat(ct.COMPARATIVE_FAULT["FL"]["since"])
        assert f"51% bar for accidents on or after {since:%B} {since.day}, {since.year}" in text


class TestSubrogationTriage:
    def test_sterling_rear_end_presumption_refers(self):
        result = claim_triage("#2026-99382")[2]
        assert result["status"] == "refer" and result["fault_basis"] == "presumption"
        assert result["evidence_types"] == ["photos", "police_report", "telematics"]
        assert result["recoverable_share"] == Decimal("1")
        assert result["expected_recovery"] == Decimal("3139.00")  # 2,133 original + 1,006 supported supplement
        assert result["deductible_reimbursed"] == Decimal("500.00")
        assert result["forum"] == "inter-company arbitration"
        assert result["referral_due"] is None
        assert any("30-day referral clock starts at the first physical damage payment" in f for f in result["flags"])

    def test_tx_insured_30_percent(self):
        result = triage(third_party_fault=Decimal("0.70"))
        assert result["recoverable_share"] == Decimal("0.70")
        assert result["expected_recovery"] == Decimal("7000.00")
        assert result["deductible_reimbursed"] == Decimal("350.00")
        assert result["fault_basis"] == "assessed" and result["status"] == "refer"

    def test_tx_insured_exactly_50_percent_still_recovers(self):
        assert triage(third_party_fault=Decimal("0.50"))["recoverable_share"] == Decimal("0.50")

    def test_tx_insured_51_percent_is_barred_but_still_an_indicator(self):
        result = triage(third_party_fault=Decimal("0.49"))
        assert result["indicators"] and result["status"] == "refer"
        assert result["recoverable_share"] == ct.ZERO and result["expected_recovery"] == Decimal("0.00")
        assert any("51% bar blocks" in flag for flag in result["flags"])

    def test_ca_pure_comparative_at_99_percent(self):
        result = triage(state="CA", third_party_fault=Decimal("0.01"))
        assert result["recoverable_share"] == Decimal("0.01") and result["indicators"] == []
        assert result["status"] == "no_indicator"

    def test_exactly_25_percent_is_an_indicator(self):
        assert triage(third_party_fault=Decimal("0.25"))["indicators"]

    def test_commercial_vehicle_cause_with_low_fault(self):
        result = triage(third_party_fault=Decimal("0.10"),
                        causes=[{"cause": "commercial_vehicle", "source": "police.pdf"}])
        assert result["indicators"] == ["loss caused by a commercial vehicle"]
        assert result["status"] == "refer"

    def test_florida_before_the_bar_date_has_no_rule(self):
        result = triage(state="FL", loss_date="2023-03-23", third_party_fault=Decimal("0.80"))
        assert result["recoverable_share"] is None and result["rule"] is None
        assert any("before 2023-03-24" in flag for flag in result["flags"])

    def test_florida_on_the_bar_date_uses_modified_rule(self):
        result = triage(state="FL", loss_date="2023-03-24", third_party_fault=Decimal("0.80"))
        assert result["recoverable_share"] == Decimal("0.80") and "51% bar" in result["rule"]

    def test_unknown_state_has_no_rule(self):
        result = triage(state="NV", third_party_fault=Decimal("0.80"))
        assert result["recoverable_share"] is None and any("NV" in flag for flag in result["flags"])

    def test_one_evidence_type_refers_after_evidence(self):
        result = triage(third_party_fault=Decimal("0.80"), evidence=TWO_TYPES[:1])
        assert result["status"] == "refer_after_evidence"
        assert any("driver statements" in flag for flag in result["flags"])

    def test_rebuttal_defeats_the_presumption(self):
        result = triage(collision="rear_end", insured_position="lead", rebuttal=["sudden unsafe lane change"])
        assert result["fault_basis"] is None and result["third_party_fault"] is None
        assert result["status"] == "refer_after_evidence"
        assert any("presumption rebutted by sudden unsafe lane change" in flag for flag in result["flags"])

    def test_cause_without_identified_party(self):
        result = triage(third_party=None, causes=[{"cause": "defective_part", "source": "shop.pdf"}])
        assert result["status"] == "refer_after_evidence"
        assert any("responsible party not identified" in flag for flag in result["flags"])

    @pytest.mark.parametrize("claim_id", ["#2026-30291", "#2026-10492", "#2026-55912"])
    def test_claims_without_a_third_party_have_no_indicator(self, claim_id):
        result = claim_triage(claim_id)[2]
        assert result["status"] == "no_indicator" and result["recoverable_share"] is None
        assert result["forum"] is None

    def test_referral_due_is_30_days_after_first_payment(self):
        result = triage(third_party_fault=Decimal("1"), first_payment="2026-01-20")
        assert result["referral_due"] == "2026-02-19"

    def test_large_recovery_goes_to_counsel(self):
        assert triage(third_party_fault=Decimal("1"), damages="100000.01")["forum"] == "Subrogation Counsel review"
        assert triage(third_party_fault=Decimal("1"), damages="100000")["forum"] == "inter-company arbitration"

    def test_missing_deductible_is_reported(self):
        result = triage(third_party_fault=Decimal("1"), deductible=None)
        assert result["deductible_reimbursed"] is None
        assert any("deductible not on file" in flag for flag in result["flags"])

    def test_every_rule_used_is_cited(self):
        result = triage(third_party_fault=Decimal("0.70"), first_payment="2026-01-20")
        cited = {(item["source"], item["section"]) for item in result["inputs"]}
        assert {(ct.SUBRO_SOP, "1. Referral Criteria"), (ct.SUBRO_SOP, "2. Deductible Reimbursement"),
                (ct.SUBRO_SOP, "3. Inter-Company Arbitration"), (ct.LIABILITY_SOP, "1. Evidence Required"),
                (ct.LIABILITY_SOP, "2. Comparative Fault by State (demo table)")} <= cited
        assert {"police.pdf", "photo.png"} <= {item["source"] for item in result["inputs"]}

    def test_presumption_cites_the_liability_sop(self):
        inputs = claim_triage("#2026-99382")[2]["inputs"]
        assert any(i["name"] == "rear-end presumption" and i["source"] == ct.LIABILITY_SOP for i in inputs)


def run(query, claim_id):
    logs = []
    result = AgenticRAGRouter()._run_simulated_agent(query, claim_id, _Store(), _Embedder(), None, logs, time.time())
    claim = next(c for c in CLAIMS_DATA if c["id"] == claim_id)
    evidence = CLAIM_EVIDENCE[claim_id]
    supp = ct.supplement_review(original=claim["estimate"], supplements=claim.get("supplements", []),
                                vehicle=claim["vehicle"], loss_year=evidence["loss_year"],
                                endorsements=claim["endorsements"])
    damages = supp["original_approved"] + sum((s["approved"] for s in supp["supplements"] if not s["flags"]), ct.ZERO)
    tool = ct.subrogation_triage(state=ct.facility_state(claim["facility"]), loss_date=evidence["loss_date"],
                                 damages=damages, deductible=AgenticRAGRouter._policy_deductible(claim),
                                 liability=evidence["subrogation"])
    return result["answer"], logs, tool


class _Embedder:
    def embed_query(self, text):
        return [0.0]


class _Store:
    def search_similarity(self, *args, **kwargs):
        return []


class TestSimulatedSubrogationAnswer:
    def test_subrogation_query_routes_to_the_tool(self):
        answer, logs, _ = run("Should we pursue subrogation on this claim?", "#2026-99382")
        assert "### ⚖️ Subrogation Triage: Matthew Sterling (#2026-99382)" in answer
        assert "Refer to the Subrogation Unit (adjuster decision)" in answer
        assert "presumption" in answer and "$3,139.00" in answer and "$500.00" in answer
        assert "SOP_Subrogation_Recovery.pdf, 1. Referral Criteria" in answer
        assert any("[Tool] subrogation_triage: refer" in line for line in logs)

    def test_recovery_from_phrase_routes_to_subrogation(self):
        answer, _, _ = run("Can we get a recovery from the other driver?", "#2026-99382")
        assert "Subrogation Triage" in answer

    def test_no_indicator_answer(self):
        answer, _, _ = run("subrogation triage", "#2026-30291")
        assert "No subrogation indicator" in answer and "suspect" in answer

    def test_settlement_letter_mentioning_subrogation_stays_a_letter(self):
        answer, _, _ = run("Draft the settlement letter and note if subrogation is being pursued", "#2026-99382")
        assert "Settlement Calculation" in answer


class TestKeywordRouting:
    @pytest.mark.parametrize("query, expected", [
        ("Is a separate rental capacity available?", "general"),
        ("Was the estimate accurate? Any escape clause?", "general"),
        ("Audit the labor rates", "labor"),
        ("Are the shop's rates over the caps?", "labor"),
        ("Is this a total loss?", "total_loss"),
        ("Review the supplement: does the shop also recover salvage value?", "supplement"),
        ("subrogation please", "subrogation"),
        ("Any fraudulent red flags?", "fraud"),
        ("Draft the decision letter", "letter"),
    ])
    def test_whole_word_keywords(self, query, expected):
        assert AgenticRAGRouter._detect_audit_type(query) == expected
