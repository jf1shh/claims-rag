# Hard golden group for ClaimsRAG retrieval evaluation (added 2026-11-20).
#
# Why it exists: the original 33 golden rows are all served at top_k=4
# (docs/current-state.md, 2026-10-21 null result), so no retrieval change can
# move them. These rows compete against near-miss documents in
# eval/hard_corpus/ (a superseded 2025 edition, a sibling rider, another
# state's addendum, a similar form number) and are scored by
# scripts/probe_retrieval.py as their own group, on top of the original corpus.
#
# Rules for this group:
#   * Every row is answerable from the corpus. Each `reference` was checked
#     against the text of its `source` document(s) (the originals in
#     rag_store.db / sample generators, the new ones in eval/hard_corpus/).
#   * `avoid` lists the near-miss documents that must not outrank the source.
#   * All data is synthetic. A row leaves the group only for a proven error in
#     the row itself, never to improve the numbers.
#   * The original rows in golden_queries.py are untouched (regression group).
# `kind`: "near-miss" (distractor competes), "multi-source" (two documents are
# needed), "paraphrase" (hand-reworded query against an original document).

HARD_GOLDEN_QUERIES = [
    {
        "id": "hard-storage-current-rate",
        "kind": "near-miss",
        "claim_id": None,
        "query": "How much per day do we currently cover for tow-yard storage, and when does the policyholder start paying?",
        "reference": "Storage is covered up to $45 per day; storage accruing more than 3 calendar days after the written "
                     "notice that the vehicle is ready to be moved is the policyholder's responsibility.",
        "source": "SOP_Vehicle_Storage_Fees.txt",
        "avoid": ["SOP_Vehicle_Storage_Fees_2025.txt"],
    },
    {
        "id": "hard-labor-2026-aluminum-mechanical",
        "kind": "near-miss",
        "claim_id": None,
        "query": "What are the 2026 hourly labor caps for aluminum structural repair and for mechanical work?",
        "reference": "Aluminum Structural Repair is capped at $120 per hour and Mechanical Work at $110 per hour for 2026.",
        "source": "SOP_Auto_Repair_Labor_Rates.pdf",
        "avoid": ["SOP_Auto_Repair_Labor_Rates_2025.txt"],
    },
    {
        "id": "hard-totalloss-florida-threshold",
        "kind": "near-miss",
        "claim_id": None,
        "query": "At what share of ACV is a vehicle a total loss in Florida under the current valuation SOP?",
        "reference": "In Florida the total loss threshold is 80% of ACV.",
        "source": "SOP_Total_Loss_Valuation.pdf",
        "avoid": ["SOP_Total_Loss_Valuation_2025.txt"],
    },
    {
        "id": "hard-roadside-plus-fuel-and-tow",
        "kind": "near-miss",
        "claim_id": None,
        "query": "With the Plus roadside rider, how much fuel can be delivered and what is the standard towing cap?",
        "reference": "Emergency fuel delivery is up to 2 gallons of gasoline; standard roadside towing is capped at "
                     "15 miles or $75 per event.",
        "source": "Rider_Roadside_Assistance_Plus.pdf",
        "avoid": ["Rider_Roadside_Assistance_Basic.txt"],
    },
    {
        "id": "hard-roadside-basic-tow",
        "kind": "near-miss",
        "claim_id": None,
        "query": "What are the towing mileage and dollar caps on the Basic roadside rider?",
        "reference": "Towing under the Basic rider is capped at 10 miles or $50 per event.",
        "source": "Rider_Roadside_Assistance_Basic.txt",
        "avoid": ["Rider_Roadside_Assistance_Plus.pdf"],
    },
    {
        "id": "hard-rental-upgrade-daily-and-delay",
        "kind": "near-miss",
        "claim_id": None,
        "query": "Under the rental upgrade rider for a mid-size SUV or full-size sedan, what is the daily reimbursement "
                 "and do parts delays extend the repair period?",
        "reference": "The daily limit is raised from $30 to $50 per day. Delays from the repair facility or backordered "
                     "parts do not extend the standard 30-day limit unless the claims manager pre-approves them.",
        "source": "Endorsement_Rental_Car_Upgrade.docx",
        "avoid": ["Endorsement_Rental_Car_Premium_Plus_Form_R15.txt"],
    },
    {
        "id": "hard-rental-r15-delay",
        "kind": "near-miss",
        "claim_id": None,
        "query": "On rental Form R15, what is the daily limit and how far can backordered parts extend the rental?",
        "reference": "Form R15 pays $65 per day for up to 45 days; backordered-parts delays extend the limit by up to "
                     "10 days when the claims manager approves the delay in writing.",
        "source": "Endorsement_Rental_Car_Premium_Plus_Form_R15.txt",
        "avoid": ["Endorsement_Rental_Car_Upgrade.docx"],
    },
    {
        "id": "hard-dv-texas",
        "kind": "near-miss",
        "claim_id": None,
        "query": "For a Texas vehicle, what base percentage of ACV starts the third-party diminished value calculation, "
                 "and how fast must the written offer go out?",
        "reference": "The Texas base loss is 8% of pre-loss ACV, and the written offer must be issued within 15 business "
                     "days of receiving the repair invoice.",
        "source": "Guideline_Diminished_Value_Texas_Addendum.txt",
        "avoid": ["Guideline_Diminished_Value_Florida_Addendum.txt", "Guideline_Diminished_Value.docx"],
    },
    {
        "id": "hard-dv-florida",
        "kind": "near-miss",
        "claim_id": None,
        "query": "For a Florida vehicle, what base percentage of ACV starts the third-party diminished value calculation, "
                 "and how fast must the written offer go out?",
        "reference": "The Florida base loss is 12% of pre-loss ACV, and the written offer must be issued within 10 "
                     "business days of receiving the repair invoice.",
        "source": "Guideline_Diminished_Value_Florida_Addendum.txt",
        "avoid": ["Guideline_Diminished_Value_Texas_Addendum.txt", "Guideline_Diminished_Value.docx"],
    },
    {
        "id": "hard-dv-general-mileage",
        "kind": "near-miss",
        "claim_id": None,
        "query": "In the general third-party diminished value formula with no state addendum, what is the base percentage "
                 "and the mileage modifier for a car with 45,000 miles?",
        "reference": "The base loss is 10% of pre-loss ACV and the mileage modifier for 40,000-59,999 miles is 0.60.",
        "source": "Guideline_Diminished_Value.docx",
        "avoid": ["Guideline_Diminished_Value_Texas_Addendum.txt", "Guideline_Diminished_Value_Florida_Addendum.txt"],
    },
    {
        "id": "hard-tire-t210",
        "kind": "near-miss",
        "claim_id": None,
        "query": "What is the per-wheel limit and the deductible under tire and wheel protection Form T210?",
        "reference": "Form T210 pays up to $1,000 per wheel assembly and $2,500 per policy year, with a $50 deductible "
                     "per claim.",
        "source": "Endorsement_Tire_Wheel_Protection_Form_T210.txt",
        "avoid": ["Endorsement_Tire_Wheel_Protection_Form_T120.txt"],
    },
    {
        "id": "hard-tire-t120-legacy",
        "kind": "near-miss",
        "claim_id": None,
        "query": "A policyholder still has the legacy tire and wheel Form T120. What is the per-wheel limit and deductible?",
        "reference": "Legacy Form T120 pays up to $600 per wheel assembly and $1,500 per policy year, with a $100 "
                     "deductible per claim.",
        "source": "Endorsement_Tire_Wheel_Protection_Form_T120.txt",
        "avoid": ["Endorsement_Tire_Wheel_Protection_Form_T210.txt"],
    },
    {
        "id": "hard-salvage-owner-retention",
        "kind": "near-miss",
        "claim_id": None,
        "query": "A policyholder wants to keep a totaled car. How is the settlement reduced and how soon must payment be released?",
        "reference": "The settlement is reduced by the vehicle's salvage bid value, obtained in writing from the approved "
                     "salvage vendor; payment is released within 7 calendar days of the signed retention form.",
        "source": "SOP_Salvage_Owner_Retention.txt",
        "avoid": ["SOP_Salvage_Auction_Fees.txt"],
    },
    {
        "id": "hard-salvage-auction-fee",
        "kind": "near-miss",
        "claim_id": None,
        "query": "What does the salvage vendor charge to sell a totaled vehicle at auction?",
        "reference": "A flat $125 listing fee per vehicle plus 10% of the final sale price.",
        "source": "SOP_Salvage_Auction_Fees.txt",
        "avoid": ["SOP_Salvage_Owner_Retention.txt"],
    },
    {
        "id": "hard-multi-storage-and-pickup",
        "kind": "multi-source",
        "claim_id": None,
        "query": "A totaled car is sitting in a tow yard. What is the daily storage cap, and how soon must the salvage "
                 "vendor pick it up after assignment?",
        "reference": "Storage is covered up to $45 per day; the salvage vendor must pick up the vehicle within 3 business "
                     "days of the assignment.",
        "source": ["SOP_Vehicle_Storage_Fees.txt", "SOP_Salvage_Auction_Fees.txt"],
        "avoid": ["SOP_Vehicle_Storage_Fees_2025.txt"],
    },
    {
        "id": "hard-multi-supplement-labor",
        "kind": "multi-source",
        "claim_id": None,
        "query": "A shop's supplement adds aluminum structural labor. What hourly cap applies this year, and when is a "
                 "field reinspection required?",
        "reference": "Supplement labor follows the same caps as the original estimate: $120 per hour for aluminum "
                     "structural repair in 2026. A field reinspection is required when a single supplement exceeds $1,500 "
                     "or all supplements combined exceed 25% of the original approved estimate.",
        "source": ["SOP_Auto_Repair_Labor_Rates.pdf", "SOP_Supplement_Review.pdf"],
        "avoid": ["SOP_Auto_Repair_Labor_Rates_2025.txt"],
    },
    {
        "id": "hard-para-windshield",
        "kind": "paraphrase",
        "claim_id": None,
        "query": "Customer cracked a windshield that has a camera behind it. Do they pay anything, and is the camera "
                 "recalibration covered?",
        "reference": "Under the zero-deductible glass rider the comprehensive deductible is waived ($0 out-of-pocket) and "
                     "ADAS camera recalibration fees are fully covered.",
        "source": "Endorsement_Windshield_Zero_Deductible.docx",
    },
    {
        "id": "hard-para-supplement-reinspect",
        "kind": "paraphrase",
        "claim_id": None,
        "query": "When do we have to send someone to look at a shop's added repair lines before approving, and how long "
                 "do we have to answer them?",
        "reference": "A field reinspection is required when a single supplement exceeds $1,500 or all supplements "
                     "combined exceed 25% of the original estimate; the adjuster responds within 2 business days of "
                     "complete documentation.",
        "source": "SOP_Supplement_Review.pdf",
    },
    {
        "id": "hard-para-plan-b-table",
        "kind": "paraphrase",
        "claim_id": None,
        "query": "Premium plan, collision: what deductible and how much rental per day?",
        "reference": "Premium Auto (Plan B) collision has a $500 deductible and a rental reimbursement of $50 per day.",
        "source": "Auto_Deductibles_And_Limits.xlsx",
    },
]
