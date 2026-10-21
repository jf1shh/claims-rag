# Subrogation Triage

Identify third-party fault and subrogation referral indicators; compute expected recovery and deductible reimbursement.

## The adjuster's problem

Subrogation referrals must meet specific criteria: third-party fault at or above 25%, or a cause (defective part, public-entity road hazard, commercial vehicle) that justifies pursuit. The adjuster must screen liability evidence (police report, witness statements, telematics), assess fault, look up the state's comparative-fault rule, and estimate recovery potential. A referral is due 30 days after the first physical damage payment if the criteria are met. Misjudging which claims qualify for referral means lost recovery revenue; mishandling the deductible reimbursement calculation creates policyholder disputes.

## What it does

The tool computes:
- Third-party fault assessment on file (assessed percentage from police report, statements, or telematics).
- Referral criteria met: third party at or above 25% fault, or a cause indicator (defective part, public-entity road hazard, commercial vehicle).
- Recoverable share: damages multiplied by third-party fault percentage, capped by the state's comparative-fault rule (e.g., modified comparative fault with a 51% bar: a claimant more than 50% at fault recovers nothing).
- Deductible reimbursement: the same percentage of the deductible as of damages recovered, pro rata.
- Forum: inter-company arbitration at $100,000 or less; Subrogation Counsel above that.
- Referral deadline: 30 days from the first physical damage payment.

The answer cites the fault basis, recovery math, the state rule, and the referral timeline.

## Example

**Claim #2026-71845 (Marcus Webb, TX):** rear-end collision, Webb 30% at fault (unsafe lane change), FastFreight Logistics 70% at fault (following too closely). Damages $865.00. Police report, witness statement, and telematics (dash camera) are on file. Third-party fault 70% exceeds 25%, and the cause is a commercial vehicle (box truck). Referral criteria met. Under TX modified comparative fault (51% bar), Webb's recovery is 70% of damages: $865.00 × 70% = $605.50 expected recovery. Damages are below the $1,000.00 deductible, so the insured paid all $865.00; the deductible is reimbursed pro rata on what the insured actually paid: 70% of $865.00 = $605.50. Forum: inter-company arbitration ($100,000 or less). Referral due 30 days after first physical damage payment on 2026-02-10 = deadline 2026-03-12. Notify the insured in writing of any recovery within 10 business days.

See [backend/claims_tools.py](../../backend/claims_tools.py), `subrogation_triage` function.

## How it is tested

- **Unit tests:** [tests/test_subrogation_triage.py](../../tests/test_subrogation_triage.py), 27 test functions.
- **Golden eval cases** (scored 2026-10-21, hybrid retrieval + reranking):
  - `webb-subrogation-triage`: context precision 0.75, recall 1.00, faithfulness 0.89, coverage 1.00. Tests the full triage and recovery math on the claim above.
  - `jenkins-subrogation-no-indicator`: context precision 0.25, recall 1.00, faithfulness 0.89, coverage 0.90. Negative case: a hail claim with no third-party liability; the tool correctly identifies no subrogation indicator.
  - `subrogation-referral-trigger`: context precision 1.00, recall 1.00, faithfulness 1.00, coverage 1.00. Reference question: when must a claim be referred (25% third-party fault or a listed cause, within 30 days of the first payment)?
  - `subrogation-partial-deductible`: context precision 1.00, recall 1.00, faithfulness 0.75, coverage 0.87. Reference question: how much of the deductible is reimbursed on a partial recovery?

## Limits

- The SOP tables are synthetic. The referral criteria, comparative-fault rules, recovery forums, and timeline thresholds shown are for demonstration only.
- The tool is advisory only. The referral decision and negotiation strategy rest with the adjuster and the subrogation unit.
- Fault assessment and liability evidence are taken from the claim dossier (police report, statements, telematics). The tool does not conduct an independent investigation.
- If no LLM is loaded, the tool runs in simulation mode (`_run_simulated_agent` in [backend/agentic_router.py](../../backend/agentic_router.py)), and the explanation uses only the numeric results and source citations.

## Where it fits

Carrier recovery units and claims platforms such as Five Sigma, at the point where an adjuster decides whether a claim goes to subrogation. This mode shows a cited referral screen with the recovery math, the deductible reimbursement and the deadline in one place.
