"""Unit tests for backend/claims_tools.siu_checklist and its use in the simulated agent.

The SIU indicator checklist replaces the old canned fraud narratives: every row is computed
and cited, and the checklist itself never adjudicates -- a referral stays an adjuster decision.
"""
import time
from pathlib import Path

import pytest

from backend import claims_tools as ct
from backend.agentic_router import CLAIM_EVIDENCE, CLAIMS_DATA, AgenticRAGRouter

ROOT = Path(__file__).resolve().parents[1]

BASE_POLICY = {"effective": "2026-01-01"}
BASE_SCENE = {"single_vehicle": True, "remote": True, "police_report": None, "witnesses": []}


def _result(**overrides):
    kwargs = dict(claim_id="#TEST-1", reported="2026-01-05", loss_date="2026-01-01", loss_time=None,
                  policy=BASE_POLICY, scene=BASE_SCENE, estimate=[])
    kwargs.update(overrides)
    return ct.siu_checklist(**kwargs)


def _indicator(result, name):
    return next(row for row in result["indicators"] if row["indicator"] == name)


class TestRuleTablesMatchCorpus:
    """The rule constants must still be what the SOP generator says."""

    def test_siu_rule_values_in_generator(self):
        text = (ROOT / "generate_auto_pdfs.py").read_text()
        assert f"within {ct.SIU_REFERRAL_BUSINESS_DAYS} business days of detection" in text
        assert f"within {ct.SIU_CHRONOLOGY_DAYS} days of policy inception or policy coverage upgrade" in text
        assert "rust, corrosion, or old dents" in text
        assert "identical photo angles or" in text


class TestChronology:
    def test_present_at_exactly_the_threshold_from_inception(self):
        result = _result(reported="2026-01-11", policy={"effective": "2026-01-01"})
        assert _indicator(result, "chronology")["result"] == "present"

    def test_absent_one_day_past_the_threshold_from_inception(self):
        result = _result(reported="2026-01-12", policy={"effective": "2026-01-01"})
        assert _indicator(result, "chronology")["result"] == "absent"

    def test_present_at_the_threshold_from_a_coverage_upgrade(self):
        policy = {"effective": "2024-01-01",
                  "coverage_changes": [{"date": "2026-02-01", "change": "liability limit increase"}]}
        result = _result(reported="2026-02-11", policy=policy)
        assert _indicator(result, "chronology")["result"] == "present"

    def test_absent_one_day_past_a_coverage_upgrade(self):
        policy = {"effective": "2024-01-01",
                  "coverage_changes": [{"date": "2026-02-01", "change": "liability limit increase"}]}
        result = _result(reported="2026-02-12", policy=policy)
        assert _indicator(result, "chronology")["result"] == "absent"

    def test_missing_when_reported_date_is_not_on_file(self):
        result = _result(reported=None)
        assert _indicator(result, "chronology")["result"] == "missing"


class TestPaperAccident:
    FULL_SCENE = {"single_vehicle": True, "remote": True, "police_report": None, "witnesses": []}

    @pytest.mark.parametrize("override, expected", [
        ({}, "present"),
        ({"single_vehicle": False}, "absent"),
        ({"remote": False}, "absent"),
        ({"police_report": "police_report_X.pdf"}, "absent"),
        ({"witnesses": ["Jane Doe"]}, "absent"),
    ])
    def test_each_condition_is_required(self, override, expected):
        scene = {**self.FULL_SCENE, **override}
        result = _result(scene=scene, loss_time="02:00")
        assert _indicator(result, "paper_accident")["result"] == expected

    def test_late_night_bound_2200_is_present(self):
        result = _result(scene=self.FULL_SCENE, loss_time="22:00")
        assert _indicator(result, "paper_accident")["result"] == "present"

    def test_late_night_bound_0500_is_absent(self):
        result = _result(scene=self.FULL_SCENE, loss_time="05:00")
        assert _indicator(result, "paper_accident")["result"] == "absent"

    def test_daytime_is_absent(self):
        result = _result(scene=self.FULL_SCENE, loss_time="14:00")
        assert _indicator(result, "paper_accident")["result"] == "absent"

    def test_missing_when_scene_matches_but_time_is_unknown(self):
        result = _result(scene=self.FULL_SCENE, loss_time=None)
        assert _indicator(result, "paper_accident")["result"] == "missing"

    def test_definition_disclaims_the_sop_does_not_set_hours(self):
        result = _result(scene=self.FULL_SCENE, loss_time="02:00")
        assert "SOP itself does not define the hours" in _indicator(result, "paper_accident")["detail"]


