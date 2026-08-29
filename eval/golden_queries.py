# Domain-grounded golden query set for AutoClaimsRAG retrieval evaluation.
#
# Every `reference` answer below was verified against the actual text stored in
# rag_store.db (not against agentic_router.py's simulated-mode narrative, which
# was found to contain several unsupported specifics — see README/eval report).
# `claim_id=None` means the query is scoped to global policy documents only;
# otherwise it's scoped to that claim's dossier + global documents, matching
# how the app itself scopes retrieval.
#
# Corpus note (2026-08-29): the live rag_store.db was rebuilt from a smaller
# generator set (generate_auto_pdfs.py + create_sample_files.py) than the one
# this set was originally verified against (generate_massive_dataset.py), which
# left 7 `source` documents absent from the corpus. scripts/rebuild_golden_source_docs.py
# regenerates 6 of them (DUI_Exclusion_Directive.txt, Endorsement_Windshield_Zero_Deductible.docx,
# Endorsement_Rental_Car_Upgrade.docx, Adjuster_Guide_Rollover_Claims.docx,
# Adjuster_Guide_Rear_Impact.docx, Case_Study_Engine_Hydro_Lock.pdf) and ingests
# them non-destructively. The two custom-equipment queries instead point at the
# corpus's own Endorsement_Custom_Equipment_Form402.pdf (which already covers
# non-OEM sound systems at $5,000) -- a separate $3,500 custom-sound endorsement
# was deliberately not rebuilt because it would be a contradictory duplicate.
# All references below were re-verified against the rebuilt corpus on 2026-08-29.

