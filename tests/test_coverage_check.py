"""Coverage verification: backend/claims_tools.coverage_check and its simulated-agent answer."""
import re
import time
from decimal import Decimal
from pathlib import Path

import pytest

from backend import claims_tools as ct
from backend.agentic_router import CLAIM_EVIDENCE, CLAIMS_DATA, AgenticRAGRouter

ROOT = Path(__file__).resolve().parents[1]


def policy(**over):
    base = {"effective": "2025-01-01", "expiration": "2027-01-01", "cancelled": None, "lapses": [],
            "listed_drivers": ["Jane Doe"], "excluded_drivers": [], "permissive": {}, "source": "claim record"}
    return {**base, **over}


def check(**over):
    kwargs = {"loss_date": "2026-01-15", "reported": "2026-01-16", "peril": "collision",
              "plan": "Plan A (Standard)", "policy": policy(), "driver": "Jane Doe"}
    kwargs.update(over)
    return ct.coverage_check(**kwargs)


def row(result, name="period"):
    return next(c for c in result["checks"] if c["check"] == name)


class TestRulesMatchCorpus:
    def test_coverage_rules_in_generator(self):
        # Join the generator's implicitly concatenated string literals back into sentences.
        text = re.sub(r'"\s*\n\s*"', "", (ROOT / "scripts" / "rebuild_golden_source_docs.py").read_text())
        assert "on or after the policy effective date and before" in text
        assert "the expiration date" in text
        assert "A loss on or after a cancellation effective date is not covered" in text
        assert "provides no coverage for losses during the lapse" in text
        assert "Excluded Driver Endorsement" in text
        assert "covered as a permissive user" in text
        assert "impact with another vehicle or object" in text and "rollover" in text
        assert "theft, vandalism, hail, flood, fire, falling objects, animal strikes and glass breakage" in text
        assert "Plan C policies carry collision coverage only" in text
        assert f"report a loss within {ct.NOTICE_DAYS} days" in text
        assert "referred to a claims supervisor" in text


class TestPeriodCheck:
    def test_in_force_passes(self):
        assert row(check())["result"] == "pass"

    def test_before_effective_is_an_issue(self):
        result = check(loss_date="2024-12-31", policy=policy())
        assert row(result)["result"] == "issue" and "before the policy effective date" in row(result)["detail"]

    def test_on_expiration_date_is_not_covered(self):
        result = check(loss_date="2027-01-01", policy=policy())
        assert row(result)["result"] == "issue" and "expiration date" in row(result)["detail"]

    def test_after_cancellation_is_an_issue(self):
        result = check(loss_date="2026-06-01", policy=policy(cancelled="2026-05-01"))
        assert row(result)["result"] == "issue" and "cancellation" in row(result)["detail"]

    def test_inside_a_lapse_is_an_issue(self):
        result = check(loss_date="2026-03-15",
                       policy=policy(lapses=[{"start": "2026-03-01", "end": "2026-03-31"}]))
        assert row(result)["result"] == "issue" and "lapse" in row(result)["detail"]


class TestDriverCheck:
    def test_listed_driver_passes(self):
        result = check(driver="Jane Doe", policy=policy(listed_drivers=["Jane Doe"]))
        assert row(result, "driver")["result"] == "pass"

    def test_excluded_driver_is_an_issue(self):
        result = check(driver="Jane Doe", policy=policy(excluded_drivers=["Jane Doe"]))
        assert row(result, "driver")["result"] == "issue"
        assert "Excluded Driver Endorsement" in row(result, "driver")["detail"]

    def test_excluded_driver_wording_is_advisory_not_a_decision(self):
        result = check(driver="Jane Doe", policy=policy(excluded_drivers=["Jane Doe"]))
        detail = row(result, "driver")["detail"]
        assert "coverage question for the adjuster" in detail
        assert "is not covered operating" not in detail

    def test_permissive_documented_passes(self):
        result = check(driver="Alex Roe",
                       policy=policy(listed_drivers=["Jane Doe"], permissive={"Alex Roe": "texted by the insured"}))
        assert row(result, "driver")["result"] == "pass"
        assert "texted by the insured" in row(result, "driver")["detail"]

    def test_permissive_undocumented_is_an_issue(self):
        result = check(driver="Alex Roe",
                       policy=policy(listed_drivers=["Jane Doe"], permissive={"Alex Roe": ""}))
        assert row(result, "driver")["result"] == "issue"

    def test_unknown_driver_is_missing(self):
        result = check(driver="Stranger", policy=policy(listed_drivers=["Jane Doe"]))
        assert row(result, "driver")["result"] == "missing"

    def test_no_driver_on_file_is_missing(self):
        result = check(driver=None)
        assert row(result, "driver")["result"] == "missing"


