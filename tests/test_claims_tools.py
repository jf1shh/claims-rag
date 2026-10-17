"""Unit tests for backend/claims_tools.py against the synthetic claim records and rule docs."""
from decimal import Decimal
from pathlib import Path

import pytest

from backend import claims_tools as ct
from backend.agentic_router import CLAIMS_DATA

ROOT = Path(__file__).resolve().parents[1]


def claim(claim_id):
    return next(c for c in CLAIMS_DATA if c["id"] == claim_id)


class TestRuleTablesMatchCorpus:
    """The rule constants must still be what the synthetic documents say."""

    def test_labor_caps_in_sop_generator(self):
        text = (ROOT / "generate_auto_pdfs.py").read_text()
        for key, cap in ct.LABOR_CAPS.items():
            assert f"{ct.LABOR_CAP_LABELS[key]}: ${cap} per hour" in text

    def test_oem_and_total_loss_rules_in_generator(self):
        text = (ROOT / "generate_auto_pdfs.py").read_text()
        assert f"only available for vehicles under {ct.OEM_RIDER_MAX_AGE} years old" in text
        assert f"under {ct.SOP_OEM_MAX_AGE} years old or with less than 36,000 miles" in text
        assert "In Texas, a flat threshold of 100% of ACV" in text
        assert "In Florida, the threshold is 80% of ACV" in text
        assert "Cost of Repair + Salvage Value >= ACV" in text
        assert "capped at $5,000" in text

    def test_deductible_table_in_xlsx_generator(self):
        text = (ROOT / "create_sample_files.py").read_text().replace(" ", "")
        assert "1000,500,500,250,2000" in text.replace("\n", "")
        assert set(ct.DEDUCTIBLES.values()) == {Decimal(v) for v in ("1000", "500", "250", "2000")}

    def test_glass_waiver_in_rebuild_generator(self):
        text = (ROOT / "scripts" / "rebuild_golden_source_docs.py").read_text()
        assert "Comprehensive deductible is waived for all windshield" in text


class TestParsing:
    def test_money_and_helpers(self):
        assert ct.money("$1,200") == Decimal("1200")
        assert ct.money("$75/hr") == Decimal("75")
        assert ct.fmt(Decimal("-500")) == "-$500.00"
        assert ct.facility_state("Elite Fleet Repair (Las Vegas, NV)") == "NV"
        assert ct.model_year("2023 Tesla Model Y") == 2023
        assert ct.plan_letter("Plan B (Premium)") == "B"
        assert ct.coverage_of("$250 Comprehensive Deductible") == "comprehensive"
        assert ct.endorsement_limit("Custom Equipment ($3.5k limit)") == Decimal("3500")

    def test_money_rejects_text_without_amount(self):
        with pytest.raises(ValueError):
            ct.money("Flat")


class TestLaborRateCheck:
    def test_sterling_every_hourly_line_over_cap(self):
        result = ct.labor_rate_check(claim("#2026-99382")["estimate"])
        assert result["status"] == "over_cap"
        excess = {line["op"]: line["excess"] for line in result["lines"] if line["status"] != "not_checked"}
        # Body $75 vs $62 x 6, paint $75 vs $62 x 8, frame $85 vs $75 x 5, mechanical $120 vs $110 x 4.
        assert list(excess.values()) == [Decimal("78"), Decimal("104"), Decimal("50"), Decimal("40")]
        assert result["over_cap_total"] == Decimal("272")

    def test_chen_parts_split_and_electrical_mapping(self):
        result = ct.labor_rate_check(claim("#2026-30291")["estimate"])
        converter = result["lines"][0]
        assert converter["labor"] == Decimal("240") and converter["parts"] == Decimal("1260")
        console = result["lines"][2]
        assert console["mapped_from_electrical"] and console["cap"] == Decimal("110")
        # $10 x 2 + $10 x 6 mechanical, $13 x 8 body.
        assert result["over_cap_total"] == Decimal("184")
        assert result["lines"][1]["status"] == "not_checked"

    def test_rostova_within_cap(self):
        result = ct.labor_rate_check(claim("#2026-55912")["estimate"])
        assert result["status"] == "within_cap"
        assert result["over_cap_total"] == 0

    def test_every_cap_used_is_cited(self):
        result = ct.labor_rate_check(claim("#2026-99382")["estimate"])
        sop = [i for i in result["inputs"] if i["source"] == ct.LABOR_SOP]
        assert {i["name"] for i in sop} == {"Sheet Metal Repair cap", "Refinishing / Painting cap",
                                             "Frame Alignment cap", "Mechanical Work cap"}

    def test_flat_only_estimate_is_not_checked(self):
        result = ct.labor_rate_check([{"cat": "Glass", "op": "x", "rate": "Flat", "qty": "1 Unit", "total": "$100"}])
        assert result["status"] == "not_checked"


