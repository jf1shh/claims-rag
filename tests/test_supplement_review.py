"""P4 supplement review: backend/claims_tools.supplement_review and its simulated-agent answer."""
import re
import time
from decimal import Decimal
from pathlib import Path

import pytest

from backend import claims_tools as ct
from backend.agentic_router import CLAIM_EVIDENCE, CLAIMS_DATA, AgenticRAGRouter

ROOT = Path(__file__).resolve().parents[1]


def claim(claim_id):
    return next(c for c in CLAIMS_DATA if c["id"] == claim_id)


def review(claim_id):
    c = claim(claim_id)
    return ct.supplement_review(original=c["estimate"], supplements=c["supplements"], vehicle=c["vehicle"],
                                loss_year=CLAIM_EVIDENCE[claim_id]["loss_year"], endorsements=c["endorsements"])


def line(op, total, cat="Body", rate="Flat", qty="1 Unit", **extra):
    return {"cat": cat, "op": op, "rate": rate, "qty": qty, "total": total, **extra}


def supplement(*lines, photos=True, before_work=True, received="2026-01-14"):
    return {"id": "S1", "received": received, "photos": photos, "submitted_before_work": before_work,
            "lines": list(lines)}


def run_tool(supplements, *, original=None, vehicle="2025 Ford Escape", endorsements=()):
    original = original or [line("Original repair", "$8,000")]
    return ct.supplement_review(original=original, supplements=supplements, vehicle=vehicle,
                                loss_year=2026, endorsements=endorsements)


class TestRulesMatchCorpus:
    def test_supplement_and_betterment_rules_in_generator(self):
        # Join the generator's implicitly concatenated string literals back into sentences.
        text = re.sub(r'"\s*\n\s*"', "", (ROOT / "scripts" / "rebuild_golden_source_docs.py").read_text())
        assert f"a single supplement exceeds ${ct.REINSPECTION_SINGLE:,}" in text
        assert f"exceed {ct.REINSPECTION_SHARE * 100:.0f}% of the original approved estimate" in text
        assert f"within {ct.SUPPLEMENT_RESPONSE_DAYS} business days" in text
        assert f"from {ct.TIRE_NEW_TREAD}/32 inch new to the {ct.TIRE_MIN_TREAD}/32 inch legal minimum" in text
        assert f"{ct.BATTERY_LIFE_MONTHS}-month service life" in text
        assert "tires, batteries, shock absorbers and exhaust components" in text