class TestPerilCheck:
    def test_collision_perils_map_to_collision(self):
        for peril in ct.COLLISION_PERILS:
            result = check(peril=peril)
            assert result["coverage_part"] == "collision"

    def test_comprehensive_perils_map_to_comprehensive(self):
        for peril in ct.COMPREHENSIVE_PERILS:
            result = check(peril=peril, plan="Plan B (Premium)")
            assert result["coverage_part"] == "comprehensive"

    def test_plan_c_comprehensive_loss_is_an_issue(self):
        result = check(peril="theft", plan="Plan C (Basic)")
        assert result["coverage_part"] == "comprehensive"
        assert row(result, "peril")["result"] == "issue"

    def test_plan_c_collision_loss_passes(self):
        result = check(peril="collision", plan="Plan C (Basic)")
        assert row(result, "peril")["result"] == "pass"

    def test_unknown_peril_raises(self):
        with pytest.raises(ValueError):
            check(peril="asteroid_strike")


class TestNoticeCheck:
    def test_thirty_days_passes(self):
        result = check(loss_date="2026-01-01", reported="2026-01-31")
        assert row(result, "notice")["result"] == "pass"

    def test_thirty_one_days_is_an_issue(self):
        result = check(loss_date="2026-01-01", reported="2026-02-01")
        assert row(result, "notice")["result"] == "issue"
        assert "supervisor" in row(result, "notice")["detail"]

    def test_missing_reported_date_is_missing(self):
        result = check(reported=None)
        assert row(result, "notice")["result"] == "missing"


class TestStatusAggregation:
    def test_any_issue_is_coverage_question(self):
        result = check(loss_date="2024-12-31")
        assert result["status"] == "coverage_question"

    def test_any_missing_without_issue_is_incomplete(self):
        result = check(reported=None)
        assert result["status"] == "incomplete"

    def test_all_pass_is_verified(self):
        assert check()["status"] == "verified"


def claim(claim_id):
    return next(c for c in CLAIMS_DATA if c["id"] == claim_id)


def coverage_for(claim_id):
    c = claim(claim_id)
    evidence = CLAIM_EVIDENCE[claim_id]
    return ct.coverage_check(loss_date=evidence["loss_date"], reported=evidence["reported"],
                             peril=evidence["peril"], plan=c["plan"], policy=evidence["policy"],
                             driver=evidence["driver"])


class TestAllDemoClaimsVerified:
    @pytest.mark.parametrize("claim_id", ["#2026-99382", "#2026-10492", "#2026-30291", "#2026-55912",
                                          "#2026-71845", "#2026-84410"])
    def test_verified(self, claim_id):
        assert coverage_for(claim_id)["status"] == "verified"


class TestWebbSubrogation:
    def test_webb_referral_and_recovery(self):
        c = claim("#2026-71845")
        evidence = CLAIM_EVIDENCE["#2026-71845"]
        supp = ct.supplement_review(original=c["estimate"], supplements=c.get("supplements", []),
                                    vehicle=c["vehicle"], loss_year=evidence["loss_year"],
                                    endorsements=c["endorsements"])
        damages = supp["original_approved"]
        assert damages == Decimal("865.00") or damages == Decimal("865")
        result = ct.subrogation_triage(state=ct.facility_state(c["facility"]), loss_date=evidence["loss_date"],
                                       damages=damages, deductible=AgenticRAGRouter._policy_deductible(c),
                                       liability=evidence["subrogation"])
        assert result["status"] == "refer"
        assert result["recoverable_share"] == Decimal("0.70")
        assert result["deductible_reimbursed"] == Decimal("605.50")  # 70% of the $865 paid, not of $1,000
        assert result["referral_due"] == "2026-03-12"


