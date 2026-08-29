import os
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# Create output folder
OUTPUT_DIR = "sample_guidelines"
os.makedirs(OUTPUT_DIR, exist_ok=True)

styles = getSampleStyleSheet()

# Create custom styles
title_style = ParagraphStyle(
    "DocTitle",
    parent=styles["Heading1"],
    fontName="Helvetica-Bold",
    fontSize=18,
    spaceAfter=12,
    textColor=colors.HexColor("#0f172a")
)
heading_style = ParagraphStyle(
    "DocHeading",
    parent=styles["Heading2"],
    fontName="Helvetica-Bold",
    fontSize=12,
    spaceBefore=10,
    spaceAfter=6,
    textColor=colors.HexColor("#0284c7")
)
body_style = ParagraphStyle(
    "DocBody",
    parent=styles["Normal"],
    fontName="Helvetica",
    fontSize=10,
    leading=14,
    spaceAfter=8,
    textColor=colors.HexColor("#334155")
)

def build_pdf(filename, title, sections):
    """
    Helper function to build a structured PDF with reportlab.
    sections is a list of tuples: (heading, text)
    """
    file_path = os.path.join(OUTPUT_DIR, filename)
    doc = SimpleDocTemplate(file_path, pagesize=letter, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54)
    story = []

    # Title
    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 10))
    story.append(Table([[""]], colWidths=[500], rowHeights=[2], style=TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#38bdf8")),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ])))
    story.append(Spacer(1, 15))

    # Sections
    for heading, text in sections:
        if heading:
            story.append(Paragraph(heading, heading_style))
        story.append(Paragraph(text, body_style))
        story.append(Spacer(1, 6))

    doc.build(story)
    print(f"Created PDF: {file_path}")

# ==========================================
# 1. State Regulations
# ==========================================
def gen_california_regulations():
    title = "California Auto Insurance claims Statutes & Codes"
    sections = [
        ("1. California Fair Claims Settlement Practices Regulations (Title 10, Chapter 5)",
         "Under California Insurance Code Section 790.03, insurers are required to adhere to strict timelines "
         "when handling auto claims. The insurer must acknowledge receipt of a claim within 15 calendar days. "
         "Upon receiving the claim, the insurer must provide all necessary claim forms and instructions "
         "to the claimant. Investigation of the claim must commence immediately and be concluded within 40 "
         "calendar days of receipt of proof of claim, unless written notices of delay are sent."),
        ("2. Pure Comparative Negligence Rule",
         "California follows a 'pure comparative negligence' system under California Civil Code Section 1714. "
         "A claimant can recover damages even if they are 99% at fault for an auto collision. Their financial recovery "
         "is reduced in direct proportion to their percentage of negligence. For example, if a claims handler determines "
         "that Claimant A suffered $10,000 in damages but was 40% responsible for the crash (due to speeding), Claimant A "
         "is eligible to receive a maximum payout of $6,000 (60% of the total claim value)."),
        ("3. Standard Statutes of Limitations",
         "Claims for physical damage to a motor vehicle in California must be filed within 3 years from the date "
         "of the accident. In contrast, claims involving bodily injuries resulting from an auto accident are subject "
         "to a 2-year statute of limitations from the occurrence date.")
    ]
    build_pdf("California_Auto_Claims_Regulations.pdf", title, sections)

def gen_texas_regulations():
    title = "Texas Insurance Code: Auto Claims Guidelines"
    sections = [
        ("1. Texas Prompt Payment of Claims Act (Subchapter B, Chapter 542)",
         "Texas law mandates quick claim resolutions. Insurers have 15 business days to acknowledge a claim, "
         "commence an investigation, and request all necessary documentation. Once proof of loss is received, "
         "the insurer must accept or reject the claim within 15 business days. If accepted, payment must be issued "
         "within 5 business days. If the insurer is unable to make a decision, they may request an extension of up "
         "to 45 calendar days, stating the specific reasons for the delay."),
        ("2. Proportionate Responsibility (51% Bar Rule)",
         "Texas operates under a modified comparative negligence system (Chapter 33, Civil Practice and Remedies Code). "
         "A claimant is barred from recovering damages if their percentage of responsibility is determined to be "
         "greater than 50%. If fault is 50% or less, their recovery is reduced by their percentage of fault. "
         "If Claimant A is found 51% negligent (e.g., failure to yield), they receive $0. If they are 50% negligent, "
         "they receive 50% of their total damages."),
        ("3. Personal Injury Protection (PIP) Rules",
         "Texas Insurance Code Section 1952.151 requires all insurers to offer Personal Injury Protection (PIP) "
         "with limits of at least $2,500. PIP covers medical costs, ambulance services, and 80% of lost wages "
         "regardless of fault. A policyholder must reject PIP coverage in writing; otherwise, it is automatically "
         "added to the policy.")
    ]
    build_pdf("Texas_Auto_Insurance_Code_2026.pdf", title, sections)

def gen_florida_regulations():
    title = "Florida No-Fault Motor Vehicle Law & PIP"
    sections = [
        ("1. No-Fault Insurance Framework",
         "Florida Statutes Section 627.736 governs the state's No-Fault auto insurance system. Under this system, "
         "each driver's own insurance company covers their medical bills and lost wages through Personal Injury "
         "Protection (PIP), regardless of who caused the accident. This system is designed to reduce civil lawsuits "
         "for minor auto injuries."),
        ("2. Personal Injury Protection (PIP) Deductibles & Limits",
         "The standard PIP limit in Florida is $10,000. It covers 80% of necessary medical treatments and 60% "
         "of lost wages, provided the claimant receives medical care within 14 days of the accident. If the claimant "
         "is diagnosed with an 'Emergency Medical Condition' (EMC), the full $10,000 limit is available. "
         "If no EMC is diagnosed by a medical professional, the PIP benefit is capped at $2,500."),
        ("3. Property Damage Liability (PDL)",
         "While medical bills are covered under No-Fault PIP, physical vehicle damage is NOT. Florida requires "
         "drivers to carry a minimum of $10,000 in Property Damage Liability (PDL) to pay for damage they cause "
         "to another driver's vehicle. Comparative fault rules apply to PDL claim settlements.")
    ]
    build_pdf("Florida_Auto_Insurance_Statutes.pdf", title, sections)

# ==========================================
# 2. Case Studies
# ==========================================
def gen_case_rear_end():
    title = "Claims Case Study: Rear-End Collision (ID: 2026-99382)"
    sections = [
        ("Summary of Occurrence",
         "On February 14, 2026, Policyholder A (driving a 2023 Tesla Model Y, insured under Plan B) failed to stop "
         "at a red light on El Camino Real, colliding with the rear bumper of Claimant B (driving a 2021 Toyota RAV4). "
         "The local police department arrived and cited Policyholder A for failing to maintain a safe distance. "
         "Fault was determined to be 100% on Policyholder A."),
        ("Vehicle Damage & Adjuster Appraisal",
         "Adjuster appraised the RAV4 damage at $4,850. Major repairs required: rear bumper cover replacement, "
         "trunk lid dent repair, and camera sensor re-calibration. Policyholder A's Tesla sustained $7,200 "
         "in damage (front bumper, hood, and cooling fan system)."),
        ("Claims Resolution & Deductibles",
         "Claimant B's RAV4 repairs were fully paid under Policyholder A's Property Damage Liability coverage ($4,850, "
         "no deductible applies to third-party liability claims). Policyholder A's Tesla was repaired under "
         "their own Collision coverage. Since Plan B Collision has a $500 deductible, the insurer paid $6,700 "
         "($7,200 total minus the $500 deductible) directly to the approved repair shop.")
    ]
    build_pdf("Case_Study_Rear_End_Collision.pdf", title, sections)

def gen_case_hail():
    title = "Claims Case Study: Hail Damage (ID: 2026-10492)"
    sections = [
        ("Summary of Occurrence",
         "On April 22, 2026, a severe thunderstorm occurred in Plano, Texas. The policyholder's vehicle (2024 Ford "
         "F-150, insured under Standard Plan A) was parked in an open driveway and sustained golf-ball-sized hail "
         "damage across the hood, roof, and passenger doors. The windshield was also cracked in multiple places."),
        ("Damage Appraisal",
         "Adjuster inspected the vehicle and estimated the total repair cost at $6,800. Repairs specified: "
         "Paintless Dent Repair (PDR) for 42 minor dents on the hood and roof ($3,200), conventional bodywork "
         "and repainting of the passenger doors ($2,400), and windshield replacement ($1,200)."),
        ("Deductible Application",
         "Because hail is classified as an act of nature, the claim was filed under Comprehensive coverage. "
         "Under Plan A, the standard Comprehensive deductible is $500. However, the policyholder filed a separate "
         "request to waive the deductible for the windshield repair. Since the windshield had cracks longer than 6 inches, "
         "repair was impossible, requiring a full replacement. The $500 Comprehensive deductible was applied "
         "to the overall claim, resulting in a net payout of $6,300 ($6,800 minus $500 deductible).")
    ]
    build_pdf("Case_Study_Hail_Damage_PlanA.pdf", title, sections)

def gen_case_theft():
    title = "Claims Case Study: Stolen Vehicle & Recovery (ID: 2026-30291)"
    sections = [
        ("Summary of Occurrence",
         "On May 1, 2026, the policyholder reported their 2022 Honda Civic Sport stolen from their apartment complex "
         "parking lot in Seattle, Washington. The policyholder filed a police report immediately. Seattle Police "
         "recovered the vehicle 12 days later parked in an alleyway. The vehicle was towed to a salvage yard for inspection."),
        ("Vehicle Damage Report",
         "The recovered Civic had sustained severe strip-down damage. The wheels and tires were missing, the catalytic "
         "converter had been cut out, and the dashboard was torn apart to steal the infotainment console. The total cost "
         "of repair, including body shop labor, OEM parts replacement, and structural inspections, was estimated at $9,400. "
         "The Actual Cash Value (ACV) of the vehicle prior to theft was valued at $22,000."),
        ("Claims Resolution",
         "Because the repair cost ($9,400) did not exceed the total loss threshold (which is 75% of ACV, or $16,500), "
         "the vehicle was approved for repairs under Comprehensive coverage. The policyholder had Plan B, which features "
         "a $250 Comprehensive deductible. The insurer paid $9,150 ($9,400 minus $250) to the repair facility.")
    ]
    build_pdf("Case_Study_Stolen_Honda_Civic.pdf", title, sections)

# ==========================================
# 3. Policy Endorsements
# ==========================================
def gen_custom_equip():
    title = "Endorsement Form 402: Custom Parts & Equipment (CPE)"
    sections = [
        ("1. Purpose of CPE Endorsement",
         "The standard auto insurance policy excludes coverage for aftermarket modifications, custom paint, "
         "modified suspension, or non-factory electronics unless declared. The Custom Parts and Equipment (CPE) "
         "endorsement provides physical damage coverage for items permanently installed by someone other than "
         "the original vehicle manufacturer."),
        ("2. Coverage Limits & Valuation",
         "Under CPE Endorsement Form 402, the standard limit for custom equipment is capped at $5,000, with options "
         "to increase the limit to $15,000 upon submitting invoices and photographs. Covered items include "
         "aftermarket alloy wheels, custom lift kits, performance exhaust systems, decals/wraps, and non-OEM sound "
         "systems. Valuation is based on Actual Cash Value (depreciated cost), not replacement cost."),
        ("3. Deductibles & Co-Insurance",
         "The deductible for CPE claims is identical to the primary Collision or Comprehensive coverage deductible. "
         "No coverage is provided for racing tires, radar detectors, or temporary items not permanently attached "
         "to the vehicle framework.")
    ]
    build_pdf("Endorsement_Custom_Equipment_Form402.pdf", title, sections)

def gen_rideshare_endorsement():
    title = "Endorsement Form 702: Rideshare Coverage (TNC Rider)"
    sections = [
        ("1. The Rideshare Gap Problem",
         "Standard personal auto policies exclude coverage for any period when a vehicle is used for hire or transport. "
         "Transportation Network Companies (TNCs, like Uber or Lyft) provide commercial insurance, but there are "
         "significant coverage gaps during specific phases of driving. The Rideshare Endorsement (TNC Rider) bridges "
         "these gaps to ensure continuous coverage."),
        ("2. Coverage Phase Breakdown",
         "Coverage varies by the driver's active TNC application status:\n"
         "- Phase 1 (App Open, Waiting for Match): The TNC commercial policy only provides low liability limits. "
         "The Rideshare Endorsement extends the driver's personal Collision and Comprehensive coverages to Phase 1.\n"
         "- Phase 2 (Match Accepted, En Route): Commercial TNC coverage is active, but carries a high deductible "
         "(e.g., Uber's $2,500 deductible). The endorsement provides 'deductible buy-down' coverage, reducing the "
         "effective deductible to the driver's personal policy limit (e.g. $500).\n"
         "- Phase 3 (Passenger in Vehicle): TNC commercial coverage is fully primary. Personal insurance does not apply."),
        ("3. Exclusion Notice",
         "No coverage is provided under this rider for commercial cargo transport, food delivery apps (e.g. DoorDash, "
         "UberEats), or traditional taxi services unless specifically endorsed.")
    ]
    build_pdf("Endorsement_Rideshare_TNC_Form702.pdf", title, sections)

def gen_classic_car():
    title = "Endorsement Form 805: Classic Car Agreed Value Clause"
    sections = [
        ("1. Definition of Classic Vehicle",
         "To qualify under Endorsement Form 805, the vehicle must be at least 20 years old, classified as a collector "
         "car, and maintained in excellent, unmodified condition. The vehicle must be stored in a fully enclosed, "
         "locked garage and cannot be used for daily commuting."),
        ("2. Agreed Value Settlement",
         "Unlike standard auto policies that settle claims based on Actual Cash Value (ACV, which factors in "
         "depreciation), classic car policies use an Agreed Value clause. The insurer and policyholder agree on "
         "the vehicle's value at policy inception based on a professional appraisal. In the event of a total loss, "
         "the insurer pays the full Agreed Value ($50,000, for example) with no deduction for physical depreciation."),
        ("3. Mileage Restrictions",
         "Coverage is subject to an annual mileage limitation of 2,500 miles. Odometer verification is required "
         "at each policy renewal. Exceeding this limit without written pre-approval voids the Agreed Value status.")
    ]
    build_pdf("Endorsement_Classic_Car_Agreed_Value.pdf", title, sections)

# ==========================================
# 4. Standard Operating Procedures (SOPs)
# ==========================================
def gen_sop_total_loss():
    title = "SOP: Total Loss Vehicle Valuation & Title Branding"
    sections = [
        ("1. Total Loss Threshold Rule",
         "A vehicle is declared a total loss when the estimated cost of repairs plus the salvage value of the vehicle "
         "equals or exceeds the Actual Cash Value (ACV). Most states enforce a statutory total loss threshold. "
         "In California, the Total Loss Formula (TLF) is used (Cost of Repair + Salvage Value >= ACV). "
         "In Texas, a flat threshold of 100% of ACV is applied. In Florida, the threshold is 80% of ACV."),
        ("2. Valuation Methodology",
         "Claims handlers must determine ACV using a minimum of two independent valuation sources. Acceptable sources: "
         "CCC ONE Market Valuation Reports (primary), NADA Guides (Kelley Blue Book is secondary and only used for "
         "historical references), and local dealer market surveys. The valuation must adjust for mileage, pre-existing "
         "body dents, tire tread depth, and trim packages."),
        ("3. Salvage & Title Branding",
         "Once totaled, the vehicle title must be surrendered to the state Department of Motor Vehicles (DMV) for "
         "re-branding. The title will be branded as 'Salvage' if the vehicle is repairable, or 'Junk / Destruction' "
         "if it cannot be safely rebuilt. If the policyholder elects to retain the salvage vehicle, the salvage "
         "value estimate is deducted from their final settlement payout.")
    ]
    build_pdf("SOP_Total_Loss_Valuation.pdf", title, sections)

def gen_sop_labor_rates():
    title = "SOP: Labor Rates & Repair Shop Guidelines"
    sections = [
        ("1. Standard Repair Labor Rates",
         "Body shop labor claims are subject to regional maximum limits. Adjusters must verify that body shop "
         "estimates adhere to the following capped hourly labor rates for the 2026 calendar year:\n"
         "- Sheet Metal Repair: $62 per hour\n"
         "- Frame Alignment: $75 per hour\n"
         "- Refinishing / Painting: $62 per hour\n"
         "- Mechanical Work: $110 per hour\n"
         "- Aluminum Structural Repair: $120 per hour"),
        ("2. Paint & Material Calculations",
         "Paint materials are capped at $42 per refinish hour. Paint supplies must be detailed on the invoice. "
         "Any invoice requesting flat rates for paint materials without hourly breakdowns must be rejected."),
        ("3. Aftermarket vs. OEM Parts Policy",
         "For vehicles under 3 years old or with less than 36,000 miles, Original Equipment Manufacturer (OEM) parts "
         "must be approved for all safety-related components (suspension, steering, airbags, and collision sensors). "
         "For vehicles older than 3 years, Like Kind and Quality (LKQ) recycled parts or certified aftermarket parts "
         "must be utilized to minimize claims severity.")
    ]
    build_pdf("SOP_Auto_Repair_Labor_Rates.pdf", title, sections)

def gen_sop_fraud():
    title = "SOP: Fraud Detection & Red Flags for Claims Handlers"
    sections = [
        ("1. The Importance of Fraud Detection",
         "Insurance fraud increases premium costs for all policyholders. Claims handlers serve as the primary "
         "line of defense against fraudulent submissions. Suspicious claims must be flagged and referred to "
         "the Special Investigative Unit (SIU) within 3 business days of detection."),
        ("2. Red Flags Checklist",
         "Handlers must screen claims for the following suspicious indicators:\n"
         "- Chronology: A claim is filed within 10 days of policy inception or policy coverage upgrade.\n"
         "- Paper Accidents: Single-vehicle collisions occurring late at night in remote areas with no police report "
         "or independent witnesses.\n"
         "- Pre-existing Damage: Invoices requesting repairs for rust, corrosion, or old dents that do not match the "
         "direction of force in the reported accident.\n"
         "- Duplicate Invoices: Submission of identical photo angles or medical treatment dates for two different claims."),
        ("3. SIU Referral Procedure",
         "When referring a claim to the SIU, do not inform the policyholder or claimant. Compile all documentation, "
         "including photo metadata, police report details, and telematics history (if available). The claims handler "
         "must continue processing non-disputed elements of the claim unless instructed otherwise by the SIU investigator.")
    ]
    build_pdf("SOP_Claims_Fraud_Red_Flags.pdf", title, sections)

# ==========================================
# 5. Policy Riders
# ==========================================
def gen_rider_roadside():
    title = "Policy Rider: Roadside Assistance & Towing Plus"
    sections = [
        ("1. Covered Roadside Emergencies",
         "The Roadside Assistance Plus rider provides 24/7 service for emergency situations. Covered events "
         "include: towing (following mechanical breakdown, not accidents), battery jumpstarts, flat tire replacement "
         "(installing the policyholder's spare), lockout assistance (vehicle entry only, no key replacement), and "
         "emergency fuel delivery (up to 2 gallons of gasoline)."),
        ("2. Distance Limits & Towing Caps",
         "Under standard policies, roadside towing is capped at 15 miles or a maximum of $75 per event. The "
         "policyholder is responsible for paying the tow operator directly for any mileage exceeding this limit. "
         "Under Premium Roadside riders, towing is covered up to 100 miles to the nearest qualified repair facility "
         "with zero out-of-pocket costs."),
        ("3. Exclusion of Accidental Towing",
         "Roadside assistance towing does NOT apply to vehicles damaged in collisions. Towing charges resulting from "
         "an accident are covered under the Collision coverage section of the primary policy, subject to the "
         "applicable collision deductible.")
    ]
    build_pdf("Rider_Roadside_Assistance_Plus.pdf", title, sections)

def gen_rider_gap():
    title = "Policy Rider: Gap Insurance Coverage"
    sections = [
        ("1. Purpose of Gap Coverage",
         "Vehicles depreciate rapidly upon purchase. If a vehicle is declared a total loss, the primary insurance "
         "settlement only pays the Actual Cash Value (ACV). If the outstanding balance on the car loan or lease "
         "exceeds the ACV, the policyholder is left with a financial 'gap'. Gap Insurance covers this difference."),
        ("2. Coverage Limits & Payments",
         "Gap coverage pays the difference between the primary insurance payout (ACV) and the unpaid principal "
         "balance on the loan or lease. For example, if a totaled car has an ACV of $18,000 but the loan balance "
         "is $23,000, Gap insurance pays the remaining $5,000 directly to the financial institution. "
         "It does not cover delinquent payments, late fees, extended warranties, or carry-over balances from previous loans."),
        ("3. Claim Requirements",
         "The claims handler must obtain a copy of the original finance contract, a certified loan payoff statement "
         "indicating the balance on the date of the loss, and the primary insurer's total loss settlement worksheet.")
    ]
    build_pdf("Rider_Gap_Insurance_Coverage.pdf", title, sections)

def gen_rider_oem():
    title = "Policy Rider: OEM Parts Replacement Guarantee"
    sections = [
        ("1. Guarantee Scope",
         "The Original Equipment Manufacturer (OEM) Parts Rider guarantees that only brand-new, factory-original "
         "parts manufactured by the vehicle's maker will be used in repairing physical damage. This rider overrides "
         "the standard policy clause that allows adjusters to specify aftermarket or LKQ recycled parts."),
        ("2. Eligibility & Term Limits",
         "This rider must be purchased at policy inception and is only available for vehicles under 5 years old. "
         "The guarantee applies to all body panels, structural frame elements, steering mechanisms, suspension assemblies, "
         "and active safety electronics (sensors, lidar, cameras)."),
        ("3. Claims Adjustment Adjustments",
         "When reviewing repair estimates under this rider, the adjuster is prohibited from writing sheet-metal or cosmetic "
         "estimates using aftermarket brands. If an OEM part is temporarily out of stock or backordered, the insurer "
         "will cover rental car reimbursement for up to an additional 15 days beyond the standard policy limit.")
    ]
    build_pdf("Rider_OEM_Parts_Guarantee.pdf", title, sections)

if __name__ == "__main__":
    print("Generating 12 realistic auto insurance PDF documents...")
    gen_california_regulations()
    gen_texas_regulations()
    gen_florida_regulations()
    gen_case_rear_end()
    gen_case_hail()
    gen_case_theft()
    gen_custom_equip()
    gen_rideshare_endorsement()
    gen_classic_car()
    gen_sop_total_loss()
    gen_sop_labor_rates()
    gen_sop_fraud()
    gen_rider_roadside()
    gen_rider_gap()
    gen_rider_oem()
    print("PDF Generation complete!")
