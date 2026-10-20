"""Deterministic claims tools for the simulated agent.

The LLM (or the simulation's prose) explains; this module computes. Every rule value below
mirrors a sentence in a synthetic source document, and every result carries the inputs it
used with a citation, so the chat can show where each number came from.

The rule tables are copied from the generators (`generate_auto_pdfs.py`,
`create_sample_files.py`, `scripts/rebuild_golden_source_docs.py`); `tests/test_claims_tools.py`
fails if a value here stops appearing in its generator's text.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

LABOR_SOP = "SOP_Auto_Repair_Labor_Rates.pdf"
OEM_RIDER = "Rider_OEM_Parts_Guarantee.pdf"
TOTAL_LOSS_SOP = "SOP_Total_Loss_Valuation.pdf"
DEDUCTIBLE_TABLE = "Auto_Deductibles_And_Limits.xlsx"
GLASS_RIDER = "Endorsement_Windshield_Zero_Deductible.docx"
CPE_FORM = "Endorsement_Custom_Equipment_Form402.pdf"
DOSSIER = "claim dossier"

# SOP_Auto_Repair_Labor_Rates.pdf, section 1: capped hourly rates for 2026.
LABOR_CAPS: Dict[str, Decimal] = {
    "sheet_metal": Decimal("62"),
    "frame": Decimal("75"),
    "refinish": Decimal("62"),
    "mechanical": Decimal("110"),
    "aluminum": Decimal("120"),
}
LABOR_CAP_LABELS = {
    "sheet_metal": "Sheet Metal Repair",
    "frame": "Frame Alignment",
    "refinish": "Refinishing / Painting",
    "mechanical": "Mechanical Work",
    "aluminum": "Aluminum Structural Repair",
}
# Estimate category -> SOP labor class. Electrical work has no class of its own in the SOP,
# so it is checked against the mechanical cap and the output says so.
CATEGORY_TO_LABOR = {
    "Body": "sheet_metal",
    "Paint": "refinish",
    "Frame": "frame",
    "Mechanical": "mechanical",
    "Electrical": "mechanical",
}

# Rider_OEM_Parts_Guarantee.pdf, section 2: only for vehicles under 5 years old.
OEM_RIDER_MAX_AGE = 5
# SOP_Auto_Repair_Labor_Rates.pdf, section 3: OEM for safety parts under 3 years or
# 36,000 miles; LKQ/aftermarket for vehicles older than 3 years.
SOP_OEM_MAX_AGE = 3
SOP_OEM_MAX_MILES = 36000

# SOP_Total_Loss_Valuation.pdf, section 1.
TOTAL_LOSS_RULES: Dict[str, Dict[str, Any]] = {
    "CA": {"kind": "formula", "text": "Total Loss Formula: Cost of Repair + Salvage Value >= ACV"},
    "TX": {"kind": "threshold", "ratio": Decimal("1.00"), "text": "flat threshold of 100% of ACV"},
    "FL": {"kind": "threshold", "ratio": Decimal("0.80"), "text": "threshold is 80% of ACV"},
}

# Auto_Deductibles_And_Limits.xlsx: standard deductible by plan and coverage.
DEDUCTIBLES: Dict[tuple, Decimal] = {
    ("A", "collision"): Decimal("1000"),
    ("A", "comprehensive"): Decimal("500"),
    ("B", "collision"): Decimal("500"),
    ("B", "comprehensive"): Decimal("250"),
    ("C", "collision"): Decimal("2000"),
}

# Endorsement_Custom_Equipment_Form402.pdf, section 2: standard CPE limit.
CPE_STANDARD_LIMIT = Decimal("5000")

SUPPLEMENT_SOP = "SOP_Supplement_Review.pdf"
BETTERMENT_GUIDE = "Guideline_Betterment_Adjustments.docx"
# SOP_Supplement_Review.pdf, section 2: reinspection when one supplement exceeds $1,500 or all
# supplements together exceed 25% of the original approved estimate. Section 3: respond within
# 2 business days of complete documentation.
REINSPECTION_SINGLE = Decimal("1500")
REINSPECTION_SHARE = Decimal("0.25")
SUPPLEMENT_RESPONSE_DAYS = 2
# Guideline_Betterment_Adjustments.docx: wear items only; tire tread 10/32 new to 2/32 minimum;
# batteries prorated over 60 months. Shocks and exhaust have no formula in the guideline.
TIRE_NEW_TREAD = 10
TIRE_MIN_TREAD = 2
BATTERY_LIFE_MONTHS = 60
# Whole words only: "entire" is not a tire and "exhaustive" is not an exhaust.
WEAR_ITEMS = {
    "tire": re.compile(r"\btires?\b"),
    "battery": re.compile(r"\bbatter(?:y|ies)\b"),
    "shock": re.compile(r"\bshocks?\b"),
    "exhaust": re.compile(r"\bexhaust\b"),
}
STORAGE_SOP = "SOP_Vehicle_Storage_Fees.txt"
# SOP_Total_Loss_Valuation.pdf, section 2: ACV from at least two independent sources; Kelley Blue
# Book is secondary (historical reference only), so it does not count toward the two.
MIN_VALUATION_SOURCES = 2
ACCEPTED_VALUATION_SOURCES = ("ccc one", "nada", "dealer")
# Whole words only: "Canada Auto Appraisal" is not NADA.
ACCEPTED_VALUATION_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(name) for name in ACCEPTED_VALUATION_SOURCES) + r")\b", re.IGNORECASE)
# SOP_Vehicle_Storage_Fees.txt: storage covered up to $45/day; the policyholder owns storage more
# than 3 calendar days after the ready-to-move notice; transfer to salvage within 3 business days.
STORAGE_DAILY_CAP = 45
STORAGE_NOTICE_DAYS = 3
SALVAGE_TRANSFER_DAYS = 3
# Lines the SOP treats as safety parts for the "OEM for safety components" rule.
SAFETY_CATEGORIES = {"Safety", "Frame"}

SUBRO_SOP = "SOP_Subrogation_Recovery.pdf"
LIABILITY_SOP = "SOP_Liability_Determination.pdf"
# SOP_Subrogation_Recovery.pdf, section 1: refer when a third party is 25% or more at fault, or the
# loss was caused by one of these; refer within 30 days of the first physical damage payment.
SUBRO_FAULT_THRESHOLD = Decimal("0.25")
SUBRO_CAUSES = {
    "defective_part": "a defective part",
    "public_road_hazard": "a road hazard maintained by a public entity",
    "commercial_vehicle": "a commercial vehicle",
}
SUBRO_REFERRAL_DAYS = 30
# Section 2: written notice to the insured within 10 business days of any recovery.
SUBRO_NOTICE_BUSINESS_DAYS = 10
# Section 3: $100,000 or less goes to inter-company arbitration; above it, Subrogation Counsel.
ARBITRATION_LIMIT = Decimal("100000")
# SOP_Liability_Determination.pdf, section 1: a liability decision cites at least two of these.
# "telematics" covers telematics or dash-camera data.
LIABILITY_EVIDENCE_TYPES = ("police_report", "driver_statements", "witness_statement", "photos", "telematics")
MIN_LIABILITY_EVIDENCE = 2
# Section 2: comparative fault by state. Florida's bar applies only from `since`; the SOP gives no
# rule for earlier Florida losses.
COMPARATIVE_FAULT: Dict[str, Dict[str, Any]] = {
    "CA": {"kind": "pure", "text": "pure comparative fault: recovery is reduced by the claimant's own "
                                   "percentage of fault, even at 99%"},
    "TX": {"kind": "modified", "bar": Decimal("0.50"),
           "text": "modified comparative fault with a 51% bar: a claimant more than 50% at fault recovers nothing"},
    "FL": {"kind": "modified", "bar": Decimal("0.50"), "since": "2023-03-24",
           "text": "modified comparative fault with a 51% bar for accidents on or after March 24, 2023"},
}

FRAUD_SOP = "SOP_Claims_Fraud_Red_Flags.pdf"
# SOP_Claims_Fraud_Red_Flags.pdf, section 1: refer to SIU within 3 business days of detection.
SIU_REFERRAL_BUSINESS_DAYS = 3
# Section 2, chronology: filed within 10 days of policy inception or a coverage upgrade.
SIU_CHRONOLOGY_DAYS = 10
# Section 2 gives "late at night" for paper accidents but no hours; this window is this tool's
# own definition, flagged as such wherever it's used.
SIU_LATE_NIGHT_START = 22
SIU_LATE_NIGHT_END = 5
# Whole words only: "trust" is not rust, "entrust" is not rust or an old dent.
PRE_EXISTING_DAMAGE_PATTERN = re.compile(r"\brust(?:ed|y)?\b|\bcorrosion\b|\bcorroded\b|\bold dents?\b",
                                         re.IGNORECASE)

COVERAGE_SOP = "SOP_Coverage_Verification.pdf"
# SOP_Coverage_Verification.pdf, section 4: 30 days to report a loss.
NOTICE_DAYS = 30
# SOP_Coverage_Verification.pdf, section 3: collision is impact with a vehicle or object, or rollover;
# comprehensive is theft, vandalism, hail, flood, fire, falling objects, animal strikes or glass.
COLLISION_PERILS = {"collision", "rollover", "single_vehicle"}
COMPREHENSIVE_PERILS = {"theft", "vandalism", "hail", "flood", "fire", "falling_object", "animal", "glass"}

ZERO = Decimal("0")


def cite(name: str, value: Any, source: str, section: Optional[str] = None) -> Dict[str, Any]:
    """One tool input: what it is, its value, and the document it came from."""
    return {"name": name, "value": value, "source": source, "section": section}


def money(value: Any) -> Decimal:
    """'$1,200' / '$75/hr' / 1200 -> Decimal('1200')."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    match = re.search(r"-?[\d,]+(?:\.\d+)?", str(value))
    if not match:
        raise ValueError(f"no amount in {value!r}")
    return Decimal(match.group(0).replace(",", ""))


