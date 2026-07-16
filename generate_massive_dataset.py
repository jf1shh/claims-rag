import os
import docx
import pandas as pd
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# Create output folder
OUTPUT_DIR = "sample_guidelines"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Setup PDF Styles
styles = getSampleStyleSheet()
title_style = ParagraphStyle(
    "DocTitle", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=18, spaceAfter=12, textColor=colors.HexColor("#0f172a")
)
heading_style = ParagraphStyle(
    "DocHeading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12, spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#0284c7")
)
body_style = ParagraphStyle(
    "DocBody", parent=styles["Normal"], fontName="Helvetica", fontSize=10, leading=14, spaceAfter=8, textColor=colors.HexColor("#334155")
)

def build_pdf(filename, title, sections):
    file_path = os.path.join(OUTPUT_DIR, filename)
    doc = SimpleDocTemplate(file_path, pagesize=letter, leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54)
    story = []
    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 10))
    story.append(Table([[""]], colWidths=[500], rowHeights=[2], style=TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#38bdf8")),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ])))
    story.append(Spacer(1, 15))
    for heading, text in sections:
        if heading:
            story.append(Paragraph(heading, heading_style))
        story.append(Paragraph(text, body_style))
        story.append(Spacer(1, 6))
    doc.build(story)
    print(f"Created PDF: {file_path}")

def build_docx(filename, title, sections):
    file_path = os.path.join(OUTPUT_DIR, filename)
    doc = docx.Document()
    doc.add_heading(title, level=1)
    for heading, text in sections:
        if heading:
            doc.add_heading(heading, level=2)
        doc.add_paragraph(text)
    doc.save(file_path)
    print(f"Created DOCX: {file_path}")

def build_xlsx(filename, data):
    file_path = os.path.join(OUTPUT_DIR, filename)
    df = pd.DataFrame(data)
    df.to_excel(file_path, index=False)
    print(f"Created XLSX: {file_path}")

def build_txt(filename, content):
    file_path = os.path.join(OUTPUT_DIR, filename)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content.strip())
    print(f"Created TXT: {file_path}")

# =========================================================================
# GENERATORS
# =========================================================================

def generate_docx_files():
    # 1. NY Auto Contract
    build_docx(
        "Auto_Policy_Contract_NewYork.docx",
        "New York Personal Auto Insurance Policy Contract",
        [
            ("Section 1: General Liability Coverages",
             "This policy provides Bodily Injury Liability limits of 25/50 ($25,000 per person / $50,000 per accident) "
             "and Property Damage Liability limits of $10,000, representing the statutory minimums in New York State. "
             "The policyholder is protected against third-party lawsuits arising from accidental collisions within the state."),
            ("Section 2: New York No-Fault PIP Benefits",
             "New York is a No-Fault state. This means the policy provides Personal Injury Protection (PIP) limits of up to "
             "$50,000 per person to cover necessary medical treatments, rehabilitation, and 80% of lost wages (capped at $2,000/month) "
             "regardless of fault. Serious Injury Threshold rules apply before a passenger can sue an at-fault third party.")
        ]
    )
    # 2. CA Auto Contract
    build_docx(
        "Auto_Policy_Contract_California.docx",
        "California Standard Vehicle Coverage Contract",
        [
            ("Section 1: Liability Limits (15/30/5)",
             "The standard California auto policy features liability limits of $15,000 for bodily injury to one person, "
             "$30,000 for bodily injury to multiple people in one accident, and $5,000 for property damage. "
             "These limits apply to all standard plans unless higher limits are requested and paid for."),
            ("Section 2: Collision Damage Terms",
             "Collision coverage pays for physical damage to the insured vehicle caused by collision with another car "
             "or object. Settlements are determined by Actual Cash Value (ACV) minus the deductible ($500 or $1,000 standard).")
        ]
    )
    # 3. TX Auto Contract
    build_docx(
        "Auto_Policy_Contract_Texas.docx",
        "Texas Personal Automobile Policy Provisions",
        [
            ("Section 1: Bodily Injury and Property Damage Limits",
             "Texas state law requires minimum liability coverage limits of 30/60/25: $30,000 for bodily injury per person, "
             "$60,000 for bodily injury per accident, and $25,000 for property damage. The insurer will pay up to these limits "
             "to settle third-party claims caused by the insured vehicle."),
            ("Section 2: Uninsured/Underinsured Motorist (UM/UIM)",
             "UM/UIM coverage protects the policyholder if they are hit by an uninsured driver or a driver whose liability limits "
             "are insufficient to cover the damages. A standard $250 deductible applies to property damage UM/UIM claims in Texas.")
        ]
    )
    # 4. Claims Field Guide
    build_docx(
        "Claim_Investigator_Field_Guide.docx",
        "Auto Insurance claims: Field Investigator Guide",
        [
            ("1. At-Scene Inspections",
             "When arriving at a claim inspection scene, the investigator must document skid marks, traffic signals, "
             "obstructions (e.g. overgrown trees), and road conditions. Note weather details on the report."),
            ("2. Vehicle Damage Alignment",
             "Inspect impact points to verify consistency. Verify if the bumper heights of both vehicles align "
             "with the crash description. Check for pre-existing rust on the metal framework which indicates historical damage.")
        ]
    )
    # 5. Rear Impact Guide
    build_docx(
        "Adjuster_Guide_Rear_Impact.docx",
        "Adjuster Reference Guide: Rear-End Collision Damage",
        [
            ("1. Rear bumper Structure",
             "Modern rear bumper assemblies contain plastic bumper covers, Styrofoam energy absorbers, steel reinforcement bars, "
             "and bracket mounts. If the absorber is compressed, it must be replaced. Do not attempt to repair safety-critical reinforcement bars."),
            ("2. Sensor Calibration",
             "Vehicles with Advanced Driver Assistance Systems (ADAS) contain blind-spot sensors and backup cameras inside "
             "the rear bumper. Any bumper replacement or structural alignment requires a mandatory electronic ADAS recalibration ($250-$450 fee).")
        ]
    )
    # 6. Front Impact Guide
    build_docx(
        "Adjuster_Guide_Front_Impact.docx",
        "Adjuster Reference Guide: Front-End Collision Damage",
        [
            ("1. Airbag deployment and Safety Systems",
             "If the driver steering wheel or passenger dashboard airbags deploy, the vehicle's Supplemental Restraint System (SRS) "
             "control module must be replaced. Replace all seatbelt pre-tensioners that were locked during the collision."),
            ("2. Cooling System Inspections",
             "Inspect the radiator, A/C condenser, and radiator core support framework. Frontal impact often pushes these components "
             "into the engine block. Verify engine block integrity before authorizing radiator repairs to avoid hidden mechanical issues.")
        ]
    )
    # 7. Side Impact Guide
    build_docx(
        "Adjuster_Guide_Side_Impact.docx",
        "Adjuster Reference Guide: Side-Impact & T-Bone Damage",
        [
            ("1. Side Pillars and Structure",
             "Side-impact collisions often compromise the vehicle's structural pillars (A-pillar, B-pillar, C-pillar). "
             "If the B-pillar is bent or cracked, the vehicle structural framework is compromised, often resulting in a total loss."),
            ("2. Side-Curtain Airbags",
             "Side-impacts frequently trigger side-curtain and seat-mounted side airbags. Replacement requires removing "
             "the headliner and replacing the complete airbag inflator assembly.")
        ]
    )
    # 8. Rollover Claims Guide
    build_docx(
        "Adjuster_Guide_Rollover_Claims.docx",
        "Adjuster Reference Guide: Vehicle Rollover Claims",
        [
            ("1. Roof Crush and A-Pillar Damage",
             "Rollover accidents are severe and regularly result in total losses due to structural roof deformation. "
             "If the roof has collapsed more than 2 inches, structural frame integrity is lost, and repair is prohibited."),
            ("2. Fluid Seepage and Engine Damage",
             "When a vehicle rolls over, engine oil and coolant seep into the combustion chambers and intake manifold. "
             "Adjusters must request a mandatory engine compression test before estimating mechanical repairs to verify there is no hydro-lock damage.")
        ]
    )
    # 9. BI Evaluation SOP
    build_docx(
        "SOP_Bodily_Injury_Evaluation.docx",
        "SOP: Bodily Injury Claim Assessment & Special Damages",
        [
            ("1. Chiropractic and Physical Therapy Caps",
             "Under standard guidelines, soft-tissue injuries (whiplash, sprains) are capped at a maximum of 12 chiropractic "
             "or physical therapy visits unless pre-approved by a medical review board. Total medical bills for soft tissue must not exceed $3,500."),
            ("2. General Damages (Pain & Suffering)",
             "Calculate pain and suffering damages using the multiplier method. For soft tissue, use a multiplier of 1.5x to 2.0x "
             "of the total special damages (medical bills). For severe fractures or surgeries, use a multiplier of 3.0x to 5.0x.")
        ]
    )
    # 10. Diminished Value SOP
    build_docx(
        "SOP_Diminished_Value_Claims.docx",
        "SOP: Diminished Value Assessment using Formula 17c",
        [
            ("1. Eligibility",
             "A policyholder is eligible for a diminished value claim if their vehicle sustained significant collision damage, "
             "was professionally repaired, is under 6 years old, and has a clean title brand. Third-party claims only."),
            ("2. Formula 17c Calculation",
             "Start with 10% of the vehicle's NADA clean retail value (base loss). Apply a damage multiplier based on severity: "
             "1.00 for severe structural, 0.75 for major, 0.50 for moderate, 0.25 for minor, 0.00 for cosmetic. "
             "Apply a mileage multiplier: 1.00 (0-19k miles), 0.80 (20k-39k), 0.60 (40k-59k), 0.40 (60k-79k), 0.20 (80k-99k), 0.00 (>=100k).")
        ]
    )
    # 11. Subrogation SOP
    build_docx(
        "SOP_Subrogation_Recovery.docx",
        "SOP: Subrogation Recovery Procedures",
        [
            ("1. Identifying Subrogation Opportunities",
             "When the insurer pays a claim where the third party was at fault, the claims handler must immediately "
             "flag the file for subrogation. Create a subrogation file containing police reports, witness statements, and repair invoices."),
            ("2. Inter-Company Arbitration",
             "If the third-party insurer disputes liability, file the claim with Arbitration Forums, Inc. "
             "The arbitrator's decision is binding on both insurance carriers. Do not pursue the policyholder for the deductible during this phase.")
        ]
    )
    # 12. Rental Upgrade Endorsement
    build_docx(
        "Endorsement_Rental_Car_Upgrade.docx",
        "Endorsement: Premium Rental Vehicle Upgrade Rider",
        [
            ("1. Rider Benefit",
             "This endorsement upgrades the standard daily rental reimbursement cap. The daily reimbursement limit "
             "is increased from $30 per day to $50 per day, allowing the policyholder to lease a mid-size SUV or full-size sedan "
             "while their primary vehicle is in the repair shop."),
            ("2. Period of Repair rule",
             "Rental reimbursement is strictly limited to the actual time of active repairs. Delays caused by the repair facility "
             "or backordered parts do not extend the standard 30-day limit unless pre-approved by the claims manager.")
        ]
    )
    # 13. Glass Zero Deductible Endorsement
    build_docx(
        "Endorsement_Windshield_Zero_Deductible.docx",
        "Endorsement: Zero-Deductible Glass Replacement Rider",
        [
            ("1. Deductible Waiver",
             "By purchasing this rider, the policyholder's Comprehensive deductible is waived for all windshield and safety glass "
             "replacements. The insurer will pay the full cost of glass parts, adhesive materials, and labor with $0 out-of-pocket cost."),
            ("2. Recalibration coverage",
             "This rider fully covers ADAS camera recalibration fees associated with the windshield replacement. "
             "Replacement glass must meet OEM specifications to ensure camera alignment.")
        ]
    )
    # 14. Custom Audio Endorsement
    build_docx(
        "Endorsement_Custom_Audio_Visual.docx",
        "Endorsement: Custom Sound and Navigation Equipment coverage",
        [
            ("1. Coverage Limits",
             "This rider provides physical damage coverage for aftermarket stereos, amplifiers, subwoofers, and custom screens "
             "not installed by the factory. Coverage is capped at a maximum of $3,500 per occurrence."),
            ("2. Proof of Purchase Requirement",
             "Claims handlers must request original receipts, serial numbers, and photos of the custom equipment "
             "prior to settling claims under this endorsement. Depreciation is calculated at 10% per year from the date of install.")
        ]
    )
    # 15. Roadside Premium Endorsement
    build_docx(
        "Endorsement_Roadside_Emergency_Premium.docx",
        "Endorsement: Premium Roadside Towing & Emergency Assistance",
        [
            ("1. Expanded Towing Limits",
             "Under the Premium Roadside endorsement, the standard towing limit is extended from 15 miles to 100 miles "
             "per event. The vehicle will be towed to any repair shop or dealership of the policyholder's choice at no additional charge."),
            ("2. Trip Interruption Benefits",
             "If the vehicle breaks down more than 100 miles from the policyholder's home, this rider reimburses up to "
             "$150 per day for a maximum of 3 days ($450 limit) for lodging, meals, and alternative transportation costs.")
        ]
    )


def generate_xlsx_files():
    # 1. Deductibles Matrix
    build_xlsx(
        "Auto_Deductibles_And_Limits.xlsx",
        {
            "Policy Plan": ["Plan A (Standard)", "Plan A (Standard)", "Plan B (Premium)", "Plan B (Premium)", "Plan C (Basic)"],
            "Coverage Category": ["Collision Damage", "Comprehensive Perils", "Collision Damage", "Comprehensive Perils", "Collision Only"],
            "Coverage Limit": ["ACV", "ACV", "ACV", "ACV", "ACV"],
            "Standard Deductible ($)": [1000, 500, 500, 250, 2000],
            "Towing & Roadside Assistance": ["Capped at $75/event", "Capped at $75/event", "Full Coverage (Unlimited)", "Full Coverage (Unlimited)", "Not Covered"],
            "Rental Reimbursement ($/Day)": [30, 30, 50, 50, 0]
        }
    )
    # 2. Labor Rates
    build_xlsx(
        "Regional_Labor_Rates_2026.xlsx",
        {
            "Region": ["West Coast", "East Coast", "South", "Midwest", "Mountain"],
            "Sheet Metal ($/Hr)": [75, 70, 60, 58, 62],
            "Frame Alignment ($/Hr)": [85, 80, 72, 70, 75],
            "Paint & Refinish ($/Hr)": [75, 70, 60, 58, 62],
            "Mechanical ($/Hr)": [120, 115, 100, 95, 105],
            "Paint Supplies ($/Hr)": [45, 42, 38, 36, 40]
        }
    )
    # 3. Vehicle Depreciation
    build_xlsx(
        "Vehicle_Depreciation_Rates.xlsx",
        {
            "Vehicle Component": ["Tires", "Battery", "Brake Rotors", "Muffler / Exhaust", "Steering / Suspension", "Engine / Transmission"],
            "Annual Depreciation (%)": [20, 25, 30, 15, 10, 5],
            "Maximum Limit (%)": [80, 90, 80, 75, 50, 40],
            "Valuation Basis": ["Tread Depth", "Age (Months)", "Thickness", "Age / Condition", "Mileage", "Mileage"]
        }
    )
    # 4. OEM vs Aftermarket Pricing
    build_xlsx(
        "OEM_vs_Aftermarket_Price_Index.xlsx",
        {
            "Car Make/Model": ["Toyota Camry", "Honda Civic", "Ford F-150", "Tesla Model 3", "Chevrolet Silverado"],
            "Component": ["Front Bumper", "Headlight Assy", "Hood Panel", "Fender Panel", "Grille Assembly"],
            "OEM Retail Price ($)": [450, 620, 550, 850, 380],
            "Aftermarket Price ($)": [220, 310, 280, 410, 190],
            "Avg Savings ($)": [230, 310, 270, 440, 190],
            "Approved for Safety Claims": ["Yes", "No (Sensor issue)", "Yes", "No (Fit issue)", "Yes"]
        }
    )
    # 5. Network Shops West
    build_xlsx(
        "Approved_Network_Body_Shops_West.xlsx",
        {
            "Shop Name": ["Caliber Collision", "Service King", "Gerber Collision", "Apex Auto Body", "Elite Fleet Repair"],
            "City / State": ["Los Angeles, CA", "Seattle, WA", "Portland, OR", "San Francisco, CA", "Las Vegas, NV"],
            "Certifications": ["I-CAR Gold, ASE", "I-CAR Gold", "I-CAR Regular, ASE", "I-CAR Gold, OEM Tesla", "ASE, Frame Certified"],
            "Agreed Labor Discount": ["10%", "8%", "10%", "5%", "12%"],
            "Preferred Status": ["Yes", "Yes", "No", "Yes", "No"]
        }
    )
    # 6. Network Shops South
    build_xlsx(
        "Approved_Network_Body_Shops_South.xlsx",
        {
            "Shop Name": ["Maaco Collision", "Car West Auto", "Crash Champions", "Southside Collision", "Classic Auto Restoration"],
            "City / State": ["Houston, TX", "Miami, FL", "Atlanta, GA", "Dallas, TX", "Orlando, FL"],
            "Certifications": ["ASE", "I-CAR Gold", "I-CAR Gold, ASE", "ASE, Frame Certified", "I-CAR Regular"],
            "Agreed Labor Discount": ["15%", "10%", "10%", "8%", "5%"],
            "Preferred Status": ["No", "Yes", "Yes", "Yes", "No"]
        }
    )
    # 7. Network Shops Midwest
    build_xlsx(
        "Approved_Network_Body_Shops_Midwest.xlsx",
        {
            "Shop Name": ["Midwest Collision Center", "Precision Refinishing", "Great Lakes Auto Body", "Metro Collision", "Columbus Frame Shop"],
            "City / State": ["Chicago, IL", "Cleveland, OH", "Detroit, MI", "Indianapolis, IN", "Columbus, OH"],
            "Certifications": ["I-CAR Gold, ASE", "ASE", "I-CAR Regular, ASE", "I-CAR Gold", "Frame Certified, ASE"],
            "Agreed Labor Discount": ["12%", "10%", "15%", "10%", "8%"],
            "Preferred Status": ["Yes", "No", "Yes", "Yes", "Yes"]
        }
    )
    # 8. PIP Fee Schedule CA
    build_xlsx(
        "PIP_Medical_Fee_Schedule_CA.xlsx",
        {
            "Medical Procedure": ["Chiropractic Adjust", "Physical Therapy (15m)", "MRI (Lumbosacral)", "Emergency Room Visit", "X-Ray (Chest)"],
            "CPT Code": [98940, 97110, 72148, 99283, 71045],
            "Medicare Standard Rate ($)": [42.50, 31.20, 220.00, 185.00, 52.00],
            "Allowable Policy Cap ($)": [85.00, 62.40, 440.00, 370.00, 104.00],
            "Requires Pre-Auth": ["No", "Yes (if > 10)", "Yes", "No", "No"]
        }
    )
    # 9. PIP Fee Schedule TX
    build_xlsx(
        "PIP_Medical_Fee_Schedule_TX.xlsx",
        {
            "Medical Procedure": ["Chiropractic Adjust", "Physical Therapy (15m)", "MRI (Lumbosacral)", "Emergency Room Visit", "X-Ray (Chest)"],
            "CPT Code": [98940, 97110, 72148, 99283, 71045],
            "Medicare Standard Rate ($)": [38.00, 28.50, 195.00, 160.00, 48.00],
            "Allowable Policy Cap ($)": [76.00, 57.00, 390.00, 320.00, 96.00],
            "Requires Pre-Auth": ["No", "Yes (if > 10)", "Yes", "No", "No"]
        }
    )
    # 10. PIP Fee Schedule FL
    build_xlsx(
        "PIP_Medical_Fee_Schedule_FL.xlsx",
        {
            "Medical Procedure": ["Chiropractic Adjust", "Physical Therapy (15m)", "MRI (Lumbosacral)", "Emergency Room Visit", "X-Ray (Chest)"],
            "CPT Code": [98940, 97110, 72148, 99283, 71045],
            "Medicare Standard Rate ($)": [35.00, 25.00, 180.00, 150.00, 45.00],
            "Allowable Policy Cap ($)": [70.00, 50.00, 360.00, 300.00, 90.00],
            "Requires Pre-Auth": ["No", "Yes (if > 10)", "Yes", "No", "No"]
        }
    )


def generate_pdf_files():
    # 1. California Regulations
    gen_california_regulations()
    # 2. Texas Regulations
    gen_texas_regulations()
    # 3. Florida Regulations
    gen_florida_regulations()
    
    # 4. New York Regulations
    build_pdf(
        "New_York_Auto_Insurance_Statutes.pdf",
        "New York Auto claims No-Fault Insurance Statutes",
        [
            ("1. Article 51 of the New York Insurance Law",
             "New York operates under a comprehensive No-Fault auto insurance system. The law requires vehicle owners "
             "to carry Personal Injury Protection (PIP) limits of at least $50,000. PIP covers medical costs, psychiatric care, "
             "and lost income resulting from motor vehicle accidents, regardless of fault."),
            ("2. Serious Injury Threshold (Section 5102(d))",
             "A claimant cannot sue an at-fault driver for non-economic loss (pain and suffering) in New York unless they meet "
             "the legal definition of a 'serious injury'. Serious injuries include: death, dismemberment, significant disfigurement, "
             "fractures, loss of a fetus, permanent loss of use of a body organ, or a non-permanent injury that prevents "
             "performing daily tasks for at least 90 days out of the 180 days immediately following the accident."),
            ("3. Basic Economic Loss limits",
             "Basic economic loss is capped at $50,000 per person. Claims exceeding this limit may be filed against the at-fault "
             "party's liability insurance if the serious injury threshold is satisfied.")
        ]
    )
    # 5. Case Rear Collision
    gen_case_rear_end()
    # 6. Case Hail
    gen_case_hail()
    # 7. Case Theft
    gen_case_theft()
    
    # 8. Case Pedestrian
    build_pdf(
        "Case_Study_Pedestrian_Accident.pdf",
        "Claims Case Study: Pedestrian Accident (ID: 2026-44021)",
        [
            ("Summary of Occurrence",
             "On March 3, 2026, the policyholder (driving a 2022 Chevrolet Silverado) struck a pedestrian who was crossing "
             "the street at a designated crosswalk in downtown Orlando, Florida. Local police cited the policyholder for "
             "failure to yield right-of-way. Liability was determined to be 100% on the policyholder."),
            ("Injury Analysis & PIP Claims",
             "The pedestrian sustained a fractured tibia and severe road rash. Medical bills totaled $14,500. "
             "Because the accident occurred in Florida (No-Fault state), and the pedestrian did not own a vehicle, the "
             "pedestrian's medical costs were initially processed under the policyholder's PIP coverage ($10,000 limit, EMC diagnosed). "
             "The remaining $4,500 was paid under the policyholder's Bodily Injury Liability coverage."),
            ("Third-Party Liability Settlement",
             "The pedestrian retained counsel and claimed serious injury (fractured tibia). The insurer settled the liability claim "
             "for an additional $22,000 for pain and suffering under the policyholder's Bodily Injury coverage. Total insurer payout: $32,000.")
        ]
    )
    # 9. Case Engine Flood
    build_pdf(
        "Case_Study_Engine_Hydro_Lock.pdf",
        "Claims Case Study: Engine Hydro-Lock Damage (ID: 2026-55912)",
        [
            ("Summary of Occurrence",
             "On June 1, 2026, the policyholder (driving a 2020 BMW 330i) drove through a flooded intersection during heavy rain "
             "in Miami, Florida. The engine stalled in the middle of the standing water and the vehicle was towed to a mechanic shop."),
            ("Mechanical Diagnosis",
             "The technician inspected the engine and confirmed it was hydro-locked. Water had entered the engine air intake, "
             "filling the cylinders. When the pistons attempted to compress the water, the connecting rods bent, destroying the engine block. "
             "The cost to replace the engine with a remanufactured unit was estimated at $12,500."),
            ("Coverage and Exclusion Ruling",
             "The policyholder filed a Collision claim. The claim was denied under Collision but approved under Comprehensive coverage, "
             "as driving through standing water is classified as a flood peril. Since the policyholder had a Plan A Comprehensive deductible "
             "of $500, the insurer paid $12,000 ($12,500 minus $500). Adjuster noted that if the policyholder had restarted the engine "
             "repeatedly after stalling, the additional damage would have been excluded under the mitigation clause.")
        ]
    )
    # 10. Case UIM Claim
    build_pdf(
        "Case_Study_Underinsured_Motorist.pdf",
        "Claims Case Study: Underinsured Motorist Resolution (ID: 2026-66381)",
        [
            ("Summary of Occurrence",
             "On January 10, 2026, the policyholder (driving a 2021 Subaru Outback) was struck head-on by a third-party driver "
             "in Austin, Texas. The third party was 100% at fault. The third party carried minimum Texas liability limits of 30/60/25."),
            ("Injury and Cost Breakdown",
             "The policyholder sustained severe neck injuries and required cervical fusion surgery. Total medical bills were $78,000. "
             "The third party's insurer paid their full bodily injury limit of $30,000, exhausting their liability policy. "
             "This left an unpaid medical balance of $48,000."),
            ("UIM Claim Activation",
             "The policyholder filed an Underinsured Motorist Bodily Injury (UIMBI) claim under their own policy (Premium Plan B, "
             "featuring UIMBI limits of $100,000 per person). The insurer reviewed the case and approved a payout of $48,000 to "
             "cover the remaining medical bills, plus $15,000 for pain and suffering. The insurer then closed the claim and filed a "
             "subrogation request against the third party's personal assets.")
        ]
    )
    # 11. SOP Total Loss
    gen_sop_total_loss()
    # 12. SOP Labor Rates
    gen_sop_labor_rates()
    # 13. SOP Fraud
    gen_sop_fraud()
    # 14. Rider Roadside
    gen_rider_roadside()
    # 15. Rider Gap
    gen_rider_gap()
    # 16. Rider OEM
    gen_rider_oem()


def generate_txt_files():
    # 1. DUI Exclusion
    build_txt(
        "DUI_Exclusion_Directive.txt",
        """
AUTO INSURANCE INTERNAL MEMO
TO: Claims Adjusting Staff, Legal Department
DATE: June 15, 2026
SUBJECT: Strict Enforcement of Section 12.2 DUI Exclusion

Claims handlers are directed to enforce the DUI exclusion under Section 12.2.
If a driver is cited for and subsequently convicted of operating the vehicle under the influence
of alcohol, narcotics, or chemical substances at the time of a collision, physical damage coverage
to the insured vehicle (Collision coverage) is voided.

Key Procedures:
1. Always request blood alcohol concentration (BAC) records or police chemical tests if DUI is mentioned.
2. Deny physical damage payouts to the insured driver.
3. Third-party liability claims (Property Damage and Bodily Injury liability to third parties) MUST still be paid
   to protect the public interest up to state limits, but the insurer must seek recovery from the policyholder.
"""
    )
    # 2. Telematics Disclosure
    build_txt(
        "Telematics_Data_Collection_Privacy.txt",
        """
TELEMATICS DATA USE DISCLOSURE & PRIVACY AGREEMENT
This agreement governs the collection of data via the SmartDrive mobile app or installed vehicle OBD-II devices.

Data Collected:
- Speed: Vehicle velocity in miles per hour.
- Acceleration & Braking: Rapid increases in speed or hard braking events (deceleration exceeding 8.5 mph per second).
- GPS Location: Latitude and longitude mapping to determine road speed limits.
- Time of Day: Distinguishing high-risk night driving (12:00 AM to 4:00 AM).

Use in Claims Adjusting:
In the event of an accident, telematics data will be retrieved to verify vehicle speed and impact force.
If telematics logs indicate speed exceeded the legal limit by more than 20 mph at the time of collision,
contributory negligence will be assessed against the driver, potentially reducing liability payouts.
"""
    )
    # 3. Handler Checklist
    build_txt(
        "Claim_Filing_Checklist_For_Handlers.txt",
        """
CLAIMS HANDLER CUSTOMER INTAKE CHECKLIST
Follow these steps for every new claim call:

1. Verify Safety: Confirm if any injuries occurred. Call emergency services if needed.
2. Confirm Identity: Search name, policy number, and vehicle VIN to verify coverage is active.
3. Date & Time: Document the exact time of the accident. Ensure it falls within the policy term.
4. Loss Description: Document the driver's narrative. Note weather, road conditions, speed, and direction.
5. Police Report: Ask if police responded. Request agency name and case report number.
6. Towing Status: Ask if the vehicle was towed. Document the tow yard location to avoid storage fees.
7. Deductibles: Explain standard deductibles based on the policy plan before authorizing repairs.
"""
    )
    # 4. Subrogation Arbitration Rules
    build_txt(
        "Subrogation_Arbitration_Rules.txt",
        """
SUBROGATION ARBITRATION RULES & PROCEDURES
These rules govern inter-company arbitration for auto damage recovery:

1. Binding Forums: Both participating insurers must belong to Arbitration Forums, Inc.
2. Filing Limit: Claims must be submitted to arbitration within 1 year of the primary insurer's final payment.
3. Evidence Package: Submitting insurer must upload repair invoices, photographs, and police liability findings.
4. Defense Response: Responding carrier has 30 days to contest liability or damages.
5. Decision Binding: The arbitrator's decision is final and cannot be appealed in court.
6. Deductible Refund: If subrogation is successful, the policyholder's deductible must be refunded in proportion to the recovery percentage within 10 days.
"""
    )
    # 5. State Minimum Limits
    build_txt(
        "State_Minimum_Liability_Limits.txt",
        """
QUICK REFERENCE: STATE MINIMUM LIABILITY LIMITS
Format: Bodily Injury Per Person / Bodily Injury Per Accident / Property Damage

California (CA): 15/30/5
- $15,000 Bodily Injury per person
- $30,000 Bodily Injury per accident
- $5,000 Property Damage

Texas (TX): 30/60/25
- $30,000 Bodily Injury per person
- $60,000 Bodily Injury per accident
- $25,000 Property Damage

Florida (FL): 10/20/10
- $10,000 Personal Injury Protection (PIP) No-Fault
- $10,000 Property Damage Liability (PDL)
- Note: Bodily Injury is not required for standard basic policies, but highly recommended.

New York (NY): 25/50/10
- $25,000 Bodily Injury per person
- $50,000 Bodily Injury per accident
- $10,000 Property Damage
- $50,000 Basic No-Fault PIP
"""
    )

# Helper function imports from generate_auto_pdfs
# To avoid copy-paste, let's write a simple implementation of these helpers here or duplicate them.
# Duplicating them is safer to ensure it runs independently of modifications to generate_auto_pdfs.py.

def gen_california_regulations():
    title = "California Auto Insurance Claims Statutes & Codes"
    sections = [
        ("1. California Fair Claims Settlement Practices Regulations (Title 10, Chapter 5)",
         "Under California Insurance Code Section 790.03, insurers are required to adhere to strict timelines when handling auto claims. The insurer must acknowledge receipt of a claim within 15 calendar days. Upon receiving the claim, the insurer must provide all necessary claim forms and instructions to the claimant. Investigation of the claim must commence immediately and be concluded within 40 calendar days of receipt of proof of claim, unless written notices of delay are sent."),
        ("2. Pure Comparative Negligence Rule",
         "California follows a 'pure comparative negligence' system under California Civil Code Section 1714. A claimant can recover damages even if they are 99% at fault for an auto collision. Their financial recovery is reduced in direct proportion to their percentage of negligence. For example, if a claims handler determines that Claimant A suffered $10,000 in damages but was 40% responsible for the crash (due to speeding), Claimant A is eligible to receive a maximum payout of $6,000 (60% of the total claim value)."),
        ("3. Standard Statutes of Limitations",
         "Claims for physical damage to a motor vehicle in California must be filed within 3 years from the date of the accident. In contrast, claims involving bodily injuries resulting from an auto accident are subject to a 2-year statute of limitations from the occurrence date.")
    ]
    build_pdf("California_Auto_Claims_Regulations.pdf", title, sections)

def gen_texas_regulations():
    title = "Texas Insurance Code: Auto Claims Guidelines"
    sections = [
        ("1. Texas Prompt Payment of Claims Act (Subchapter B, Chapter 542)",
         "Texas law mandates quick claim resolutions. Insurers have 15 business days to acknowledge a claim, commence an investigation, and request all necessary documentation. Once proof of loss is received, the insurer must accept or reject the claim within 15 business days. If accepted, payment must be issued within 5 business days. If the insurer is unable to make a decision, they may request an extension of up to 45 calendar days, stating the specific reasons for the delay."),
        ("2. Proportionate Responsibility (51% Bar Rule)",
         "Texas operates under a modified comparative negligence system (Chapter 33, Civil Practice and Remedies Code). A claimant is barred from recovering damages if their percentage of responsibility is determined to be greater than 50%. If fault is 50% or less, their recovery is reduced by their percentage of fault. If Claimant A is found 51% negligent (e.g., failure to yield), they receive $0. If they are 50% negligent, they receive 50% of their total damages."),
        ("3. Personal Injury Protection (PIP) Rules",
         "Texas Insurance Code Section 1952.151 requires all insurers to offer Personal Injury Protection (PIP) with limits of at least $2,500. PIP covers medical costs, ambulance services, and 80% of lost wages regardless of fault. A policyholder must reject PIP coverage in writing; otherwise, it is automatically added to the policy.")
    ]
    build_pdf("Texas_Auto_Insurance_Code_2026.pdf", title, sections)

def gen_florida_regulations():
    title = "Florida No-Fault Motor Vehicle Law & PIP"
    sections = [
        ("1. No-Fault Insurance Framework",
         "Florida Statutes Section 627.736 governs the state's No-Fault auto insurance system. Under this system, each driver's own insurance company covers their medical bills and lost wages through Personal Injury Protection (PIP), regardless of who caused the accident. This system is designed to reduce civil lawsuits for minor auto injuries."),
        ("2. Personal Injury Protection (PIP) Deductibles & Limits",
         "The standard PIP limit in Florida is $10,000. It covers 80% of necessary medical treatments and 60% of lost wages, provided the claimant receives medical care within 14 days of the accident. If the claimant is diagnosed with an 'Emergency Medical Condition' (EMC), the full $10,000 limit is available. If no EMC is diagnosed by a medical professional, the PIP benefit is capped at $2,500."),
        ("3. Property Damage Liability (PDL)",
         "While medical bills are covered under No-Fault PIP, physical vehicle damage is NOT. Florida requires drivers to carry a minimum of $10,000 in Property Damage Liability (PDL) to pay for damage they cause to another driver's vehicle. Comparative fault rules apply to PDL claim settlements.")
    ]
    build_pdf("Florida_Auto_Insurance_Statutes.pdf", title, sections)

def gen_case_rear_end():
    title = "Claims Case Study: Rear-End Collision (ID: 2026-99382)"
    sections = [
        ("Summary of Occurrence",
         "On February 14, 2026, Policyholder A (driving a 2023 Tesla Model Y, insured under Plan B) failed to stop at a red light on El Camino Real, colliding with the rear bumper of Claimant B (driving a 2021 Toyota RAV4). The local police department arrived and cited Policyholder A for failing to maintain a safe distance. Fault was determined to be 100% on Policyholder A."),
        ("Vehicle Damage & Adjuster Appraisal",
         "Adjuster appraised the RAV4 damage at $4,850. Major repairs required: rear bumper cover replacement, trunk lid dent repair, and camera sensor re-calibration. Policyholder A's Tesla sustained $7,200 in damage (front bumper, hood, and cooling fan system)."),
        ("Claims Resolution & Deductibles",
         "Claimant B's RAV4 repairs were fully paid under Policyholder A's Property Damage Liability coverage ($4,850, no deductible applies to third-party liability claims). Policyholder A's Tesla was repaired under their own Collision coverage. Since Plan B Collision has a $500 deductible, the insurer paid $6,700 ($7,200 total minus the $500 deductible) directly to the approved repair shop.")
    ]
    build_pdf("Case_Study_Rear_End_Collision.pdf", title, sections)

def gen_case_hail():
    title = "Claims Case Study: Hail Damage (ID: 2026-10492)"
    sections = [
        ("Summary of Occurrence",
         "On April 22, 2026, a severe thunderstorm occurred in Plano, Texas. The policyholder's vehicle (2024 Ford F-150, insured under Standard Plan A) was parked in an open driveway and sustained golf-ball-sized hail damage across the hood, roof, and passenger doors. The windshield was also cracked in multiple places."),
        ("Damage Appraisal",
         "Adjuster inspected the vehicle and estimated the total repair cost at $6,800. Repairs specified: Paintless Dent Repair (PDR) for 42 minor dents on the hood and roof ($3,200), conventional bodywork and repainting of the passenger doors ($2,400), and windshield replacement ($1,200)."),
        ("Deductible Application",
         "Because hail is classified as an act of nature, the claim was filed under Comprehensive coverage. Under Plan A, the standard Comprehensive deductible is $500. However, the policyholder filed a separate request to waive the deductible for the windshield repair. Since the windshield had cracks longer than 6 inches, repair was impossible, requiring a full replacement. The $500 Comprehensive deductible was applied to the overall claim, resulting in a net payout of $6,300 ($6,800 minus $500 deductible).")
    ]
    build_pdf("Case_Study_Hail_Damage_PlanA.pdf", title, sections)

def gen_case_theft():
    title = "Claims Case Study: Stolen Vehicle & Recovery (ID: 2026-30291)"
    sections = [
        ("Summary of Occurrence",
         "On May 1, 2026, the policyholder reported their 2022 Honda Civic Sport stolen from their apartment complex parking lot in Seattle, Washington. The policyholder filed a police report immediately. Seattle Police recovered the vehicle 12 days later parked in an alleyway. The vehicle was towed to a salvage yard for inspection."),
        ("Vehicle Damage Report",
         "The recovered Civic had sustained severe strip-down damage. The wheels and tires were missing, the catalytic converter had been cut out, and the dashboard was torn apart to steal the infotainment console. The total cost of repair, including body shop labor, OEM parts replacement, and structural inspections, was estimated at $9,400. The Actual Cash Value (ACV) of the vehicle prior to theft was valued at $22,000."),
        ("Claims Resolution",
         "Because the repair cost ($9,400) did not exceed the total loss threshold (which is 75% of ACV, or $16,500), the vehicle was approved for repairs under Comprehensive coverage. The policyholder had Plan B, which features a $250 Comprehensive deductible. The insurer paid $9,150 ($9,400 minus $250) to the repair facility.")
    ]
    build_pdf("Case_Study_Stolen_Honda_Civic.pdf", title, sections)

def gen_custom_equip():
    title = "Endorsement Form 402: Custom Parts & Equipment (CPE)"
    sections = [
        ("1. Purpose of CPE Endorsement",
         "The standard auto insurance policy excludes coverage for aftermarket modifications, custom paint, modified suspension, or non-factory electronics unless declared. The Custom Parts and Equipment (CPE) endorsement provides physical damage coverage for items permanently installed by someone other than the original vehicle manufacturer."),
        ("2. Coverage Limits & Valuation",
         "Under CPE Endorsement Form 402, the standard limit for custom equipment is capped at $5,000, with options to increase the limit to $15,000 upon submitting invoices and photographs. Covered items include aftermarket alloy wheels, custom lift kits, performance exhaust systems, decals/wraps, and non-OEM sound systems. Valuation is based on Actual Cash Value (depreciated cost), not replacement cost."),
        ("3. Deductibles & Co-Insurance",
         "The deductible for CPE claims is identical to the primary Collision or Comprehensive coverage deductible. No coverage is provided for racing tires, radar detectors, or temporary items not permanently attached to the vehicle framework.")
    ]
    build_pdf("Endorsement_Custom_Equipment_Form402.pdf", title, sections)

def gen_rideshare_endorsement():
    title = "Endorsement Form 702: Rideshare Coverage (TNC Rider)"
    sections = [
        ("1. The Rideshare Gap Problem",
         "Standard personal auto policies exclude coverage for any period when a vehicle is used for hire or transport. Transportation Network Companies (TNCs, like Uber or Lyft) provide commercial insurance, but there are significant coverage gaps during specific phases of driving. The Rideshare Endorsement (TNC Rider) bridges these gaps to ensure continuous coverage."),
        ("2. Coverage Phase Breakdown",
         "Coverage varies by the driver's active TNC application status:\n- Phase 1 (App Open, Waiting for Match): The TNC commercial policy only provides low liability limits. The Rideshare Endorsement extends the driver's personal Collision and Comprehensive coverages to Phase 1.\n- Phase 2 (Match Accepted, En Route): Commercial TNC coverage is active, but carries a high deductible (e.g., Uber's $2,500 deductible). The endorsement provides 'deductible buy-down' coverage, reducing the effective deductible to the driver's personal policy limit (e.g. $500).\n- Phase 3 (Passenger in Vehicle): TNC commercial coverage is fully primary. Personal insurance does not apply."),
        ("3. Exclusion Notice",
         "No coverage is provided under this rider for commercial cargo transport, food delivery apps (e.g. DoorDash, UberEats), or traditional taxi services unless specifically endorsed.")
    ]
    build_pdf("Endorsement_Rideshare_TNC_Form702.pdf", title, sections)

def gen_classic_car():
    title = "Endorsement Form 805: Classic Car Agreed Value Clause"
    sections = [
        ("1. Definition of Classic Vehicle",
         "To qualify under Endorsement Form 805, the vehicle must be at least 20 years old, classified as a collector car, and maintained in excellent, unmodified condition. The vehicle must be stored in a fully enclosed, locked garage and cannot be used for daily commuting."),
        ("2. Agreed Value Settlement",
         "Unlike standard auto policies that settle claims based on Actual Cash Value (ACV, which factors in depreciation), classic car policies use an Agreed Value clause. The insurer and policyholder agree on the vehicle's value at policy inception based on a professional appraisal. In the event of a total loss, the insurer pays the full Agreed Value ($50,000, for example) with no deduction for physical depreciation."),
        ("3. Mileage Restrictions",
         "Coverage is subject to an annual mileage limitation of 2,500 miles. Odometer verification is required at each policy renewal. Exceeding this limit without written pre-approval voids the Agreed Value status.")
    ]
    build_pdf("Endorsement_Classic_Car_Agreed_Value.pdf", title, sections)

def gen_sop_total_loss():
    title = "SOP: Total Loss Vehicle Valuation & Title Branding"
    sections = [
        ("1. Total Loss Threshold Rule",
         "A vehicle is declared a total loss when the estimated cost of repairs plus the salvage value of the vehicle equals or exceeds the Actual Cash Value (ACV). Most states enforce a statutory total loss threshold. In California, the Total Loss Formula (TLF) is used (Cost of Repair + Salvage Value >= ACV). In Texas, a flat threshold of 100% of ACV is applied. In Florida, the threshold is 80% of ACV."),
        ("2. Valuation Methodology",
         "Claims handlers must determine ACV using a minimum of two independent valuation sources. Acceptable sources: CCC ONE Market Valuation Reports (primary), NADA Guides (Kelley Blue Book is secondary and only used for historical references), and local dealer market surveys. The valuation must adjust for mileage, pre-existing body dents, tire tread depth, and trim packages."),
        ("3. Salvage & Title Branding",
         "Once totaled, the vehicle title must be surrendered to the state Department of Motor Vehicles (DMV) for re-branding. The title will be branded as 'Salvage' if the vehicle is repairable, or 'Junk / Destruction' if it cannot be safely rebuilt. If the policyholder elects to retain the salvage vehicle, the salvage value estimate is deducted from their final settlement payout.")
    ]
    build_pdf("SOP_Total_Loss_Valuation.pdf", title, sections)

def gen_sop_labor_rates():
    title = "SOP: Labor Rates & Repair Shop Guidelines"
    sections = [
        ("1. Standard Repair Labor Rates",
         "Body shop labor claims are subject to regional maximum limits. Adjusters must verify that body shop estimates adhere to the following capped hourly labor rates for the 2026 calendar year:\n- Sheet Metal Repair: $62 per hour\n- Frame Alignment: $75 per hour\n- Refinishing / Painting: $62 per hour\n- Mechanical Work: $110 per hour\n- Aluminum Structural Repair: $120 per hour"),
        ("2. Paint & Material Calculations",
         "Paint materials are capped at $42 per refinish hour. Paint supplies must be detailed on the invoice. Any invoice requesting flat rates for paint materials without hourly breakdowns must be rejected."),
        ("3. Aftermarket vs. OEM Parts Policy",
         "For vehicles under 3 years old or with less than 36,000 miles, Original Equipment Manufacturer (OEM) parts must be approved for all safety-related components (suspension, steering, airbags, and collision sensors). For vehicles older than 3 years, Like Kind and Quality (LKQ) recycled parts or certified aftermarket parts must be utilized to minimize claims severity.")
    ]
    build_pdf("SOP_Auto_Repair_Labor_Rates.pdf", title, sections)

def gen_sop_fraud():
    title = "SOP: Fraud Detection & Red Flags for Claims Handlers"
    sections = [
        ("1. The Importance of Fraud Detection",
         "Insurance fraud increases premium costs for all policyholders. Claims handlers serve as the primary line of defense against fraudulent submissions. Suspicious claims must be flagged and referred to the Special Investigative Unit (SIU) within 3 business days of detection."),
        ("2. Red Flags Checklist",
         "Handlers must screen claims for the following suspicious indicators:\n- Chronology: A claim is filed within 10 days of policy inception or policy coverage upgrade.\n- Paper Accidents: Single-vehicle collisions occurring late at night in remote areas with no police report or independent witnesses.\n- Pre-existing Damage: Invoices requesting repairs for rust, corrosion, or old dents that do not match the direction of force in the reported accident.\n- Duplicate Invoices: Submission of identical photo angles or medical treatment dates for two different claims."),
        ("3. SIU Referral Procedure",
         "When referring a claim to the SIU, do not inform the policyholder or claimant. Compile all documentation, including photo metadata, police report details, and telematics history (if available). The claims handler must continue processing non-disputed elements of the claim unless instructed otherwise by the SIU investigator.")
    ]
    build_pdf("SOP_Claims_Fraud_Red_Flags.pdf", title, sections)

def gen_rider_roadside():
    title = "Policy Rider: Roadside Assistance & Towing Plus"
    sections = [
        ("1. Covered Roadside Emergencies",
         "The Roadside Assistance Plus rider provides 24/7 service for emergency situations. Covered events include: towing (following mechanical breakdown, not accidents), battery jumpstarts, flat tire replacement (installing the policyholder's spare), lockout assistance (vehicle entry only, no key replacement), and emergency fuel delivery (up to 2 gallons of gasoline)."),
        ("2. Distance Limits & Towing Caps",
         "Under standard policies, roadside towing is capped at 15 miles or a maximum of $75 per event. The policyholder is responsible for paying the tow operator directly for any mileage exceeding this limit. Under Premium Roadside riders, towing is covered up to 100 miles to the nearest qualified repair facility with zero out-of-pocket costs."),
        ("3. Exclusion of Accidental Towing",
         "Roadside assistance towing does NOT apply to vehicles damaged in collisions. Towing charges resulting from an accident are covered under the Collision coverage section of the primary policy, subject to the applicable collision deductible.")
    ]
    build_pdf("Rider_Roadside_Assistance_Plus.pdf", title, sections)

def gen_rider_gap():
    title = "Policy Rider: Gap Insurance Coverage"
    sections = [
        ("1. Purpose of Gap Coverage",
         "Vehicles depreciate rapidly upon purchase. If a vehicle is declared a total loss, the primary insurance settlement only pays the Actual Cash Value (ACV). If the outstanding balance on the car loan or lease exceeds the ACV, the policyholder is left with a financial 'gap'. Gap Insurance covers this difference."),
        ("2. Coverage Limits & Payments",
         "Gap coverage pays the difference between the primary insurance payout (ACV) and the unpaid principal balance on the loan or lease. For example, if a totaled car has an ACV of $18,000 but the loan balance is $23,000, Gap insurance pays the remaining $5,000 directly to the financial institution. It does not cover delinquent payments, late fees, extended warranties, or carry-over balances from previous loans."),
        ("3. Claim Requirements",
         "The claims handler must obtain a copy of the original finance contract, a certified loan payoff statement indicating the balance on the date of the loss, and the primary insurer's total loss settlement worksheet.")
    ]
    build_pdf("Rider_Gap_Insurance_Coverage.pdf", title, sections)

def gen_rider_oem():
    title = "Policy Rider: OEM Parts Replacement Guarantee"
    sections = [
        ("1. Guarantee Scope",
         "The Original Equipment Manufacturer (OEM) Parts Rider guarantees that only brand-new, factory-original parts manufactured by the vehicle's maker will be used in repairing physical damage. This rider overrides the standard policy clause that allows adjusters to specify aftermarket or LKQ recycled parts."),
        ("2. Eligibility & Term Limits",
         "This rider must be purchased at policy inception and is only available for vehicles under 5 years old. The guarantee applies to all body panels, structural frame elements, steering mechanisms, suspension assemblies, and active safety electronics (sensors, lidar, cameras)."),
        ("3. Claims Adjustment Adjustments",
         "When reviewing repair estimates under this rider, the adjuster is prohibited from writing sheet-metal or cosmetic estimates using aftermarket brands. If an OEM part is temporarily out of stock or backordered, the insurer will cover rental car reimbursement for up to an additional 15 days beyond the standard policy limit.")
    ]
    build_pdf("Rider_OEM_Parts_Guarantee.pdf", title, sections)


if __name__ == "__main__":
    print("=== Generating Massive Auto Insurance Dataset ===")
    
    # Clean previous sample folder contents
    for filename in os.listdir(OUTPUT_DIR):
        file_path = os.path.join(OUTPUT_DIR, filename)
        try:
            if os.path.isfile(file_path):
                os.unlink(file_path)
        except Exception as e:
            print(f"Error clearing {filename}: {e}")
            
    print("1. Generating 15 DOCX guidelines...")
    generate_docx_files()
    
    print("\n2. Generating 10 XLSX fee and deductibles sheets...")
    generate_xlsx_files()
    
    print("\n3. Generating 15 PDF statutes and studies...")
    generate_pdf_files()
    
    print("\n4. Generating 5 TXT directives...")
    generate_txt_files()
    
    print("\n=== Dataset Generation Complete (45 total files) ===")