class TestBrooksFactsPresent:
    def test_brooks_evidence_on_file(self):
        evidence = CLAIM_EVIDENCE["#2026-84410"]
        assert evidence["loss_date"] == "2026-02-25"
        assert evidence["reported"] == "2026-02-26"
        assert evidence["peril"] == "single_vehicle"
        assert evidence["driver"] == "Tyler Brooks"
        assert evidence["policy"]["effective"] == "2026-02-20"
        assert evidence["subrogation"]["third_party"] is None


class TestChenExclusionCitesInspectionPhotos:
    def test_chen_rust_exclusion_source(self):
        reason = CLAIM_EVIDENCE["#2026-30291"]["excluded"]["Repair Passenger Side Key Scratches"]
        assert "inspection_photos_Chen.pdf" in reason
        assert ct.LABOR_SOP not in reason  # SOP source stays the fraud SOP, not the labor SOP


class TestRouterRoutesCoverage:
    @pytest.mark.parametrize("query, expected", [
        ("Is the driver covered under the policy?", "coverage"),
        ("Verify coverage for this claim", "coverage"),
        ("Was the loss in force on the loss date?", "coverage"),
        ("Is this an excluded driver?", "coverage"),
        ("Was this a permissive use situation?", "coverage"),
        ("Audit the labor rates", "labor"),
        ("Is this a total loss?", "total_loss"),
    ])
    def test_whole_word_keywords(self, query, expected):
        assert AgenticRAGRouter._detect_audit_type(query) == expected


def _ui_button_queries():
    src = (Path(__file__).resolve().parent.parent / "frontend" / "app.js").read_text(encoding="utf-8")
    found = re.findall(r"case '(\w+)':\s*\n\s*queryText = `([^`]*)`", src)
    return [(audit, re.sub(r"\$\{[^}]*\}", "X", text)) for audit, text in found]


class TestUiButtonQueriesKeepTheirRoute:
    def test_every_button_is_found(self):
        assert {a for a, _ in _ui_button_queries()} >= {"coverage", "labor", "oem", "fraud", "supplement",
                                                          "total_loss", "subrogation", "letter"}

    @pytest.mark.parametrize("audit, query", _ui_button_queries())
    def test_button_query_routes_to_its_own_audit(self, audit, query):
        # The OEM and letter queries mention "coverage" in passing; they must not route to coverage.
        assert AgenticRAGRouter._detect_audit_type(query) == audit


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


class TestSimulatedCoverageAnswer:
    def test_coverage_query_routes_to_the_tool(self):
        answer, logs = run("Verify coverage for this claim", "#2026-99382")
        assert "### 🛡️ Coverage Verification: Matthew Sterling (#2026-99382)" in answer
        assert "All coverage checks pass (advisory)" in answer
        assert "SOP_Coverage_Verification.pdf" in answer
        assert any("[Tool] coverage_check: verified" in line for line in logs)

    def test_webb_coverage_answer(self):
        answer, _ = run("Verify coverage for this claim", "#2026-71845")
        assert "Coverage Verification: Marcus Webb" in answer


class TestSeedDocsContainCitedFacts:
    def test_webb_docs_contain_the_cited_facts(self):
        text = (ROOT / "ingest_all.py").read_text()
        assert "police_report_Webb.pdf" in text and "30%" in text and "70%" in text
        assert "witness_statement_Webb.pdf" in text and "following too closely" in text
        assert "dashcam_summary_Webb.pdf" in text and "1.2 seconds" in text and "41 mph" in text

    def test_brooks_docs_contain_the_cited_facts(self):
        text = (ROOT / "ingest_all.py").read_text()
        assert "loss_statement_Brooks.pdf" in text and "deer" in text
        assert "inspection_photos_Brooks.pdf" in text and "rust-through" in text

    def test_chen_inspection_photos_doc_contains_the_cited_facts(self):
        text = (ROOT / "ingest_all.py").read_text()
        assert "inspection_photos_Chen.pdf" in text and "rust oxidation" in text