GOLDEN_QUERIES = [
    # ---- Global policy / guideline queries ----
    {
        "id": "labor-mechanical-cap",
        "claim_id": None,
        "query": "What is the labor rate cap for mechanical work under the 2026 standard labor rate guidelines?",
        "reference": "Mechanical Work is capped at $110 per hour under the 2026 standard labor rate SOP. "
                      "Aluminum Structural Repair has a separate, higher cap of $120 per hour.",
        "source": "SOP_Auto_Repair_Labor_Rates.pdf",
    },
    {
        "id": "labor-body-paint-caps",
        "claim_id": None,
        "query": "What are the capped labor rates for sheet metal repair, frame alignment, and refinishing/painting?",
        "reference": "Sheet Metal Repair is capped at $62/hr, Frame Alignment at $75/hr, and Refinishing/Painting "
                      "at $62/hr, with paint materials capped at $42 per refinish hour.",
        "source": "SOP_Auto_Repair_Labor_Rates.pdf",
    },
    {
        "id": "dui-exclusion-trigger",
        "claim_id": None,
        "query": "Under what circumstances is collision coverage voided due to a DUI?",
        "reference": "If a driver is cited for and subsequently convicted of operating the vehicle under the "
                      "influence of alcohol, narcotics, or chemical substances at the time of a collision, "
                      "Collision coverage physical damage to the insured vehicle is voided, per Section 12.2 "
                      "of the DUI Exclusion Directive.",
        "source": "DUI_Exclusion_Directive.txt",
    },
    {
        "id": "oem-parts-eligibility",
        "claim_id": None,
        "query": "What vehicles are eligible for the OEM Parts Replacement Guarantee rider?",
        "reference": "The OEM Parts Replacement Guarantee rider is only available for vehicles under 5 years old, "
                      "and must be purchased at policy inception. It guarantees brand-new, factory-original "
                      "parts and overrides the standard policy clause allowing aftermarket or LKQ parts.",
        "source": "Rider_OEM_Parts_Guarantee.pdf",
    },
    {
        "id": "custom-av-cap",
        "claim_id": None,
        "query": "What is the coverage cap for custom aftermarket stereo and navigation equipment?",
        "reference": "Under CPE Endorsement Form 402, the standard limit for custom equipment -- including non-OEM "
                      "sound systems such as aftermarket stereos and navigation -- is capped at $5,000, with "
                      "an option to increase the limit to $15,000 upon submitting invoices and photographs. "
                      "Valuation is based on Actual Cash Value (depreciated cost), not replacement cost.",
        "source": "Endorsement_Custom_Equipment_Form402.pdf",
    },
    {
        "id": "windshield-zero-deductible",
        "claim_id": None,
        "query": "What does the Zero-Deductible Glass Replacement rider cover?",
        "reference": "The rider waives the Comprehensive deductible entirely for windshield and safety glass "
                      "replacement ($0 out-of-pocket for parts, adhesive, and labor), and fully covers ADAS "
                      "camera recalibration fees. Replacement glass must meet OEM specifications.",
        "source": "Endorsement_Windshield_Zero_Deductible.docx",
    },
    {
        "id": "rental-upgrade-rider",
        "claim_id": None,
        "query": "How much does the Premium Rental Vehicle Upgrade rider increase the daily rental reimbursement?",
        "reference": "The rider increases the daily rental reimbursement cap from $30 per day to $50 per day, "
                      "limited strictly to the actual time of active repairs.",
        "source": "Endorsement_Rental_Car_Upgrade.docx",
    },
    {
        "id": "rollover-total-loss-threshold",
        "claim_id": None,
        "query": "At what point does roof damage from a rollover make a vehicle a structural total loss?",
        "reference": "If the roof has collapsed more than 2 inches, structural frame integrity is considered "
                      "lost and repair is prohibited.",
        "source": "Adjuster_Guide_Rollover_Claims.docx",
    },
    {
        "id": "fraud-chronology-red-flag",
        "claim_id": None,
        "query": "What claim filing timing is considered a fraud red flag, and how fast must it be referred to SIU?",
        "reference": "A claim filed within 10 days of policy inception or a coverage upgrade is a chronology red "
                      "flag. Suspicious claims must be referred to the Special Investigative Unit (SIU) within "
                      "3 business days of detection.",
        "source": "SOP_Claims_Fraud_Red_Flags.pdf",
    },
    {
        "id": "ca-claim-ack-timeline",
        "claim_id": None,
        "query": "Under California regulations, how quickly must an insurer acknowledge receipt of a claim?",
        "reference": "Under California Insurance Code Section 790.03, the insurer must acknowledge receipt of a "
                      "claim within 15 calendar days.",
        "source": "California_Auto_Claims_Regulations.pdf",
    },
    {
        "id": "rear-impact-adas-fee",
        "claim_id": None,
        "query": "Is ADAS recalibration required after a rear bumper replacement, and what does it cost?",
        "reference": "Any rear bumper replacement or structural alignment on a vehicle with ADAS blind-spot "
                      "sensors or backup cameras requires a mandatory electronic ADAS recalibration, "
                      "costing $250-$450.",
        "source": "Adjuster_Guide_Rear_Impact.docx",
    },
    {
        "id": "plan-a-comprehensive-deductible",
        "claim_id": None,
        "query": "What is the standard comprehensive deductible under Plan A?",
        "reference": "Plan A (Standard) Comprehensive Perils coverage has a standard deductible of $500, valued "
                      "at ACV (Actual Cash Value).",
        "source": "Auto_Deductibles_And_Limits.xlsx",
    },

    # ---- Claim-scoped queries ----
    {
        "id": "sterling-oem-eligibility",
        "claim_id": "#2026-99382",
        "query": "Is Matthew Sterling's 2023 Tesla Model Y eligible for OEM parts under his rider?",
        "reference": "Yes. The OEM Parts Replacement Guarantee rider applies to vehicles under 5 years old; "
                      "the 2023 Tesla Model Y is 3 years old in 2026, so it qualifies for brand-new, "
                      "factory-original parts.",
        "source": "Rider_OEM_Parts_Guarantee.pdf",
    },
    {
        "id": "sterling-shop-estimate-detail",
        "claim_id": "#2026-99382",
        "query": "What additional repair work did the body shop identify beyond the original estimate for Matthew Sterling's claim?",
        "reference": "Caliber Collision found the rear motor shield is cracked and needs full replacement, and "
                      "identified 5.0 hours of frame time needed to pull the rear body panel to align the "
                      "tailgate, in addition to the rear bumper cover.",
        "source": "shop_email_thread_Sterling.pdf",
    },
    {
        "id": "jenkins-repaint-vs-pdr",
        "claim_id": "#2026-10492",
        "query": "Is the door repainting charge on Sarah Jenkins' hail claim duplicative of the PDR work, or a separate legitimate repair?",
        "reference": "It is a separate, legitimate line item. The estimate includes Paintless Dent Repair for "
                      "42 dents on the hood/roof ($3,200) and conventional bodywork and repainting of the "
                      "passenger doors ($2,400) as distinct repairs within the $6,800 total, not a duplicate "
                      "charge.",
        "source": "Case_Study_Hail_Damage_PlanA.pdf",
    },
    {
        "id": "chen-custom-equipment-cap",
        "claim_id": "#2026-30291",
        "query": "Does David Chen's stolen custom equipment exceed his endorsement's coverage cap, and by how much?",
        "reference": "Yes. The receipts show $2,400 for wheels and $3,500 for the infotainment console/amplifier, "
                      "totaling $5,900. Under CPE Endorsement Form 402, custom equipment is capped at $5,000 "
                      "per occurrence, so the claimed value exceeds the cap by $900.",
        "source": "Endorsement_Custom_Equipment_Form402.pdf",
    },
    {
        "id": "chen-theft-report-detail",
        "claim_id": "#2026-30291",
        "query": "What did the police report describe about how David Chen's vehicle was broken into?",
        "reference": "The passenger window was found smashed, the dashboard infotainment screen had been "
                      "amateurishly pried out and stolen, and the exhaust system sounded abnormally loud on "
                      "startup, suggesting possible additional theft.",
        "source": "police_theft_report_Chen.pdf",
    },
    {
        "id": "rostova-hydrolock-coverage",
        "claim_id": "#2026-55912",
        "query": "Is Elena Rostova's hydro-locked engine damage a covered loss?",
        "reference": "No. Telematics show the vehicle stalled in standing water at 45 mph, after which the "
                      "ignition was pressed multiple times attempting to restart; the engine diagnostic found "
                      "water in the intake and a fractured block. Damage caused by attempting to restart an "
                      "engine stalled in water is driver-induced consequential damage and is excluded.",
        "source": "Case_Study_Engine_Hydro_Lock.pdf",
    },
    {
        "id": "rostova-dui-hallucination-probe",
        "claim_id": "#2026-55912",
        "query": "Was the driver cited for DUI in Elena Rostova's claim?",
        "reference": "There is no evidence of a DUI citation in the claim file. The available documents "
                      "(telematics log, engine diagnostic report) describe the water-related engine failure "
                      "but do not mention any DUI citation or police report establishing impairment.",
        "source": None,  # deliberately unsupported — tests whether the system fabricates an answer
        "notes": "Hallucination probe: the simulated agent's narrative asserts a DUI citation for this claim "
                 "with no backing document in the corpus. A well-grounded system should say the evidence "
                 "isn't there, not confirm the claim.",
    },
]
