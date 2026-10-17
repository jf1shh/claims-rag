"""Regenerates and ingests the golden-eval source documents missing from the
current corpus, non-destructively.

Background: eval/golden_queries.py was verified against the full generated
corpus (generate_massive_dataset.py), but the live rag_store.db was rebuilt
from a smaller generator set (generate_auto_pdfs.py + create_sample_files.py),
so 7 source documents referenced by golden queries exist nowhere in the DB.
Retrieval-eval scores (Context Precision/Recall and answer Correctness) were
being judged against references whose source documents could not be
retrieved -- a measurement gap, not a pipeline regression.

This script regenerates exactly those 7 documents (real binaries, matching
the repo's own generation patterns) and adds them to the SQLite store with
the real embedding engine. It never drops or rewrites existing rows, so it is
safe to re-run (add_document overwrites in place).

Since 2026-10-16 (roadmap P2) it also adds six synthetic sources for domains the
golden set had not covered: subrogation, supplement review, diminished value,
betterment, liability and storage fees.

Run:  .venv/bin/python scripts/rebuild_golden_source_docs.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.rag_engine import EmbeddingEngine, SQLiteVectorStore


def _build_docx(path: str, title: str, sections: list[tuple[str, str]]) -> None:
    import docx

    doc = docx.Document()
    doc.add_heading(title, level=1)
    for heading, body in sections:
        doc.add_heading(heading, level=2)
        doc.add_paragraph(body)
    doc.save(path)


def _build_pdf(path: str, title: str, sections: list[tuple[str, str]]) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    doc = SimpleDocTemplate(path, pagesize=letter, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("DocTitle", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#0f172a"))
    heading_style = ParagraphStyle("DocHeading", parent=styles["Heading2"], fontSize=12, textColor=colors.HexColor("#0284c7"))
    body_style = ParagraphStyle("DocBody", parent=styles["Normal"], fontSize=10, leading=14)

    story = [Paragraph(title, title_style), Spacer(1, 12)]
    for heading, body in sections:
        story.append(Paragraph(heading, heading_style))
        story.append(Paragraph(body, body_style))
        story.append(Spacer(1, 6))
    doc.build(story)


# --------------------------------------------------------------------------- #
# Document content -- kept in sync with generate_massive_dataset.py and the
# verified references in eval/golden_queries.py.
# --------------------------------------------------------------------------- #

def _documents() -> list[tuple[str, str, callable]]:
    """Returns (filename, file_type, build_fn) for each missing golden source."""
    return [
        (
            "DUI_Exclusion_Directive.txt",
            "txt",
            lambda path: open(path, "w", encoding="utf-8").write(
                "AUTO INSURANCE INTERNAL MEMO\n"
                "TO: Claims Adjusting Staff, Legal Department\n"
                "DATE: June 15, 2026\n"
                "SUBJECT: Strict Enforcement of Section 12.2 DUI Exclusion\n\n"
                "Claims handlers are directed to enforce the DUI exclusion under Section 12.2.\n"
                "If a driver is cited for and subsequently convicted of operating the vehicle under the influence\n"
                "of alcohol, narcotics, or chemical substances at the time of a collision, physical damage coverage\n"
                "to the insured vehicle (Collision coverage) is voided.\n\n"
                "Key Procedures:\n"
                "1. Always request blood alcohol concentration (BAC) records or police chemical tests if DUI is mentioned.\n"
                "2. Deny physical damage payouts to the insured driver.\n"
                "3. Third-party liability claims (Property Damage and Bodily Injury liability to third parties) MUST still be paid\n"
                "   to protect the public interest up to state limits, but the insurer must seek recovery from the policyholder.\n"
            ),
        ),
        # NOTE: Endorsement_Custom_Audio_Visual.docx is deliberately NOT rebuilt.
        # The current corpus's own Endorsement_Custom_Equipment_Form402.pdf already
        # covers non-OEM sound systems at $5,000; a second $3,500 custom-sound
        # endorsement would be a contradictory duplicate that makes retrieval a
        # coin flip (custom-av-cap / chen-custom-equipment-cap both reference
        # Form402 in eval/golden_queries.py).
        (
            "Endorsement_Windshield_Zero_Deductible.docx",
            "docx",
            lambda path: _build_docx(
                path,
                "Endorsement: Zero-Deductible Glass Replacement Rider",
                [
                    (
                        "1. Deductible Waiver",
                        "By purchasing this rider, the policyholder's Comprehensive deductible is waived for all windshield "
                        "and safety glass replacements. The insurer will pay the full cost of glass parts, adhesive materials, "
                        "and labor with $0 out-of-pocket cost.",
                    ),
                    (
                        "2. Recalibration coverage",
                        "This rider fully covers ADAS camera recalibration fees associated with the windshield replacement. "
                        "Replacement glass must meet OEM specifications to ensure camera alignment.",
                    ),
                ],
            ),
        ),
        (
            "Endorsement_Rental_Car_Upgrade.docx",
            "docx",
            lambda path: _build_docx(
                path,
                "Endorsement: Premium Rental Vehicle Upgrade Rider",
                [
                    (
                        "1. Rider Benefit",
                        "This endorsement upgrades the standard daily rental reimbursement cap. The daily reimbursement "
                        "limit is increased from $30 per day to $50 per day, allowing the policyholder to lease a mid-size "
                        "SUV or full-size sedan while their primary vehicle is in the repair shop.",
                    ),
                    (
                        "2. Period of Repair rule",
                        "Rental reimbursement is strictly limited to the actual time of active repairs. Delays caused by "
                        "the repair facility or backordered parts do not extend the standard 30-day limit unless "
                        "pre-approved by the claims manager.",
                    ),
                ],
            ),
        ),
        (
            "Adjuster_Guide_Rollover_Claims.docx",
            "docx",
            lambda path: _build_docx(
                path,
                "Adjuster Reference Guide: Vehicle Rollover Claims",
                [
                    (
                        "1. Roof Crush and A-Pillar Damage",
                        "Rollover accidents are severe and regularly result in total losses due to structural roof "
                        "deformation. If the roof has collapsed more than 2 inches, structural frame integrity is lost, "
                        "and repair is prohibited.",
                    ),
                    (
                        "2. Fluid Seepage and Engine Damage",
                        "When a vehicle rolls over, engine oil and coolant seep into the combustion chambers and intake "
                        "manifold. Adjusters must request a mandatory engine compression test before estimating mechanical "
                        "repairs to verify there is no hydro-lock damage.",
                    ),
                ],
            ),
        ),
        (
            "Adjuster_Guide_Rear_Impact.docx",
            "docx",
            lambda path: _build_docx(
                path,
                "Adjuster Reference Guide: Rear-End Collision Damage",
                [
                    (
                        "1. Rear bumper Structure",
                        "Modern rear bumper assemblies contain plastic bumper covers, Styrofoam energy absorbers, steel "
                        "reinforcement bars, and bracket mounts. If the absorber is compressed, it must be replaced. Do not "
                        "attempt to repair safety-critical reinforcement bars.",
                    ),
                    (
                        "2. Sensor Calibration",
                        "Vehicles with Advanced Driver Assistance Systems (ADAS) contain blind-spot sensors and backup "
                        "cameras inside the rear bumper. Any bumper replacement or structural alignment requires a "
                        "mandatory electronic ADAS recalibration ($250-$450 fee).",
                    ),
                ],
            ),
        ),
        (
            "Case_Study_Engine_Hydro_Lock.pdf",
            "pdf",
            lambda path: _build_pdf(
                path,
                "Claims Case Study: Engine Hydro-Lock Damage (ID: 2026-55912)",
                [
                    (
                        "Summary of Occurrence",
                        "On January 20, 2026, the policyholder (driving a 2020 BMW 330i) drove through standing water "
                        "during heavy rain. The engine stalled in the middle of the standing water and the vehicle was "
                        "towed to a mechanic shop. Telematics show the vehicle entered the water at 45 mph and stalled "
                        "with a hydraulic lock flag active.",
                    ),
                    (
                        "Mechanical Diagnosis",
                        "The technician confirmed the engine was hydro-locked. Water had entered the engine air intake "
                        "and filled the cylinders. The telematics log shows the ignition was pressed 3 times attempting "
                        "to restart the stalled engine, drawing water further into the intake and bending the connecting "
                        "rod in cylinder 2, with a hairline block fracture near the starter mount.",
                    ),
                    (
                        "Coverage and Exclusion Ruling",
                        "Damage caused by attempting to restart an engine stalled in water is driver-induced consequential "
                        "damage. Under the policy's mitigation clause, this damage is excluded from coverage. The claim "
                        "was denied for the engine damage caused by the restart attempts; the adjuster noted the initial "
                        "flood-related stall itself would have been a Comprehensive-peril event.",
                    ),
                ],
            ),
        ),
        # ---- P2 (2026-10-16): synthetic sources for the claims domains the golden set did not cover.
        # Rules here are invented for the demo corpus; they do not describe any real carrier's guidelines.
        (
            "SOP_Subrogation_Recovery.pdf",
            "pdf",
            lambda path: _build_pdf(
                path,
                "SOP: Subrogation Referral & Recovery",
                [
                    (
                        "1. Referral Criteria",
                        "A claim must be referred to the Subrogation Unit when a third party is assessed at 25% or more "
                        "fault for the loss, or when the loss was caused by a defective part, a road hazard maintained by "
                        "a public entity, or a commercial vehicle. Referral must be made within 30 days of the first "
                        "physical damage payment.",
                    ),
                    (
                        "2. Deductible Reimbursement",
                        "Recovered funds are applied to the insured's deductible first. If the recovery is partial, the "
                        "insured receives the same percentage of the deductible as the percentage of total damages "
                        "recovered (pro rata). The insured is notified in writing within 10 business days of any recovery.",
                    ),
                    (
                        "3. Inter-Company Arbitration",
                        "Disputed recoveries of $100,000 or less against another signatory carrier are filed in "
                        "inter-company arbitration rather than litigation. Recoveries above $100,000 are reviewed by "
                        "Subrogation Counsel.",
                    ),
                ],
            ),
        ),
        (
            "SOP_Supplement_Review.pdf",
            "pdf",
            lambda path: _build_pdf(
                path,
                "SOP: Repair Supplement Review",
                [
                    (
                        "1. Supplement Submission",
                        "A supplement is a request to add hidden damage found after teardown to an approved estimate. The "
                        "shop must submit itemized supplement lines and teardown photos before the added work is "
                        "performed. Supplement labor is subject to the same caps as the original estimate under the "
                        "Labor Rates SOP.",
                    ),
                    (
                        "2. Reinspection Threshold",
                        "A field reinspection is required before approval when a single supplement exceeds $1,500 or when "
                        "all supplements combined exceed 25% of the original approved estimate. Supplements below both "
                        "thresholds may be approved on photos alone.",
                    ),
                    (
                        "3. Response Time",
                        "The adjuster must respond to a supplement request within 2 business days of receiving complete "
                        "documentation.",
                    ),
                ],
            ),
        ),
        (
            "Guideline_Diminished_Value.docx",
            "docx",
            lambda path: _build_docx(
                path,
                "Claims Guideline: Diminished Value",
                [
                    (
                        "1. First-Party Diminished Value",
                        "The standard auto policy pays to repair or replace damaged property. It does not cover a "
                        "first-party claim for loss of market value after a completed repair (inherent diminished value).",
                    ),
                    (
                        "2. Third-Party Diminished Value Formula",
                        "Third-party diminished value claims against an insured are evaluated by starting from a base "
                        "loss of 10% of the vehicle's pre-loss Actual Cash Value. The base is multiplied by a damage "
                        "modifier (1.00 severe structural damage, 0.75 major structural and panel damage, 0.50 moderate "
                        "panel damage, 0.25 minor panel damage, 0.00 no structural or panel damage) and by a mileage "
                        "modifier (1.00 under 20,000 miles, 0.80 for 20,000-39,999, 0.60 for 40,000-59,999, 0.40 for "
                        "60,000-79,999, 0.20 for 80,000-99,999, 0.00 at 100,000 miles or more).",
                    ),
                ],
            ),
        ),
        (
            "Guideline_Betterment_Adjustments.docx",
            "docx",
            lambda path: _build_docx(
                path,
                "Claims Guideline: Betterment on Wear Items",
                [
                    (
                        "1. When Betterment Applies",
                        "Betterment is taken only on wear items replaced with new parts as part of a covered repair: "
                        "tires, batteries, shock absorbers and exhaust components. Betterment is never taken on body "
                        "panels, glass, or structural collision parts. Every betterment line must be itemized on the "
                        "estimate with its calculation.",
                    ),
                    (
                        "2. Tire Betterment",
                        "Tire betterment equals the replacement tire price multiplied by the share of usable tread already "
                        "worn. Usable tread is measured from 10/32 inch new to the 2/32 inch legal minimum (8/32 inch of "
                        "usable tread).",
                    ),
                    (
                        "3. Battery Betterment",
                        "Battery betterment is prorated over a 60-month service life: the replacement price multiplied by "
                        "the battery's age in months divided by 60.",
                    ),
                ],
            ),
        ),
        (
            "SOP_Liability_Determination.pdf",
            "pdf",
            lambda path: _build_pdf(
                path,
                "SOP: Liability Determination & Comparative Fault",
                [
                    (
                        "1. Evidence Required",
                        "A liability decision must cite at least two of: the police report, statements from both drivers, "
                        "an independent witness statement, scene or damage photos, and telematics or dash-camera data. "
                        "A rear-end collision creates a presumption that the following driver is at fault, which can be "
                        "rebutted by evidence of a sudden unsafe lane change or non-functioning brake lights.",
                    ),
                    (
                        "2. Comparative Fault by State (demo table)",
                        "California applies pure comparative fault: a claimant recovers damages reduced by their own "
                        "percentage of fault, even at 99%. Texas applies modified comparative fault with a 51% bar: a "
                        "claimant more than 50% at fault recovers nothing. Florida applies modified comparative fault with "
                        "a 51% bar for accidents on or after March 24, 2023.",
                    ),
                ],
            ),
        ),
        (
            "SOP_Vehicle_Storage_Fees.txt",
            "txt",
            lambda path: open(path, "w", encoding="utf-8").write(
                "STANDARD OPERATING PROCEDURE: VEHICLE STORAGE FEES\n\n"
                "1. Covered Storage\n"
                "Reasonable storage charges at a tow yard or repair facility are covered as part of a covered collision "
                "or comprehensive loss, up to $45 per day.\n\n"
                "2. Notice and Cut-Off\n"
                "The adjuster must notify the policyholder and the facility in writing when the vehicle is ready to be "
                "moved (inspection complete or total loss declared). Storage accruing more than 3 calendar days after "
                "that notice is the policyholder's responsibility.\n\n"
                "3. Moving the Vehicle\n"
                "To stop accruing charges, the adjuster should arrange transfer of a total-loss vehicle to the salvage "
                "vendor's yard within 3 business days of the total loss declaration.\n"
            ),
        ),
    ]


def main() -> int:
    store = SQLiteVectorStore()
    embedding_engine = EmbeddingEngine()

    with tempfile.TemporaryDirectory(prefix="golden_sources_") as workdir:
        for filename, file_type, build in _documents():
            path = os.path.join(workdir, filename)
            build(path)
            file_size = os.path.getsize(path)

            with open(path, "rb") as fh:
                if file_type == "txt":
                    text = fh.read().decode("utf-8", errors="ignore")
                else:
                    from backend.rag_engine import DocumentParser

                    text = DocumentParser.parse(path, file_type)

            doc_id, parents = store.add_document(
                filename=filename,
                file_type=file_type,
                file_size=file_size,
                text=text,
                embedding_engine=embedding_engine,
                file_path=path,
            )
            print(f"Indexed {filename}: doc_id={doc_id}, parents={parents}")

    total = len(store.get_all_documents())
    print(f"\nDone. Global document count is now {total}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
