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


def render_inputs(results: Iterable[Dict[str, Any]]) -> List[str]:
    """Markdown table of every tool input with its citation."""
    out = ["", "#### Tool inputs and citations", "| Tool | Input | Value | Source |", "| --- | --- | --- | --- |"]
    for result in results:
        for item in result["inputs"]:
            source = item["source"] + (f", {item['section']}" if item.get("section") else "")
            out.append(f"| `{result['tool']}` | {item['name']} | {item['value']} | {source} |")
    return out