class TestOemRiderCheck:
    def test_rider_under_five_years(self):
        c = claim("#2026-99382")
        result = ct.oem_rider_check(vehicle=c["vehicle"], loss_year=2026, endorsements=c["endorsements"])
        assert result["status"] == "oem_rider" and result["age"] == 3

    def test_no_rider_young_vehicle_gets_sop_safety_rule(self):
        c = claim("#2026-10492")
        result = ct.oem_rider_check(vehicle=c["vehicle"], loss_year=2026, endorsements=c["endorsements"])
        assert result["status"] == "sop_oem_safety"
        assert any("OEM specification" in n for n in result["notes"])

    def test_no_rider_old_vehicle_is_lkq(self):
        c = claim("#2026-55912")
        result = ct.oem_rider_check(vehicle=c["vehicle"], loss_year=2026, endorsements=c["endorsements"])
        assert result["status"] == "lkq" and result["age"] == 6
        assert any("Mileage is not on file" in n for n in result["notes"])

    def test_rider_on_old_vehicle_is_ineligible(self):
        result = ct.oem_rider_check(vehicle="2019 Audi A4", loss_year=2026, endorsements=["OEM Parts Guarantee"])
        assert result["status"] == "rider_ineligible"

    def test_exactly_three_years_is_an_sop_gap(self):
        assert ct.oem_rider_check(vehicle="2023 Kia EV6", loss_year=2026, endorsements=[])["status"] == "sop_gap"

    def test_low_mileage_overrides_age(self):
        result = ct.oem_rider_check(vehicle="2020 Kia EV6", loss_year=2026, endorsements=[], mileage=20000)
        assert result["status"] == "sop_oem_safety"


class TestTotalLossCheck:
    def test_florida_threshold(self):
        result = ct.total_loss_check(state="FL", repair_cost=Decimal("11940"), acv=Decimal("14000"))
        assert result["limit"] == Decimal("11200.00") and result["status"] == "total_loss"

    def test_texas_threshold_repairable(self):
        result = ct.total_loss_check(state="TX", repair_cost=Decimal("9000"), acv=Decimal("10000"))
        assert result["status"] == "repairable"

    def test_california_formula_needs_salvage(self):
        result = ct.total_loss_check(state="CA", repair_cost=Decimal("2405"), acv=Decimal("30000"))
        assert result["status"] == "needs_input" and result["missing"] == ["salvage value"]
        result = ct.total_loss_check(state="CA", repair_cost=Decimal("8000"), acv=Decimal("10000"),
                                     salvage=Decimal("2000"))
        assert result["status"] == "total_loss" and result["compared"] == Decimal("10000")

    def test_missing_acv_and_unknown_state(self):
        assert ct.total_loss_check(state="FL", repair_cost=Decimal("1"), acv=None)["missing"] == ["ACV"]
        assert ct.total_loss_check(state="NV", repair_cost=Decimal("1"), acv=Decimal("2"))["status"] == "no_rule"


def approved_lines(claim_id):
    return ct.labor_rate_check(claim(claim_id)["estimate"])["lines"]


def approved(lines):
    return [{**line, "approved": line.get("approved", line["total"])} for line in lines]


class TestDeductibleApply:
    def test_sterling_collision_deductible(self):
        c = claim("#2026-99382")
        result = ct.deductible_apply(lines=approved(approved_lines(c["id"])), plan=c["plan"],
                                     deductible=c["deductible"], endorsements=c["endorsements"])
        # $2,405 itemized - $272 over cap = $2,133; minus $500 collision.
        assert result["gross"] == Decimal("2133") and result["payout"] == Decimal("1633")
        assert not result["notes"]

    def test_jenkins_glass_waiver_limits_deductible_to_non_glass(self):
        c = claim("#2026-10492")
        result = ct.deductible_apply(lines=approved(approved_lines(c["id"])), plan=c["plan"],
                                     deductible=c["deductible"], endorsements=c["endorsements"])
        assert result["glass"] == Decimal("1550") and result["glass_waiver"]
        # $3,200 + $744 (paint at cap) + $1,200 + $350 = $5,494; $500 off the non-glass part.
        assert result["gross"] == Decimal("5494") and result["payout"] == Decimal("4994")

    def test_chen_custom_limit_and_exclusion(self):
        c = claim("#2026-30291")
        result = ct.deductible_apply(
            lines=approved(approved_lines(c["id"])), plan=c["plan"], deductible=c["deductible"],
            endorsements=c["endorsements"],
            custom_ops={"Replace 4x Sport Wheels & Tires", "Replace Stolen Infotainment Console"},
            excluded={"Repair Passenger Side Key Scratches": "pre-existing"})
        assert result["custom"] == Decimal("5840") and result["custom_paid"] == Decimal("3500")
        # Converter $1,480 + custom $3,500 - $250.
        assert result["payout"] == Decimal("4730")
        assert any("Form 402" in n for n in result["notes"])

    def test_full_exclusion_pays_nothing(self):
        c = claim("#2026-55912")
        lines = approved(approved_lines(c["id"]))
        result = ct.deductible_apply(lines=lines, plan=c["plan"], deductible=c["deductible"],
                                     endorsements=c["endorsements"],
                                     excluded={line["op"]: "consequential damage" for line in lines})
        assert result["payout"] == 0 and result["deductible_applied"] == 0

    def test_deductible_mismatch_is_noted(self):
        line = {"op": "x", "cat": "Body", "approved": Decimal("3000")}
        result = ct.deductible_apply(lines=[line], plan="Plan A (Standard)", deductible="$500 Collision Deductible",
                                     endorsements=[])
        assert result["payout"] == Decimal("2500")
        assert "differs from the Plan A table value $1,000.00" in result["notes"][0]

    def test_custom_items_without_endorsement_unpaid(self):
        line = {"op": "Wheels", "cat": "Body", "approved": Decimal("2000")}
        result = ct.deductible_apply(lines=[line], plan="Plan B", deductible="$250 Comprehensive",
                                     endorsements=[], custom_ops={"Wheels"})
        assert result["custom_paid"] == 0 and result["payout"] == 0


def test_render_inputs_lists_each_citation():
    result = ct.labor_rate_check(claim("#2026-55912")["estimate"])
    table = "\n".join(ct.render_inputs([result]))
    assert "SOP_Auto_Repair_Labor_Rates.pdf, 1. Standard Repair Labor Rates" in table
    assert table.count("| `labor_rate_check` |") == len(result["inputs"])