def fmt(amount: Decimal) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def _hourly_rate(rate: str) -> Optional[Decimal]:
    return money(rate) if "/hr" in str(rate) else None


def _hours(qty: str) -> Optional[Decimal]:
    return money(qty) if "hr" in str(qty) else None


def facility_state(facility: str) -> Optional[str]:
    """'Apex Auto Body (San Francisco, CA)' -> 'CA'."""
    match = re.search(r",\s*([A-Z]{2})\)\s*$", facility or "")
    return match.group(1) if match else None


def model_year(vehicle: str) -> Optional[int]:
    match = re.match(r"\s*((?:19|20)\d{2})\b", vehicle or "")
    return int(match.group(1)) if match else None


def plan_letter(plan: str) -> Optional[str]:
    match = re.search(r"Plan\s+([A-C])\b", plan or "")
    return match.group(1) if match else None


def coverage_of(deductible: str) -> Optional[str]:
    text = (deductible or "").lower()
    if "comprehensive" in text:
        return "comprehensive"
    if "collision" in text:
        return "collision"
    return None


def endorsement_limit(text: str) -> Decimal:
    """'Custom Equipment ($3.5k limit)' -> Decimal('3500')."""
    match = re.search(r"\$([\d,]+(?:\.\d+)?)\s*(k)?", text, re.IGNORECASE)
    if not match:
        raise ValueError(f"no limit in {text!r}")
    amount = Decimal(match.group(1).replace(",", ""))
    return amount * 1000 if match.group(2) else amount


def is_glass_line(line: Dict[str, Any]) -> bool:
    """Glass replacement, plus the ADAS recalibration that the glass rider covers with it."""
    op = line.get("op", "").lower()
    return line.get("cat") == "Glass" or (line.get("cat") == "Safety" and "windshield" in op)