class TestPreExistingDamage:
    def test_rust_in_an_estimate_line_alone_is_review_not_present(self):
        result = _result(estimate=[{"op": "Repair Rust-Through on Rear Quarter Panel", "cat": "Body",
                                    "rate": "$60/hr", "qty": "5.0 hrs", "total": "$300"}])
        assert _indicator(result, "pre_existing_damage")["result"] == "review"

    def test_whole_word_match_trust_is_not_rust(self):
        result = _result(estimate=[{"op": "Replace Trust Sensor Module", "cat": "Electrical",
                                    "rate": "$110/hr", "qty": "1.0 hrs", "total": "$110"}])
        assert _indicator(result, "pre_existing_damage")["result"] == "absent"

    def test_dossier_finding_is_present(self):
        result = _result(findings=[{"indicator": "pre_existing_damage", "detail": "rust in scratch channels",
                                    "source": "inspection_photos_X.pdf"}])
        assert _indicator(result, "pre_existing_damage")["result"] == "present"

    def test_no_match_is_absent(self):
        result = _result(estimate=[{"op": "Replace Rear Bumper Cover", "cat": "Body", "rate": "$60/hr",
                                    "qty": "5.0 hrs", "total": "$300"}])
        assert _indicator(result, "pre_existing_damage")["result"] == "absent"


class TestDuplicateInvoices:
    def test_missing_with_no_photo_metadata(self):
        result = _result()
        assert _indicator(result, "duplicate_invoices")["result"] == "missing"

    def test_absent_when_no_other_claim_matches(self):
        scene = {**BASE_SCENE, "photos": [{"angle": "front bumper", "date": "2026-01-01"}]}
        result = _result(scene=scene, other_claims=[{"claim_id": "#OTHER",
                                                      "photos": [{"angle": "rear bumper", "date": "2026-05-01"}]}])
        assert _indicator(result, "duplicate_invoices")["result"] == "absent"

    def test_review_when_only_the_photo_angle_matches(self):
        scene = {**BASE_SCENE, "photos": [{"angle": "front bumper", "date": "2026-01-01"}]}
        result = _result(scene=scene, other_claims=[{"claim_id": "#OTHER",
                                                      "photos": [{"angle": "front bumper", "date": "2026-05-01"}]}])
        indicator = _indicator(result, "duplicate_invoices")
        assert indicator["result"] == "review"
        assert "#OTHER" not in indicator["detail"]

    def test_review_when_only_the_date_matches(self):
        scene = {**BASE_SCENE, "photos": [{"angle": "front bumper", "date": "2026-01-01"}]}
        result = _result(scene=scene, other_claims=[{"claim_id": "#OTHER",
                                                      "photos": [{"angle": "rear bumper", "date": "2026-01-01"}]}])
        assert _indicator(result, "duplicate_invoices")["result"] == "review"

    def test_present_when_another_claim_has_the_same_photo_angle_and_date(self):
        scene = {**BASE_SCENE, "photos": [{"angle": "front bumper", "date": "2026-01-01"}]}
        result = _result(scene=scene, other_claims=[{"claim_id": "#OTHER",
                                                      "photos": [{"angle": "front bumper", "date": "2026-01-01"}]}])
        indicator = _indicator(result, "duplicate_invoices")
        assert indicator["result"] == "present"
        assert "#OTHER" not in indicator["detail"]
        assert indicator["source"] == ct.DOSSIER
        assert indicator["_internal"]["matched_claim_id"] == "#OTHER"


class TestStatus:
    def test_refer_to_siu_when_any_indicator_is_present(self):
        result = _result(estimate=[{"op": "Repair Rust Damage", "cat": "Body", "rate": "$60/hr",
                                    "qty": "1.0 hrs", "total": "$60"}])
        assert result["status"] == "refer_to_siu"
        assert "refer to SIU" in result["message"]
        assert result["procedure"]

    def test_no_indicator_when_all_are_absent(self):
        scene = {**BASE_SCENE, "single_vehicle": False,
                 "photos": [{"angle": "x", "date": "2026-01-01"}]}
        result = _result(scene=scene, reported="2026-06-01", policy={"effective": "2026-01-01"},
                         other_claims=[{"claim_id": "#OTHER", "photos": [{"angle": "y", "date": "2099-01-01"}]}])
        assert result["status"] == "no_indicator"

    def test_incomplete_when_none_present_but_some_missing(self):
        result = _result(reported="2026-06-01", policy={"effective": "2026-01-01"},
                         scene={**BASE_SCENE, "single_vehicle": False})
        assert result["status"] == "incomplete"

    def test_review_status_when_an_indicator_needs_review_and_none_is_present(self):
        result = _result(reported="2026-06-01", policy={"effective": "2026-01-01"},
                         scene={**BASE_SCENE, "single_vehicle": False},
                         estimate=[{"op": "Repair Rust Damage", "cat": "Body", "rate": "$60/hr",
                                    "qty": "1.0 hrs", "total": "$60"}])
        assert result["status"] == "review"
        assert "review" in result["message"].lower()
        assert not result["procedure"]

    def test_referral_due_only_computed_when_detected_given(self):
        result = _result(estimate=[{"op": "Repair Rust Damage", "cat": "Body", "rate": "$60/hr",
                                    "qty": "1.0 hrs", "total": "$60"}])
        assert result["referral_due"] is None
        result = _result(estimate=[{"op": "Repair Rust Damage", "cat": "Body", "rate": "$60/hr",
                                    "qty": "1.0 hrs", "total": "$60"}], detected="2026-01-16")
        assert result["referral_due"] == ct.add_business_days("2026-01-16", ct.SIU_REFERRAL_BUSINESS_DAYS)