class TestSupplementReview:
    def test_sterling_labor_cap_betterment_and_cumulative_reinspection(self):
        result = review("#2026-99382")
        assert result["original_approved"] == Decimal("2133")
        s1 = result["supplements"][0]
        assert s1["billed"] == Decimal("1205") and s1["approved"] == Decimal("1006")
        assert [row["labor_excess"] for row in s1["lines"]] == [Decimal("39"), Decimal("40"), ct.ZERO]
        assert s1["lines"][2]["betterment"] == Decimal("120")
        assert s1["reinspection"] and "25%" in s1["reinspection_reason"]
        assert s1["due"] == "2026-01-16"
        assert result["status"] == "reinspection_required"
        assert result["parts"]["status"] == "oem_rider"
        assert not any("parts" in flag for row in s1["lines"] for flag in row["flags"])

    def test_jenkins_holds_for_photos_and_flags_oem_on_non_safety_part(self):
        result = review("#2026-10492")
        s1 = result["supplements"][0]
        assert s1["billed"] == Decimal("2105") and s1["approved"] == Decimal("1774")
        assert s1["lines"][2]["betterment"] == Decimal("240")
        assert any("LKQ" in flag for flag in s1["lines"][0]["flags"])
        assert s1["reinspection"] and "$1,500" in s1["reinspection_reason"]
        assert s1["due"] is None and any("photos" in flag for flag in s1["flags"])
        assert result["status"] == "hold_documentation"

    def test_small_documented_supplement_can_be_approved_on_photos(self):
        result = run_tool([supplement(line("Replace Hidden Bracket", "$400"))])
        assert result["status"] == "approve_on_photos"
        assert not result["supplements"][0]["reinspection"]

    def test_cumulative_share_counts_every_supplement(self):
        first = supplement(line("A", "$1,200"))
        second = {**supplement(line("B", "$1,000")), "id": "S2"}
        result = run_tool([first, second])
        assert [s["reinspection"] for s in result["supplements"]] == [False, True]
        assert result["cumulative_billed"] == Decimal("2200")

    def test_work_before_submission_is_flagged(self):
        result = run_tool([supplement(line("A", "$300"), before_work=False)])
        assert result["status"] == "hold_documentation"
        assert any("before" in flag for flag in result["supplements"][0]["flags"])

    def test_rider_requires_oem_flags_aftermarket(self):
        result = run_tool([supplement(line("Replace Fender", "$500", part="Aftermarket"))],
                          vehicle="2024 Ford Escape", endorsements=["OEM Parts Guarantee"])
        assert any("rider" in flag for flag in result["supplements"][0]["lines"][0]["flags"])

    def test_wear_item_without_measurement_needs_input(self):
        result = run_tool([supplement(line("Replace 4x Tires", "$800", cat="Tires"))])
        row = result["supplements"][0]["lines"][0]
        assert row["betterment"] == ct.ZERO and any("tread depth" in flag for flag in row["flags"])

    def test_betterment_never_on_body_panels(self):
        result = run_tool([supplement(line("Replace Quarter Panel", "$900", wear={"tread_32nds": 4}))])
        assert result["supplements"][0]["lines"][0]["betterment"] == ct.ZERO

    def test_shocks_have_no_formula_and_are_flagged(self):
        result = run_tool([supplement(line("Replace Rear Shock Absorbers", "$600", cat="Mechanical"))])
        row = result["supplements"][0]["lines"][0]
        assert row["betterment"] == ct.ZERO and any("no formula" in flag for flag in row["flags"])

    @pytest.mark.parametrize("op", ["Replace Entire Wiring Harness", "Exhaustive Underbody Inspection"])
    def test_wear_items_match_whole_words_only(self, op):
        row = run_tool([supplement(line(op, "$500", cat="Electrical"))])["supplements"][0]["lines"][0]
        assert row["betterment"] == ct.ZERO and row["flags"] == []

    def test_betterment_is_on_the_part_price_not_labor(self):
        battery = line("Replace Battery", "$360", cat="Mechanical", rate="$120/hr", qty="2.0 hrs",
                       wear={"battery_age_months": 30})
        row = run_tool([supplement(battery)])["supplements"][0]["lines"][0]
        assert row["labor_excess"] == Decimal("20") and row["betterment"] == Decimal("60")
        assert row["approved"] == Decimal("280")

    def test_aftermarket_on_safety_part_needs_oem(self):
        result = run_tool([supplement(line("Replace Rear Rail Section", "$700", cat="Frame", part="Aftermarket"))],
                          vehicle="2025 Ford Escape")
        assert any("requires OEM" in flag for flag in result["supplements"][0]["lines"][0]["flags"])

    def test_due_date_skips_weekend(self):
        result = run_tool([supplement(line("A", "$100"), received="2026-01-16")])  # a Friday
        assert result["supplements"][0]["due"] == "2026-01-20"

    def test_every_rule_used_is_cited(self):
        sources = {item["source"] for item in review("#2026-10492")["inputs"]}
        assert {ct.SUPPLEMENT_SOP, ct.BETTERMENT_GUIDE, ct.LABOR_SOP} <= sources

    def test_no_supplements(self):
        result = run_tool([])
        assert result["status"] == "no_supplement" and result["supplements"] == []


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


class TestSimulatedSupplementAnswer:
    def test_supplement_query_routes_to_the_tool(self):
        answer, logs = run("Review the supplement against the labor rate caps", "#2026-99382")
        assert "### 🧾 Supplement Review: Matthew Sterling (#2026-99382)" in answer
        assert "REINSPECTION REQUIRED" in answer
        assert "SOP_Supplement_Review.pdf, 2. Reinspection Threshold" in answer
        assert "not a coverage decision" in answer
        assert any("[Tool] supplement_review: reinspection_required" in line for line in logs)

    def test_hold_answer_names_missing_photos(self):
        answer, _ = run("Supplement review please", "#2026-10492")
        assert "HOLD" in answer and "photos" in answer and "LKQ" in answer

    @pytest.mark.parametrize("claim_id", ["#2026-30291", "#2026-55912"])
    def test_claim_without_supplement_says_so(self, claim_id):
        answer, _ = run("Review the supplement", claim_id)
        assert "No supplement is on file" in answer