def labor_rate_check(estimate: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Check each hourly estimate line against the SOP labor cap for its class.

    Returns per-line results and the total billed over the caps (rate excess x hours).
    Flat-fee lines are listed as not checked. A line whose total exceeds rate x hours
    carries parts; only the labor portion is compared.
    """
    lines: List[Dict[str, Any]] = []
    inputs: List[Dict[str, Any]] = []
    used_caps = set()
    over_total = ZERO
    for index, row in enumerate(estimate, start=1):
        rate = _hourly_rate(row.get("rate", ""))
        hours = _hours(row.get("qty", ""))
        total = money(row["total"])
        base = {"line": index, "op": row["op"], "cat": row["cat"], "total": total}
        labor_class = CATEGORY_TO_LABOR.get(row["cat"])
        if rate is None or hours is None or labor_class is None:
            lines.append({**base, "status": "not_checked", "reason": "flat fee or no SOP labor class"})
            continue
        cap = LABOR_CAPS[labor_class]
        used_caps.add(labor_class)
        labor = rate * hours
        excess = max(ZERO, rate - cap) * hours
        over_total += excess
        inputs.append(cite(f"line {index} rate", f"{fmt(rate)}/hr x {hours} hrs", DOSSIER, "estimate"))
        lines.append({
            **base,
            "status": "over_cap" if rate > cap else "within_cap",
            "rate": rate,
            "hours": hours,
            "labor": labor,
            "parts": max(ZERO, total - labor),
            "labor_class": labor_class,
            "cap": cap,
            "excess": excess,
            "approved": total - excess,
            "mapped_from_electrical": row["cat"] == "Electrical",
        })
    for labor_class in sorted(used_caps):
        inputs.append(cite(f"{LABOR_CAP_LABELS[labor_class]} cap", f"{fmt(LABOR_CAPS[labor_class])}/hr",
                           LABOR_SOP, "1. Standard Repair Labor Rates"))
    checked = [line for line in lines if line["status"] != "not_checked"]
    status = "over_cap" if any(line["status"] == "over_cap" for line in checked) else (
        "within_cap" if checked else "not_checked")
    return {"tool": "labor_rate_check", "status": status, "lines": lines,
            "over_cap_total": over_total, "inputs": inputs}


def oem_rider_check(*, vehicle: str, loss_year: int, endorsements: Iterable[str],
                    mileage: Optional[int] = None) -> Dict[str, Any]:
    """Decide the parts standard: OEM rider, the SOP's safety-parts rule, or LKQ/aftermarket."""
    year = model_year(vehicle)
    if year is None:
        raise ValueError(f"no model year in {vehicle!r}")
    age = loss_year - year
    endorsements = list(endorsements)
    has_rider = any("oem parts" in e.lower() for e in endorsements)
    has_glass_rider = any("glass" in e.lower() for e in endorsements)
    inputs = [
        cite("vehicle", vehicle, DOSSIER),
        cite("loss year", loss_year, DOSSIER),
        cite("vehicle age", f"{loss_year} - {year} = {age} years", DOSSIER),
        cite("OEM rider on policy", "yes" if has_rider else "no", DOSSIER, "endorsements"),
        cite("rider age limit", f"under {OEM_RIDER_MAX_AGE} years", OEM_RIDER, "2. Eligibility & Term Limits"),
        cite("SOP OEM safety-parts rule", f"under {SOP_OEM_MAX_AGE} years or {SOP_OEM_MAX_MILES:,} miles",
             LABOR_SOP, "3. Aftermarket vs. OEM Parts Policy"),
    ]
    notes: List[str] = []
    if has_rider and age < OEM_RIDER_MAX_AGE:
        status, standard = "oem_rider", "OEM for all body, structural, steering, suspension and safety-electronics parts"
    elif has_rider:
        status, standard = "rider_ineligible", "rider on file but vehicle is not under 5 years; SOP rules apply"
        notes.append("Verify the rider was sold at inception for an eligible vehicle.")
    elif age < SOP_OEM_MAX_AGE or (mileage is not None and mileage < SOP_OEM_MAX_MILES):
        status, standard = "sop_oem_safety", "OEM for safety-related components only; LKQ/aftermarket elsewhere"
    elif age > SOP_OEM_MAX_AGE:
        status, standard = "lkq", "LKQ recycled or certified aftermarket parts"
    else:
        status, standard = "sop_gap", "SOP is silent at exactly 3 years: adjuster decision"
        notes.append("The SOP covers 'under 3 years' and 'older than 3 years', not exactly 3.")
    if mileage is None and not has_rider and age >= SOP_OEM_MAX_AGE:
        notes.append(f"Mileage is not on file; under {SOP_OEM_MAX_MILES:,} miles would make safety parts OEM.")
    if has_glass_rider:
        inputs.append(cite("glass rider", "replacement glass must meet OEM specifications", GLASS_RIDER,
                           "2. Recalibration coverage"))
        notes.append("Zero-Deductible Glass rider: replacement glass is written to OEM specification.")
    return {"tool": "oem_rider_check", "status": status, "standard": standard, "age": age,
            "has_rider": has_rider, "notes": notes, "inputs": inputs}


def total_loss_check(*, state: Optional[str], repair_cost: Decimal, acv: Optional[Decimal],
                     salvage: Optional[Decimal] = None) -> Dict[str, Any]:
    """Apply the state's total-loss rule from the SOP. Missing inputs are reported, not guessed."""
    inputs = [cite("loss state", state or "unknown", DOSSIER, "repair facility"),
              cite("repair cost", fmt(repair_cost), DOSSIER, "estimate line items")]
    rule = TOTAL_LOSS_RULES.get(state or "")
    if rule is None:
        return {"tool": "total_loss_check", "status": "no_rule", "inputs": inputs,
                "missing": [], "reason": f"{TOTAL_LOSS_SOP} has no total-loss rule for {state or 'this state'}"}
    inputs.append(cite(f"{state} rule", rule["text"], TOTAL_LOSS_SOP, "1. Total Loss Threshold Rule"))
    missing = []
    if acv is None:
        missing.append("ACV")
    if rule["kind"] == "formula" and salvage is None:
        missing.append("salvage value")
    if missing:
        return {"tool": "total_loss_check", "status": "needs_input", "inputs": inputs, "missing": missing}
    inputs.append(cite("ACV", fmt(acv), "query"))
    if rule["kind"] == "formula":
        inputs.append(cite("salvage value", fmt(salvage), "query"))
        compared = repair_cost + salvage
        limit = acv
        basis = f"{fmt(repair_cost)} repair + {fmt(salvage)} salvage = {fmt(compared)} vs ACV {fmt(acv)}"
    else:
        compared = repair_cost
        limit = (acv * rule["ratio"]).quantize(Decimal("0.01"))
        basis = f"{fmt(repair_cost)} repair vs {rule['ratio'] * 100:.0f}% of ACV {fmt(acv)} = {fmt(limit)}"
    total = compared >= limit
    return {"tool": "total_loss_check", "status": "total_loss" if total else "repairable",
            "basis": basis, "compared": compared, "limit": limit, "inputs": inputs, "missing": []}


def deductible_apply(*, lines: Iterable[Dict[str, Any]], plan: str, deductible: str,
                     endorsements: Iterable[str], custom_ops: Iterable[str] = (),
                     custom_source: Optional[str] = None,
                     excluded: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Payout math: approved lines, exclusions, custom-equipment limit, glass waiver, deductible.

    `lines` are dicts with `op`, `cat` and `approved` (labor_rate_check output, which has
    already removed any labor billed over the cap). `excluded` maps an op to the reason.
    """
    endorsements = list(endorsements)
    excluded = excluded or {}
    custom_ops = set(custom_ops)
    letter = plan_letter(plan)
    coverage = coverage_of(deductible)
    stated = money(deductible)
    inputs = [cite("policy plan", plan, DOSSIER), cite("deductible on policy", deductible, DOSSIER)]
    notes: List[str] = []
    table = DEDUCTIBLES.get((letter, coverage)) if letter and coverage else None
    if table is not None:
        inputs.append(cite(f"Plan {letter} {coverage} deductible", fmt(table), DEDUCTIBLE_TABLE))
        if table != stated:
            notes.append(f"Policy deductible {fmt(stated)} differs from the Plan {letter} table value {fmt(table)}; "
                         "the policy's own figure is used.")

    rows = []
    covered = ZERO
    glass = ZERO
    custom = ZERO
    for line in lines:
        amount = line["approved"]
        if line["op"] in excluded:
            rows.append({"op": line["op"], "amount": ZERO, "note": f"excluded: {excluded[line['op']]}"})
            continue
        if line["op"] in custom_ops:
            custom += amount
            rows.append({"op": line["op"], "amount": amount, "note": "custom equipment"})
            continue
        if is_glass_line(line):
            glass += amount
        covered += amount
        rows.append({"op": line["op"], "amount": amount, "note": "glass" if is_glass_line(line) else ""})

    custom_paid = custom
    limit_text = next((e for e in endorsements if "custom equipment" in e.lower()), None)
    if custom:
        inputs.append(cite("custom equipment items", fmt(custom), custom_source or DOSSIER))
        if limit_text:
            limit = endorsement_limit(limit_text)
            inputs.append(cite("custom equipment limit", fmt(limit), DOSSIER, "endorsements"))
            inputs.append(cite("Form 402 standard limit", fmt(CPE_STANDARD_LIMIT), CPE_FORM, "2. Coverage Limits & Valuation"))
            if limit != CPE_STANDARD_LIMIT:
                notes.append(f"The policy lists a {fmt(limit)} custom-equipment limit; Form 402's standard "
                             f"limit is {fmt(CPE_STANDARD_LIMIT)}. Confirm the declared limit.")
            custom_paid = min(custom, limit)
        else:
            custom_paid = ZERO
            notes.append("No Custom Equipment endorsement on the policy: custom items are not covered.")

    gross = covered + custom_paid
    has_glass_rider = any("glass" in e.lower() for e in endorsements)
    waived = has_glass_rider and coverage == "comprehensive" and glass > 0
    if waived:
        inputs.append(cite("glass deductible waiver", "Comprehensive deductible waived for glass",
                           GLASS_RIDER, "1. Deductible Waiver"))
        # The deductible applies only to the non-glass part of the loss.
        applied = min(stated, gross - glass)
    else:
        applied = min(stated, gross)
    payout = gross - applied
    return {"tool": "deductible_apply", "rows": rows, "covered": covered, "glass": glass,
            "custom": custom, "custom_paid": custom_paid, "gross": gross,
            "deductible": stated, "deductible_applied": applied, "glass_waiver": waived,
            "payout": payout, "notes": notes, "inputs": inputs}


def add_business_days(day: str, count: int) -> str:
    """'2026-01-16' (a Friday) + 2 business days -> '2026-01-20'. Holidays are not modelled."""
    current = date.fromisoformat(day)
    while count:
        current += timedelta(days=1)
        if current.weekday() < 5:
            count -= 1
    return current.isoformat()


def _wear_item(op: str) -> Optional[str]:
    text = op.lower()
    return next((item for item, pattern in WEAR_ITEMS.items() if pattern.search(text)), None)


def _betterment(row: Dict[str, Any], price: Decimal) -> tuple:
    """(amount, flag or None, citation or None) for one supplement line."""
    item = _wear_item(row["op"])
    if item is None:
        return ZERO, None, None  # body panels, glass and structural parts never take betterment
    wear = row.get("wear") or {}
    if item == "tire":
        if "tread_32nds" not in wear:
            return ZERO, "betterment applies but the remaining tread depth is not on file", None
        usable = TIRE_NEW_TREAD - TIRE_MIN_TREAD
        worn = min(max(TIRE_NEW_TREAD - Decimal(str(wear["tread_32nds"])), ZERO), Decimal(usable))
        amount = (price * worn / usable).quantize(Decimal("0.01"))
        basis = f"{fmt(price)} x ({TIRE_NEW_TREAD} - {wear['tread_32nds']})/{usable} worn = {fmt(amount)}"
        return amount, None, cite(f"{row['op']} betterment", basis, BETTERMENT_GUIDE, "2. Tire Betterment")
    if item == "battery":
        if "battery_age_months" not in wear:
            return ZERO, "betterment applies but the battery's age is not on file", None
        months = min(int(wear["battery_age_months"]), BATTERY_LIFE_MONTHS)
        amount = (price * months / BATTERY_LIFE_MONTHS).quantize(Decimal("0.01"))
        basis = f"{fmt(price)} x {months}/{BATTERY_LIFE_MONTHS} months = {fmt(amount)}"
        return amount, None, cite(f"{row['op']} betterment", basis, BETTERMENT_GUIDE, "3. Battery Betterment")
    return ZERO, f"betterment applies to {item} parts but the guideline gives no formula: adjuster to calculate", None


def _parts_flag(row: Dict[str, Any], standard: str) -> Optional[str]:
    part = (row.get("part") or "").upper()
    if not part:
        return None
    if standard == "oem_rider" and part != "OEM":
        return f"parts: {part.title()} part billed, but the OEM rider requires OEM"
    if standard == "lkq" and part == "OEM":
        return "parts: OEM billed; the SOP standard for this vehicle is LKQ or certified aftermarket"
    if standard == "sop_oem_safety" and part == "OEM" and row["cat"] not in SAFETY_CATEGORIES:
        return "parts: OEM billed on a non-safety part; the SOP calls for LKQ or aftermarket pricing"
    if standard == "sop_oem_safety" and part != "OEM" and row["cat"] in SAFETY_CATEGORIES:
        return f"parts: {part.title()} part billed on a safety part; the SOP requires OEM"
    if standard in ("rider_ineligible", "sop_gap"):
        return f"parts: {part.title()} part billed; parts standard is an adjuster decision for this vehicle"
    return None


def supplement_review(*, original: Iterable[Dict[str, Any]], supplements: Iterable[Dict[str, Any]],
                      vehicle: str, loss_year: int, endorsements: Iterable[str],
                      mileage: Optional[int] = None) -> Dict[str, Any]:
    """Check each supplement line against the same rules as the original estimate.

    Per line: labor over the SOP cap, betterment on wear items, and the parts standard. Per
    supplement: documentation, the reinspection threshold (single and cumulative) and the
    response due date. Amounts are what the guidelines support; the decision stays with the
    adjuster.
    """
    endorsements = list(endorsements)
    supplements = list(supplements)
    base = labor_rate_check(original)
    original_approved = sum((line.get("approved", line["total"]) for line in base["lines"]), ZERO)
    parts = oem_rider_check(vehicle=vehicle, loss_year=loss_year, endorsements=endorsements, mileage=mileage)
    inputs = [cite("original approved estimate", fmt(original_approved), DOSSIER, "estimate at SOP labor caps"),
              cite("supplement labor", "same caps as the original estimate", SUPPLEMENT_SOP, "1. Supplement Submission")]
    if not supplements:
        return {"tool": "supplement_review", "status": "no_supplement", "supplements": [],
                "original_approved": original_approved, "cumulative_billed": ZERO, "parts": parts, "inputs": inputs}
    inputs += [cite("reinspection threshold", f"single supplement over {fmt(REINSPECTION_SINGLE)} or all supplements "
                    f"over {REINSPECTION_SHARE * 100:.0f}% of the original approved estimate", SUPPLEMENT_SOP,
                    "2. Reinspection Threshold"),
               cite("parts standard", parts["standard"], OEM_RIDER if parts["has_rider"] else LABOR_SOP,
                    "2. Eligibility & Term Limits" if parts["has_rider"] else "3. Aftermarket vs. OEM Parts Policy")]

    reviewed = []
    cumulative = ZERO
    used_caps = set()
    for supp in supplements:
        labor = labor_rate_check(supp["lines"])
        used_caps.update(line["labor_class"] for line in labor["lines"] if line["status"] != "not_checked")
        rows = []
        for row, checked in zip(supp["lines"], labor["lines"], strict=True):
            billed = checked["total"]
            excess = checked.get("excess", ZERO)
            flags = []
            if checked["status"] == "over_cap":
                flags.append(f"labor {fmt(checked['rate'])}/hr over the {fmt(checked['cap'])}/hr cap")
            # Betterment is on the replacement part's price: labor is never prorated.
            price = checked["parts"] if "parts" in checked else billed
            amount, flag, citation = _betterment(row, price)
            if flag:
                flags.append(flag)
            if citation:
                inputs.append(citation)
            parts_flag = _parts_flag(row, parts["status"])
            if parts_flag:
                flags.append(parts_flag)
            rows.append({"line": checked["line"], "op": row["op"], "cat": row["cat"], "billed": billed,
                         "labor_excess": excess, "betterment": amount, "approved": billed - excess - amount,
                         "flags": flags})
        billed = sum((r["billed"] for r in rows), ZERO)
        cumulative += billed
        flags = []
        if not supp.get("photos"):
            flags.append("teardown photos missing: the SOP requires itemized lines and photos")
        if not supp.get("submitted_before_work", True):
            flags.append("work was performed before the supplement was submitted")
        share = cumulative / original_approved if original_approved else Decimal("1")
        reasons = []
        if billed > REINSPECTION_SINGLE:
            reasons.append(f"this supplement ({fmt(billed)}) exceeds {fmt(REINSPECTION_SINGLE)}")
        if share > REINSPECTION_SHARE:
            reasons.append(f"supplements to date ({fmt(cumulative)}) are {share * 100:.1f}% of the original "
                           f"approved estimate, over {REINSPECTION_SHARE * 100:.0f}%")
        complete = not flags
        due = add_business_days(supp["received"], SUPPLEMENT_RESPONSE_DAYS) if complete and supp.get("received") else None
        inputs.append(cite(f"{supp['id']} received", supp.get("received", "unknown"), DOSSIER, "supplement"))
        reviewed.append({"id": supp["id"], "received": supp.get("received"), "billed": billed,
                         "approved": sum((r["approved"] for r in rows), ZERO), "lines": rows, "flags": flags,
                         "reinspection": bool(reasons), "reinspection_reason": "; ".join(reasons),
                         "cumulative_billed": cumulative, "due": due})

    for labor_class in sorted(used_caps):
        inputs.append(cite(f"{LABOR_CAP_LABELS[labor_class]} cap", f"{fmt(LABOR_CAPS[labor_class])}/hr",
                           LABOR_SOP, "1. Standard Repair Labor Rates"))
    if any(s["due"] for s in reviewed):
        inputs.append(cite("response time", f"{SUPPLEMENT_RESPONSE_DAYS} business days from complete documentation",
                           SUPPLEMENT_SOP, "3. Response Time"))
    if any(s["flags"] for s in reviewed):
        status = "hold_documentation"
    elif any(s["reinspection"] for s in reviewed):
        status = "reinspection_required"
    else:
        status = "approve_on_photos"
    return {"tool": "supplement_review", "status": status, "supplements": reviewed,
            "original_approved": original_approved, "cumulative_billed": cumulative, "parts": parts,
            "inputs": inputs}


def _valuation_flags(valuations: List[Dict[str, Any]]) -> List[str]:
    flags = []
    accepted = [v for v in valuations if ACCEPTED_VALUATION_PATTERN.search(v["source"])]
    for v in valuations:
        if "kelley" in v["source"].lower() or "kbb" in v["source"].lower():
            flags.append(f"{v['source']} is a secondary source (historical reference only) and does not count "
                         "toward the ACV")
    if len(accepted) < MIN_VALUATION_SOURCES:
        flags.append(f"ACV needs at least two independent valuation sources; {len(accepted)} acceptable "
                     "source(s) on file")
    return flags


def total_loss_explain(*, original: Iterable[Dict[str, Any]], supplements: Iterable[Dict[str, Any]],
                       vehicle: str, loss_year: int, endorsements: Iterable[str], state: Optional[str],
                       acv: Optional[Decimal], salvage: Optional[Decimal] = None,
                       valuations: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Explain the total-loss decision: the state rule, the math, the headroom, and what follows.

    Repair cost is the original estimate at the SOP labor caps plus the supported amount of every
    documented supplement (supplement_review), so the explainer can say whether the supplements
    flip the result. Supplements held for documentation are reported as pending, not counted.
    """
    supp = supplement_review(original=original, supplements=supplements, vehicle=vehicle,
                             loss_year=loss_year, endorsements=endorsements)
    original_cost = supp["original_approved"]
    supplement_cost = sum((s["approved"] for s in supp["supplements"] if not s["flags"]), ZERO)
    pending_cost = sum((s["approved"] for s in supp["supplements"] if s["flags"]), ZERO)
    repair_cost = original_cost + supplement_cost
    valuations = list(valuations)
    check = total_loss_check(state=state, repair_cost=repair_cost, acv=acv, salvage=salvage)
    inputs = [cite("original repair at SOP caps", fmt(original_cost), DOSSIER, "estimate"),
              cite("supported supplements", fmt(supplement_cost), DOSSIER, "supplement_review")]
    inputs += [item for item in check["inputs"] if item["name"] != "repair cost"]
    for v in valuations:
        inputs.append(cite(f"valuation: {v['source']}", fmt(money(v["acv"])), DOSSIER, "valuation reports"))
    if valuations or acv is not None:
        inputs.append(cite("valuation sources", "minimum of two independent; KBB secondary", TOTAL_LOSS_SOP,
                           "2. Valuation Methodology"))
    result = {"tool": "total_loss_explain", "status": check["status"], "missing": check.get("missing", []),
              "reason": check.get("reason"), "original_cost": original_cost, "supplement_cost": supplement_cost,
              "pending_cost": pending_cost, "pending_status": None, "repair_cost": repair_cost, "supplements": supp["supplements"], "steps": [], "margin": None,
              "break_even_repair": None, "limit": None, "original_status": None, "flipped_by_supplements": False,
              "retained_payout": None, "valuation_flags": _valuation_flags(valuations), "next_steps": [],
              "inputs": inputs}
    if check["status"] not in ("total_loss", "repairable"):
        return result

    rule = TOTAL_LOSS_RULES[state]
    steps = [f"{state} rule: {rule['text']} ({TOTAL_LOSS_SOP}, section 1).",
             f"Repair cost: {fmt(original_cost)} original at the SOP labor caps + {fmt(supplement_cost)} "
             f"supported supplements = {fmt(repair_cost)}."]
    if rule["kind"] == "formula":
        break_even = acv - salvage
        steps.append(f"Repair + salvage: {fmt(repair_cost)} + {fmt(salvage)} = {fmt(repair_cost + salvage)}, "
                     f"compared with ACV {fmt(acv)}.")
    else:
        break_even = check["limit"]
        steps.append(f"Threshold: {rule['ratio'] * 100:.0f}% of ACV {fmt(acv)} = {fmt(check['limit'])}, "
                     f"compared with repair cost {fmt(repair_cost)}.")
    margin = break_even - repair_cost
    steps.append(f"Repairs can rise {fmt(margin)} before the vehicle is a total loss." if margin > 0 else
                 f"Repair cost is {fmt(-margin)} over the threshold." if margin < 0 else
                 "Repair cost is exactly at the threshold, which the rule counts as a total loss.")
    original_check = total_loss_check(state=state, repair_cost=original_cost, acv=acv, salvage=salvage)
    result.update(steps=steps, margin=margin, break_even_repair=break_even, limit=check["limit"],
                  original_status=original_check["status"],
                  flipped_by_supplements=original_check["status"] != check["status"])
    if pending_cost:
        pending_check = total_loss_check(state=state, repair_cost=repair_cost + pending_cost, acv=acv, salvage=salvage)
        result["pending_status"] = pending_check["status"]
        steps.append(f"Not counted: {fmt(pending_cost)} in supplements held for documentation; with them the "
                     f"vehicle would be {pending_check['status'].replace('_', ' ')}.")
    if salvage is not None:
        result["retained_payout"] = acv - salvage
    if check["status"] == "total_loss":
        result["next_steps"] = [
            "Title: surrender to the DMV for re-branding, 'Salvage' if repairable or 'Junk / Destruction' if it "
            "cannot be safely rebuilt (adjuster's call).",
            f"Storage: covered up to ${STORAGE_DAILY_CAP}/day; notify the policyholder and facility in writing, and "
            f"storage more than {STORAGE_NOTICE_DAYS} calendar days after that notice is the policyholder's.",
            f"Transfer the vehicle to the salvage vendor's yard within {SALVAGE_TRANSFER_DAYS} business days of "
            "the total loss declaration.",
        ]
        inputs += [cite("title branding", "Salvage or Junk / Destruction", TOTAL_LOSS_SOP, "3. Salvage & Title Branding"),
                   cite("storage cap and cut-off", f"${STORAGE_DAILY_CAP}/day; {STORAGE_NOTICE_DAYS} calendar days "
                        "after notice", STORAGE_SOP, "1-2. Covered Storage, Notice and Cut-Off"),
                   cite("salvage transfer", f"{SALVAGE_TRANSFER_DAYS} business days", STORAGE_SOP, "3. Moving the Vehicle")]
    if salvage is not None:
        inputs.append(cite("owner retains salvage", "salvage value deducted from the payout", TOTAL_LOSS_SOP,
                           "3. Salvage & Title Branding"))
    return result


def _pct(share: Decimal) -> str:
    return f"{share * 100:.0f}%"


def _comparative_rule(state: Optional[str], loss_date: Optional[str]) -> tuple:
    """(rule or None, flag or None) for the loss state and date."""
    rule = COMPARATIVE_FAULT.get(state or "")
    if rule is None:
        return None, f"{LIABILITY_SOP} has no comparative-fault rule for {state or 'an unknown state'}"
    if "since" in rule:
        if loss_date is None:
            return None, f"loss date not on file; the {state} rule applies only to accidents on or after {rule['since']}"
        if loss_date < rule["since"]:
            return None, (f"{LIABILITY_SOP} gives no comparative-fault rule for {state} accidents before "
                          f"{rule['since']}; recovery share needs counsel input")
    return rule, None


def subrogation_triage(*, state: Optional[str], loss_date: Optional[str], damages: Decimal,
                       deductible: Optional[Decimal], liability: Dict[str, Any]) -> Dict[str, Any]:
    """Screen a claim for subrogation: referral criteria, liability evidence, recovery math, forum.

    Third-party fault is the assessment on file, or the rear-end presumption when the insured was
    the lead car and nothing rebuts it. Recovery is computed only when both the fault share and the
    state's comparative-fault rule are known. The referral itself is the adjuster's decision.
    """
    third_party = liability.get("third_party")
    tp_fault = liability.get("third_party_fault")
    rebuttal = list(liability.get("rebuttal") or [])
    causes = list(liability.get("causes") or [])
    evidence = list(liability.get("evidence") or [])
    first_payment = liability.get("first_payment")
    collision_source = liability.get("collision_source", DOSSIER)
    inputs = [cite("loss state", state or "unknown", DOSSIER, "repair facility"),
              cite("loss date", loss_date or "not on file", DOSSIER),
              cite("damages", fmt(damages), DOSSIER, "estimate at SOP caps + supported supplements"),
              cite("deductible", fmt(deductible) if deductible is not None else "not on file", DOSSIER, "policy"),
              cite("identified third party", third_party or "none", liability.get("third_party_source", DOSSIER))]
    flags: List[str] = []
    indicators: List[str] = []

    # Fault: an assessment on file wins; otherwise the SOP's rear-end presumption, if unrebutted.
    fault_basis = None
    rear_end = liability.get("collision") == "rear_end"
    position = liability.get("insured_position")
    if rear_end:
        inputs.append(cite("collision", f"rear-end; insured vehicle {position or 'position unknown'}", collision_source))
    if tp_fault is not None:
        tp_fault = Decimal(str(tp_fault))
        fault_basis = "assessed"
        inputs.append(cite("third-party fault (assessed)", _pct(tp_fault), liability.get("fault_source") or DOSSIER))
    elif rear_end and position in ("lead", "following") and not rebuttal:
        tp_fault = Decimal("1") if position == "lead" else ZERO
        fault_basis = "presumption"
        inputs.append(cite("rear-end presumption", "the following driver is presumed at fault (rebuttable)",
                           LIABILITY_SOP, "1. Evidence Required"))
    elif rear_end and rebuttal:
        inputs.append(cite("rebuttal evidence", "; ".join(rebuttal), collision_source))
        flags.append(f"rear-end presumption rebutted by {'; '.join(rebuttal)}; liability needs an assessment")
    elif third_party:
        flags.append("third-party fault is not assessed on file; liability needs an assessment")
    insured_fault = Decimal("1") - tp_fault if tp_fault is not None else None
    fault_unknown = tp_fault is None and bool(third_party)

    if tp_fault is not None:
        inputs.append(cite("referral fault threshold", f"third party {_pct(SUBRO_FAULT_THRESHOLD)} or more at fault",
                           SUBRO_SOP, "1. Referral Criteria"))
        if tp_fault >= SUBRO_FAULT_THRESHOLD:
            indicators.append(f"third party {_pct(tp_fault)} at fault ({fault_basis}), at or above "
                              f"{_pct(SUBRO_FAULT_THRESHOLD)}")
    for item in causes:
        label = SUBRO_CAUSES[item["cause"]]
        indicators.append(f"loss caused by {label}")
        inputs.append(cite(f"cause: {label}", "established on file", item["source"]))
    if causes:
        inputs.append(cite("referral causes", "defective part, public-entity road hazard or commercial vehicle",
                           SUBRO_SOP, "1. Referral Criteria"))
    if indicators and not third_party:
        flags.append("responsible party not identified: recovery needs an identified third party")

    # Liability evidence: at least two distinct types before a liability decision.
    for item in evidence:
        if item["type"] not in LIABILITY_EVIDENCE_TYPES:
            raise ValueError(f"unknown evidence type {item['type']!r}")
        inputs.append(cite(f"evidence: {item['type'].replace('_', ' ')}", item.get("detail", ""), item["source"]))
    evidence_types = sorted({item["type"] for item in evidence})
    needs_liability = bool(indicators) or fault_unknown
    short_evidence = len(evidence_types) < MIN_LIABILITY_EVIDENCE
    if needs_liability:
        inputs.append(cite("liability evidence", f"at least {MIN_LIABILITY_EVIDENCE} of: police report, statements "
                           "from both drivers, witness statement, photos, telematics or dash-camera data",
                           LIABILITY_SOP, "1. Evidence Required"))
        if short_evidence:
            missing = [t.replace("_", " ") for t in LIABILITY_EVIDENCE_TYPES if t not in evidence_types]
            flags.append(f"{len(evidence_types)} liability evidence type(s) on file; the SOP needs at least "
                         f"{MIN_LIABILITY_EVIDENCE}. Not on file: {', '.join(missing)}")

    if not needs_liability:
        status = "no_indicator"
    elif short_evidence or fault_unknown or not third_party:
        status = "refer_after_evidence"
    else:
        status = "refer"

    # Recovery math: only with a known fault share and a known comparative-fault rule.
    rule = share = expected = reimbursed = None
    if tp_fault is not None:
        rule, rule_flag = _comparative_rule(state, loss_date)
        if rule_flag:
            flags.append(rule_flag)
        else:
            inputs.append(cite(f"{state} comparative fault", rule["text"], LIABILITY_SOP,
                               "2. Comparative Fault by State (demo table)"))
            share = tp_fault
            if rule["kind"] == "modified" and insured_fault > rule["bar"]:
                share = ZERO
                flags.append(f"insured is {_pct(insured_fault)} at fault: the {state} 51% bar blocks any recovery")
            expected = (damages * share).quantize(Decimal("0.01"))
            if deductible is not None:
                reimbursed = (deductible * share).quantize(Decimal("0.01"))
                inputs.append(cite("deductible reimbursement", "same percentage of the deductible as of damages "
                                   "recovered (pro rata)", SUBRO_SOP, "2. Deductible Reimbursement"))
    if deductible is None and needs_liability:
        flags.append("deductible not on file: reimbursement cannot be computed")

    forum = referral_due = None
    if status != "no_indicator":
        basis = expected if expected is not None else damages
        forum = "inter-company arbitration" if basis <= ARBITRATION_LIMIT else "Subrogation Counsel review"
        inputs.append(cite("forum", f"{fmt(ARBITRATION_LIMIT)} or less: inter-company arbitration; above: "
                           "Subrogation Counsel", SUBRO_SOP, "3. Inter-Company Arbitration"))
        inputs.append(cite("referral deadline", f"{SUBRO_REFERRAL_DAYS} days from the first physical damage payment",
                           SUBRO_SOP, "1. Referral Criteria"))
        inputs.append(cite("first physical damage payment", first_payment or "not on file", DOSSIER, "payments"))
        if first_payment:
            referral_due = (date.fromisoformat(first_payment) + timedelta(days=SUBRO_REFERRAL_DAYS)).isoformat()
        else:
            flags.append(f"no physical damage payment on file: the {SUBRO_REFERRAL_DAYS}-day referral clock starts "
                         "at the first physical damage payment")
    notice = (f"Notify the insured in writing within {SUBRO_NOTICE_BUSINESS_DAYS} business days of any recovery "
              f"({SUBRO_SOP}, section 2).")
    inputs.append(cite("recovery notice", f"written, within {SUBRO_NOTICE_BUSINESS_DAYS} business days", SUBRO_SOP,
                       "2. Deductible Reimbursement"))
    if liability.get("note"):
        flags.append(liability["note"])
    return {"tool": "subrogation_triage", "status": status, "third_party": third_party, "fault_basis": fault_basis,
            "third_party_fault": tp_fault, "insured_fault": insured_fault, "indicators": indicators,
            "flags": flags, "evidence_types": evidence_types, "rule": rule["text"] if rule else None,
            "recoverable_share": share, "expected_recovery": expected, "deductible_reimbursed": reimbursed,
            "forum": forum, "referral_due": referral_due, "notice": notice, "inputs": inputs}


def _period_check(loss_date: str, policy: Dict[str, Any]) -> Dict[str, Any]:
    reasons = []
    if loss_date < policy["effective"]:
        reasons.append(f"loss date {loss_date} is before the policy effective date {policy['effective']}")
    elif loss_date >= policy["expiration"]:
        reasons.append(f"loss date {loss_date} is on or after the policy expiration date {policy['expiration']}")
    cancelled = policy.get("cancelled")
    if cancelled and loss_date >= cancelled:
        reasons.append(f"loss date {loss_date} is on or after the cancellation effective date {cancelled}")
    for lapse in policy.get("lapses") or []:
        if lapse["start"] <= loss_date <= lapse["end"]:
            reasons.append(f"loss date {loss_date} falls within a coverage lapse ({lapse['start']} to "
                           f"{lapse['end']})")
    if reasons:
        return {"check": "period", "result": "issue", "detail": "; ".join(reasons), "source": COVERAGE_SOP,
                "section": "1. Policy Period"}
    return {"check": "period", "result": "pass",
            "detail": f"loss date {loss_date} is within the policy period {policy['effective']} to "
                       f"{policy['expiration']}", "source": COVERAGE_SOP, "section": "1. Policy Period"}


def _driver_check(driver: Optional[str], policy: Dict[str, Any]) -> Dict[str, Any]:
    fact_source = policy.get("source", "claim record")
    if driver is None:
        return {"check": "driver", "result": "missing", "detail": "no driver is on file for this loss",
                "source": fact_source, "section": None}
    if driver in (policy.get("excluded_drivers") or ()):
        return {"check": "driver", "result": "issue",
                "detail": f"{driver} is named on an Excluded Driver Endorsement and is not covered operating "
                           "this vehicle", "source": COVERAGE_SOP, "section": "2. Drivers"}
    if driver in (policy.get("listed_drivers") or ()):
        return {"check": "driver", "result": "pass", "detail": f"{driver} is listed on the declarations page",
                "source": COVERAGE_SOP, "section": "2. Drivers"}
    permissive = policy.get("permissive") or {}
    if driver in permissive:
        documented = permissive[driver]
        if documented:
            return {"check": "driver", "result": "pass",
                    "detail": f"{driver} is a permissive user; permission documented: {documented}",
                    "source": COVERAGE_SOP, "section": "2. Drivers"}
        return {"check": "driver", "result": "issue",
                "detail": f"{driver} is a permissive user but how permission was given is not documented",
                "source": COVERAGE_SOP, "section": "2. Drivers"}
    return {"check": "driver", "result": "missing",
            "detail": f"{driver} is not listed, excluded, or recorded as a permissive user on this policy",
            "source": fact_source, "section": None}


def _peril_check(peril: str, plan: str) -> tuple:
    """(coverage_part, check row)."""
    if peril in COLLISION_PERILS:
        part = "collision"
    elif peril in COMPREHENSIVE_PERILS:
        part = "comprehensive"
    else:
        raise ValueError(f"unknown peril {peril!r}")
    letter = plan_letter(plan)
    if (letter, part) in DEDUCTIBLES:
        return part, {"check": "peril", "result": "pass",
                      "detail": f"{peril} is a {part} peril; Plan {letter} carries {part} coverage",
                      "source": COVERAGE_SOP, "section": "3. Peril and Coverage Part"}
    return part, {"check": "peril", "result": "issue",
                  "detail": f"{peril} is a {part} peril; Plan {letter} does not carry {part} coverage",
                  "source": COVERAGE_SOP, "section": "3. Peril and Coverage Part"}


def _notice_check(loss_date: str, reported: Optional[str]) -> Dict[str, Any]:
    if reported is None:
        return {"check": "notice", "result": "missing", "detail": "the date the loss was reported is not on file",
                "source": DOSSIER, "section": None}
    days = (date.fromisoformat(reported) - date.fromisoformat(loss_date)).days
    if days > NOTICE_DAYS:
        return {"check": "notice", "result": "issue",
                "detail": f"reported {days} days after the loss, over the {NOTICE_DAYS}-day notice requirement: "
                           "document it; refer to a claims supervisor if the delay prejudiced the investigation",
                "source": COVERAGE_SOP, "section": "4. Notice of Loss"}
    return {"check": "notice", "result": "pass",
            "detail": f"reported {days} day(s) after the loss, within the {NOTICE_DAYS}-day notice requirement",
            "source": COVERAGE_SOP, "section": "4. Notice of Loss"}


def coverage_check(*, loss_date: str, reported: Optional[str], peril: str, plan: str,
                   policy: Dict[str, Any], driver: Optional[str]) -> Dict[str, Any]:
    """Check policy period, driver, peril/coverage part and notice of loss for one claim.

    Every row is advisory -- a coverage question for the adjuster, never a denial or approval.
    """
    fact_source = policy.get("source", "claim record")
    coverage_part, peril_row = _peril_check(peril, plan)
    checks = [_period_check(loss_date, policy), _driver_check(driver, policy), peril_row,
              _notice_check(loss_date, reported)]

    if any(c["result"] == "issue" for c in checks):
        status = "coverage_question"
    elif any(c["result"] == "missing" for c in checks):
        status = "incomplete"
    else:
        status = "verified"

    letter = plan_letter(plan)
    inputs = [
        cite("loss date", loss_date, DOSSIER),
        cite("policy effective", policy["effective"], fact_source),
        cite("policy expiration", policy["expiration"], fact_source),
        cite("policy period rule", "covered only on or after the effective date and before the expiration date; "
             "not covered on or after a cancellation date or during a lapse", COVERAGE_SOP, "1. Policy Period"),
        cite("driver", driver or "not on file", fact_source),
        cite("listed drivers", ", ".join(policy.get("listed_drivers") or []) or "none", fact_source),
        cite("excluded drivers", ", ".join(policy.get("excluded_drivers") or []) or "none", fact_source),
        cite("driver coverage rule", "listed drivers are covered; an excluded driver is not; an unlisted driver "
             "with permission is a permissive user and the permission must be documented", COVERAGE_SOP,
             "2. Drivers"),
        cite("peril", peril, DOSSIER),
        cite("policy plan", plan, DOSSIER),
        cite("peril coverage rule", "collision covers impact and rollover; comprehensive covers theft, "
             "vandalism, hail, flood, fire, falling objects, animal strikes and glass; Plan C carries collision "
             "only", COVERAGE_SOP, "3. Peril and Coverage Part"),
        cite(f"Plan {letter} {coverage_part} coverage", "carried" if (letter, coverage_part) in DEDUCTIBLES
             else "not carried", DEDUCTIBLE_TABLE),
        cite("reported", reported or "not on file", DOSSIER),
        cite("notice rule", f"the insured must report a loss within {NOTICE_DAYS} days", COVERAGE_SOP,
             "4. Notice of Loss"),
    ]
    if policy.get("cancelled"):
        inputs.append(cite("policy cancelled", policy["cancelled"], fact_source))
    if policy.get("lapses"):
        inputs.append(cite("coverage lapse(s)",
                           "; ".join(f"{lapse['start']} to {lapse['end']}" for lapse in policy["lapses"]),
                           fact_source))

    return {"tool": "coverage_check", "status": status, "coverage_part": coverage_part, "checks": checks,
            "inputs": inputs}


def _chronology_check(reported: Optional[str], policy: Dict[str, Any]) -> Dict[str, Any]:
    if reported is None:
        return {"indicator": "chronology", "result": "missing",
                "detail": "the date the loss was reported is not on file", "source": DOSSIER, "section": None}
    report_date = date.fromisoformat(reported)
    candidates = [("policy inception", policy["effective"])]
    for change in policy.get("coverage_changes") or []:
        candidates.append((change["change"], change["date"]))
    scored = [(label, trigger, (report_date - date.fromisoformat(trigger)).days) for label, trigger in candidates]
    within = [item for item in scored if 0 <= item[2] <= SIU_CHRONOLOGY_DAYS]
    if within:
        label, trigger, days = min(within, key=lambda item: item[2])
        return {"indicator": "chronology", "result": "present",
                "detail": f"claim reported {days} day(s) after {label} ({trigger}), within "
                           f"{SIU_CHRONOLOGY_DAYS} days", "source": FRAUD_SOP, "section": "2. Red Flags Checklist"}
    label, trigger, days = min(scored, key=lambda item: abs(item[2]))
    return {"indicator": "chronology", "result": "absent",
            "detail": f"claim reported {days} day(s) after {label} ({trigger}), outside "
                       f"{SIU_CHRONOLOGY_DAYS} days", "source": FRAUD_SOP, "section": "2. Red Flags Checklist"}


def _paper_accident_check(loss_time: Optional[str], scene: Dict[str, Any]) -> Dict[str, Any]:
    single_vehicle = bool(scene.get("single_vehicle"))
    remote = bool(scene.get("remote"))
    police_report = scene.get("police_report")
    witnesses = list(scene.get("witnesses") or [])
    hours_note = (f"late night is taken here as {SIU_LATE_NIGHT_START}:00-0{SIU_LATE_NIGHT_END}:00; the SOP "
                  "itself does not define the hours")
    known = {"single-vehicle": single_vehicle, "remote area": remote,
             "no police report": police_report is None, "no independent witness": not witnesses}
    failed = [label for label, ok in known.items() if not ok]
    if failed:
        return {"indicator": "paper_accident", "result": "absent",
                "detail": f"not a paper-accident pattern: {', '.join(failed)} not met; {hours_note}",
                "source": FRAUD_SOP, "section": "2. Red Flags Checklist"}
    if loss_time is None:
        return {"indicator": "paper_accident", "result": "missing",
                "detail": "single-vehicle, remote, no police report, no witness, but the time of loss is "
                           f"not on file to check late night; {hours_note}", "source": DOSSIER, "section": None}
    hour = int(loss_time.split(":")[0])
    late_night = hour >= SIU_LATE_NIGHT_START or hour < SIU_LATE_NIGHT_END
    if late_night:
        return {"indicator": "paper_accident", "result": "present",
                "detail": f"single-vehicle collision at {loss_time}, remote area, no police report, no "
                           f"independent witness; {hours_note}", "source": FRAUD_SOP,
                "section": "2. Red Flags Checklist"}
    return {"indicator": "paper_accident", "result": "absent",
            "detail": f"single-vehicle, remote, no police report, no witness, but {loss_time} is not late "
                       f"night; {hours_note}", "source": FRAUD_SOP, "section": "2. Red Flags Checklist"}


def _pre_existing_damage_check(estimate: List[Dict[str, Any]], findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    hits = [(row["op"], DOSSIER) for row in estimate if PRE_EXISTING_DAMAGE_PATTERN.search(row.get("op", ""))]
    dossier_findings = [f for f in findings if f.get("indicator") == "pre_existing_damage"]
    if hits or dossier_findings:
        details = [f"estimate line '{op}' describes rust, corrosion or an old dent" for op, _ in hits]
        details += [f["detail"] for f in dossier_findings]
        sources = [src for _, src in hits] + [f["source"] for f in dossier_findings]
        return {"indicator": "pre_existing_damage", "result": "present", "detail": "; ".join(details),
                "source": "; ".join(dict.fromkeys(sources)), "section": "2. Red Flags Checklist"}
    return {"indicator": "pre_existing_damage", "result": "absent",
            "detail": "no estimate line or dossier finding of rust, corrosion, an old dent, or damage "
                       "predating the loss", "source": FRAUD_SOP, "section": "2. Red Flags Checklist"}


def _duplicate_invoices_check(photos: Optional[List[Dict[str, Any]]],
                              other_claims: List[Dict[str, Any]]) -> Dict[str, Any]:
    photos = list(photos or [])
    other_claims = list(other_claims or [])
    if not photos or not other_claims:
        return {"indicator": "duplicate_invoices", "result": "missing",
                "detail": "not checkable: no photo metadata on file", "source": DOSSIER, "section": None}
    for other in other_claims:
        for other_photo in other.get("photos") or []:
            for photo in photos:
                same_angle = photo.get("angle") and photo.get("angle") == other_photo.get("angle")
                same_date = photo.get("date") and photo.get("date") == other_photo.get("date")
                if same_angle or same_date:
                    return {"indicator": "duplicate_invoices", "result": "present",
                            "detail": f"photo angle or date on this claim matches "
                                       f"{other.get('claim_id', 'another claim')}",
                            "source": other_photo.get("source", DOSSIER), "section": None}
    return {"indicator": "duplicate_invoices", "result": "absent",
            "detail": f"no identical photo angle or treatment date found against {len(other_claims)} other "
                       "claim(s) on file", "source": FRAUD_SOP, "section": "2. Red Flags Checklist"}


def siu_checklist(*, claim_id: str, reported: Optional[str], loss_date: str, loss_time: Optional[str],
                  policy: Dict[str, Any], scene: Dict[str, Any], estimate: Iterable[Dict[str, Any]],
                  findings: Iterable[Dict[str, Any]] = (), other_claims: Iterable[Dict[str, Any]] = (),
                  detected: Optional[str] = None) -> Dict[str, Any]:
    """Screen a claim against the SOP's four SIU red flags. Every indicator carries its own
    citation; the checklist never adjudicates -- referral itself stays with the adjuster.
    """
    estimate = list(estimate)
    findings = list(findings)
    other_claims = list(other_claims)

    indicators = [
        _chronology_check(reported, policy),
        _paper_accident_check(loss_time, scene),
        _pre_existing_damage_check(estimate, findings),
        _duplicate_invoices_check(scene.get("photos"), other_claims),
    ]
    present = [i for i in indicators if i["result"] == "present"]
    missing = [i for i in indicators if i["result"] == "missing"]
    if present:
        status = "refer_to_siu"
    elif missing:
        status = "incomplete"
    else:
        status = "no_indicator"

    inputs = [
        cite("claim", claim_id, DOSSIER),
        cite("loss date", loss_date, DOSSIER),
        cite("reported", reported or "not on file", DOSSIER),
        cite("policy effective", policy.get("effective", "unknown"), policy.get("source", DOSSIER)),
        cite("SIU chronology rule", f"filed within {SIU_CHRONOLOGY_DAYS} days of policy inception or a "
             "coverage upgrade", FRAUD_SOP, "2. Red Flags Checklist"),
        cite("SIU paper-accident rule", "single-vehicle collision, late at night, remote area, no police "
             "report, no independent witness", FRAUD_SOP, "2. Red Flags Checklist"),
        cite("SIU pre-existing-damage rule", "rust, corrosion or old dents that do not match the direction "
             "of force", FRAUD_SOP, "2. Red Flags Checklist"),
        cite("SIU duplicate-invoices rule", "identical photo angles or treatment dates across two claims",
             FRAUD_SOP, "2. Red Flags Checklist"),
    ]

    procedure: List[str] = []
    if status == "refer_to_siu":
        message = (f"Indicators present: refer to SIU within {SIU_REFERRAL_BUSINESS_DAYS} business days of "
                   "detection (adjuster decision).")
        procedure = [
            "Do not inform the policyholder or claimant that a referral is being made.",
            "Compile photo metadata, the police report and telematics history (if available) before the referral.",
            "Continue processing non-disputed elements of the claim unless instructed otherwise by the SIU investigator.",
        ]
        inputs.append(cite("SIU referral procedure", "do not inform the policyholder or claimant; compile "
                           "documentation; continue non-disputed processing", FRAUD_SOP,
                           "3. SIU Referral Procedure"))
        inputs.append(cite("SIU referral deadline", f"{SIU_REFERRAL_BUSINESS_DAYS} business days of detection",
                           FRAUD_SOP, "1. The Importance of Fraud Detection"))
    elif status == "incomplete":
        message = "No indicator is present, but some could not be checked on the file as it stands."
    else:
        message = "No SOP indicator is present on this claim."

    referral_due = add_business_days(detected, SIU_REFERRAL_BUSINESS_DAYS) if detected and status == "refer_to_siu" else None
    if detected:
        inputs.append(cite("detection date", detected, "query"))

    return {"tool": "siu_checklist", "status": status, "indicators": indicators, "message": message,
            "procedure": procedure, "referral_due": referral_due, "inputs": inputs}


def render_inputs(results: Iterable[Dict[str, Any]]) -> List[str]:
    """Markdown table of every tool input with its citation."""
    out = ["", "#### Tool inputs and citations", "| Tool | Input | Value | Source |", "| --- | --- | --- | --- |"]
    for result in results:
        for item in result["inputs"]:
            source = item["source"] + (f", {item['section']}" if item.get("section") else "")
            out.append(f"| `{result['tool']}` | {item['name']} | {item['value']} | {source} |")
    return out