FORBIDDEN_PHRASES = ["Denied", "Deny", "Approve claim", "CLAIM DENIED"]


def _known_filenames():
    """Every filename literal in ingest_all.py's seeds or the document generators."""
    import re
    pattern = re.compile(r'"([\w\-]+\.(?:pdf|docx|xlsx|png|txt))"')
    names = set(pattern.findall((ROOT / "ingest_all.py").read_text()))
    names |= set(pattern.findall((ROOT / "generate_auto_pdfs.py").read_text()))
    names |= set(pattern.findall((ROOT / "create_sample_files.py").read_text()))
    names |= set(pattern.findall((ROOT / "scripts" / "rebuild_golden_source_docs.py").read_text()))
    return names


class _Embedder:
    def embed_query(self, text):
        return [0.0]


class _Store:
    def search_similarity(self, *args, **kwargs):
        return []


def _run_fraud_query(claim_id):
    logs = []
    result = AgenticRAGRouter()._run_simulated_agent(
        "Any SIU red flags or fraud indicators for this claim?", claim_id, _Store(), _Embedder(), None, logs,
        time.time())
    return result["answer"]


@pytest.mark.parametrize("claim_id", [c["id"] for c in CLAIMS_DATA])
def test_no_claim_answer_adjudicates_or_cites_an_unknown_file(claim_id):
    answer = _run_fraud_query(claim_id)
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in answer
    known = _known_filenames()
    import re
    cited = set(re.findall(r"\b[\w\-]+\.(?:pdf|docx|xlsx|png|txt)\b", answer))
    unknown = {name for name in cited if name not in known}
    assert not unknown, f"{claim_id} cites unknown file(s): {unknown}"


class TestDemoClaimStatuses:
    @pytest.mark.parametrize("claim_id", ["#2026-99382", "#2026-10492", "#2026-71845"])
    def test_no_fraud_indicators(self, claim_id):
        answer = _run_fraud_query(claim_id)
        assert "No SOP indicator is present" in answer

    def test_chen_pre_existing_damage_present(self):
        answer = _run_fraud_query("#2026-30291")
        assert "Pre Existing Damage | PRESENT" in answer
        assert "inspection_photos_Chen.pdf" in answer

    def test_brooks_chronology_and_pre_existing_damage_present(self):
        answer = _run_fraud_query("#2026-84410")
        assert "Chronology | PRESENT" in answer
        assert "Pre Existing Damage | PRESENT" in answer

    def test_rostova_names_the_consequential_damage_note_not_fraud(self):
        answer = _run_fraud_query("#2026-55912")
        assert "consequential-damage coverage exclusion question" in answer
        assert "not a fraud indicator" in answer
        assert "SIU Flagged" in answer


class TestCrossClaimLeak:
    """The duplicate-invoices row must never name another claim's ID or source filename to the user."""

    CLAIM = {"id": "#TEST-A", "insured": "Test Insured", "estimate": [], "endorsements": [],
              "plan": "Plan A (Standard)", "deductible": "$500", "vehicle": "2025 Test Car",
              "facility": "Test Shop", "totalEst": "$0"}

    def test_matched_claim_id_and_source_do_not_reach_the_rendered_section(self, monkeypatch):
        other_claim = {"id": "#TEST-B"}
        evidence = {
            "reported": "2026-01-05", "loss_date": "2026-01-01", "policy": BASE_POLICY,
            "fraud": {"scene": {**BASE_SCENE, "photos": [{"angle": "front bumper", "date": "2026-01-01"}]}},
        }
        other_evidence = {"fraud": {"scene": {"photos": [
            {"angle": "front bumper", "date": "2026-01-01", "source": "secret_other_claim_photo.pdf"}]}}}
        monkeypatch.setattr("backend.agentic_router.CLAIMS_DATA", [self.CLAIM, other_claim])
        monkeypatch.setattr("backend.agentic_router.CLAIM_EVIDENCE",
                             {self.CLAIM["id"]: evidence, other_claim["id"]: other_evidence})
        out = AgenticRAGRouter()._siu_answer(self.CLAIM, evidence, [])
        rendered = "\n".join(out)
        assert "#TEST-B" not in rendered
        assert "secret_other_claim_photo.pdf" not in rendered
        assert "SIU can see which" in rendered


def test_brooks_chronology_matches_claim_evidence():
    evidence = CLAIM_EVIDENCE["#2026-84410"]
    assert evidence["reported"] == "2026-02-26"
    assert evidence["policy"]["effective"] == "2026-02-20"
    days = 6
    from datetime import date
    assert (date.fromisoformat(evidence["reported"]) - date.fromisoformat(evidence["policy"]["effective"])).days == days
