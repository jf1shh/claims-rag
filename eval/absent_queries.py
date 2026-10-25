# Questions the corpus cannot answer, for measuring the "not found" signal.
#
# Retrieval always returns top_k passages, relevant or not, so a question with
# no answer in the corpus still reaches synthesis with lookalike context. These
# rows measure how well a score separates them from the answerable golden rows
# (scripts/probe_answerability.py). Idea from Subworkflow's "RAG vs JAG"
# experiment (2026-10), whose hardest misses were near-misses: a real document
# very like the one asked about.
#
# Every row was checked on 2026-10-25 against the full text of the 48-document
# corpus (parent_chunks) and CLAIMS_DATA: the named form, state, rider, invoice
# number, reading or statement does not exist. `trap` names the real content
# the question is designed to be confused with; rows without one are far misses.

ABSENT_QUERIES = [
    # ---- Global: near misses ----
    {"id": "absent-form-905-limit", "claim_id": None,
     "query": "What is the coverage limit under Endorsement Form 905?",
     "trap": "Endorsement Forms 402 (custom equipment) and 702 (rideshare) exist"},
    {"id": "absent-upholstery-labor-cap", "claim_id": None,
     "query": "What is the labor rate cap for upholstery and interior trim repair?",
     "trap": "the labor SOP caps mechanical, sheet metal, frame, refinish and aluminum work"},
    {"id": "absent-nevada-ack-deadline", "claim_id": None,
     "query": "How many days does an insurer have to acknowledge a claim under Nevada regulations?",
     "trap": "California's acknowledgment deadline is in the corpus"},
    {"id": "absent-ny-pip-minimum", "claim_id": None,
     "query": "What is the minimum PIP limit required in New York?",
     "trap": "Texas and Florida PIP limits are in the corpus"},
    {"id": "absent-arbitration-filing-fee", "claim_id": None,
     "query": "What filing fee does the insurer pay to file an inter-company arbitration case?",
     "trap": "the subrogation SOP sends recoveries of $100,000 or less to arbitration"},
    {"id": "absent-um-pd-deductible", "claim_id": None,
     "query": "What is the deductible for uninsured motorist property damage coverage?",
     "trap": "the deductibles table lists collision and comprehensive deductibles"},
    # ---- Global: far misses ----
    {"id": "absent-motorcycle-accessory-limit", "claim_id": None,
     "query": "What is the accessory coverage limit under the motorcycle endorsement?"},
    {"id": "absent-pet-injury-benefit", "claim_id": None,
     "query": "How much does the policy pay for injuries to a pet riding in the insured vehicle?"},
    {"id": "absent-boat-trailer", "claim_id": None,
     "query": "Is damage to a towed boat trailer covered when it detaches from the insured vehicle?"},
    # ---- Claim-scoped: the claim's own documents are the near miss ----
    {"id": "absent-chen-invoice-number", "claim_id": "#2026-30291",
     "query": "What is the total on invoice INV-48213 in David Chen's claim?",
     "trap": "Chen's custom equipment receipt has a total but no invoice number"},
    {"id": "absent-webb-settlement", "claim_id": "#2026-71845",
     "query": "What settlement amount did FastFreight Logistics' insurer agree to pay on Marcus Webb's claim?",
     "trap": "the police report and dashcam name FastFreight; no recovery has happened"},
    {"id": "absent-sterling-bac", "claim_id": "#2026-99382",
     "query": "What was the driver's blood alcohol reading in Matthew Sterling's police report?",
     "trap": "Sterling's police report exists and has no BAC or DUI finding"},
    {"id": "absent-jenkins-tow-statement", "claim_id": "#2026-10492",
     "query": "What did the tow truck driver say in his statement on Sarah Jenkins's hail claim?",
     "trap": "Jenkins has a Premium Towing Plus endorsement and an inspector's photo notes"},
    {"id": "absent-brooks-rental", "claim_id": "#2026-84410",
     "query": "How many days of rental car did Tyler Brooks receive, and from which agency?",
     "trap": "Brooks has a loss statement and inspection photos, no rental record"},
    {"id": "absent-rostova-witness", "claim_id": "#2026-55912",
     "query": "What did the eyewitness say about how Elena Rostova's BMW entered the water?",
     "trap": "Rostova's telematics log describes the water entry; there is no witness"},
]
