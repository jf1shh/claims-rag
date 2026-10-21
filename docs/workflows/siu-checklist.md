# SIU Indicator Checklist

Screen a claim against the four red flags in the Fraud Red Flags SOP (chronology, paper accident, pre-existing damage, duplicate invoices), cite the rule and the evidence for each, and show the SIU referral procedure.

## The adjuster's problem

Red flags are easy to miss on a busy desk, and a referral has to cite both the SOP rule and the evidence in the dossier. A claim can also carry an "SIU Flagged" status with no recorded reason, and the adjuster then has to say plainly that the file does not support it rather than invent one.

## What it does

The **SIU Indicator Checklist** button runs `siu_checklist` ([backend/claims_tools.py](../../backend/claims_tools.py)) on the active claim. It checks four rules from `SOP_Claims_Fraud_Red_Flags.pdf`, section 2 (Red Flags Checklist):

1. **Chronology:** filed within 10 days of policy inception or a coverage upgrade.
2. **Paper accident:** single-vehicle collision, late at night, remote area, no police report, no independent witness. The SOP does not define "late at night"; the tool takes it as 22:00-05:00 and says so in its output.
3. **Pre-existing damage:** rust, corrosion or old dents that do not match the direction of force.
4. **Duplicate invoices:** identical photo angles or treatment dates across two claims, checked against the other claims on file.

Each indicator is PRESENT or ABSENT, with its evidence and source. If any is present, the header reads "refer to SIU within 3 business days of detection (adjuster decision)" and the output lists the referral procedure from section 3: do not inform the policyholder or claimant, compile photo metadata, the police report and telematics history, and keep processing the non-disputed parts of the claim. If none is present, the output says so. Issues that are not fraud indicators go under a separate "Coverage notes" heading.

## Example

Output captured in simulation mode on 2026-10-21.

- **#2026-30291 (David Chen):** one indicator present. Pre-existing damage: the passenger-side key scratches show rust oxidation in the scratch channels, consistent with damage older than the January 18, 2026 break-in (inspection_photos_Chen.pdf). Chronology is absent (reported 443 days after the 2024-11-01 policy inception), as are paper accident and duplicate invoices.
- **#2026-84410 (Tyler Brooks):** three indicators present. The claim was reported 6 days after the 2026-02-20 policy inception; it was a single-vehicle collision at 02:40 in a remote area with no police report and no independent witness; and the left rear quarter panel has rust-through outside the front-impact direction of force. No duplicate invoices.
- **#2026-55912 (Elena Rostova):** "No SOP indicator is present on this claim." The coverage notes say the claim record's status is 'SIU Flagged' but the record does not document a reason, and that the restart attempts after the stall raise a consequential-damage coverage question (Case_Study_Engine_Hydro_Lock.pdf), not a fraud indicator.

## How it is tested

- **Unit tests:** [tests/test_siu_checklist.py](../../tests/test_siu_checklist.py), 33 test functions.
- **Golden eval cases** (scored 2026-10-21, hybrid retrieval + reranking):
  - `chen-siu-checklist`: context precision 1.00, recall 1.00, faithfulness not scored, coverage 1.00. One indicator (pre-existing damage) present; referral indicated.
  - `brooks-siu-checklist`: context precision 0.50, recall 1.00, faithfulness 0.93, coverage 1.00. Three indicators (chronology, paper accident, pre-existing damage) present.
  - `rostova-siu-flag-unexplained`: context precision 0.33, recall 0.67, faithfulness 0.88, coverage 1.00. Hallucination probe: the record says 'SIU Flagged', no SOP indicator is present, and a grounded answer must not invent a fraud reason.
  - `fraud-chronology-red-flag`: context precision 1.00, recall 1.00, faithfulness 0.57, coverage 1.00. Reference question: which filing timing is a red flag, and how fast must it be referred?

## Limits

- The SOP, claims and photos are synthetic.
- The 22:00-05:00 late-night window is the tool's assumption, not an SOP rule.
- Duplicate invoices are checked only against the claims loaded in this instance.
- Advisory only: the referral is the adjuster's call.
- With no LLM loaded, the answer is built in simulation mode (`_run_simulated_agent` in [backend/agentic_router.py](../../backend/agentic_router.py)) from the checklist and its citations alone.

## Where it fits

Desk adjusters and SIU intake teams, and fraud-screening platforms such as Shift Technology, at the point where someone decides whether a claim needs a referral. Each red flag comes with its rule and evidence, and a flag with no support is called out instead of explained away.
