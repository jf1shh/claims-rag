# SIU Indicator Checklist

Screen claims for four fraud red flags—chronology, paper accident, pre-existing damage, and duplicate invoices—and cite SOP referral procedures.

## The adjuster's problem

Fraud red flags are easy to miss when processing many claims. A claim filed within 10 days of policy inception, a single-vehicle accident at 2 a.m. in a remote area, or a pre-existing dent that doesn't match the impact direction are signals to refer to SIU. The adjuster must know the rule, gather evidence (dates, photos), and cite it in the referral. This tool automates the checklist: given the claim data and documents, it flags which red flags are present and cites the SOP section for each.

## What it does

The tool screens for four fraud indicators:

1. **Chronology:** claim filed within 10 days of policy inception or a coverage upgrade.
2. **Paper accident:** single-vehicle collision at late night, remote area, no police report, no witness.
3. **Pre-existing damage:** rust, corrosion, or old dents not matching the force direction.
4. **Duplicate invoices:** identical photo angles or treatment dates across claims.

For each, the tool cites the SOP and notes which conditions are met. If any indicator is present, refer to SIU within 3 business days (do not inform the policyholder). The output includes the referral procedure and documentation checklist.

The LLM explanation walks through each flag and provides reasoning; the tool output includes SOP citations.

## Example

**Claim #2026-30291 (David Chen):** loss date 2026-01-18, policy effective 2024-11-01. Chronology: claim reported 443 days after policy inception, outside 10 days (ABSENT). Paper accident: not single-vehicle or not all conditions met (ABSENT). Pre-existing damage: inspection photos show passenger-side key scratches with rust oxidation in the scratch channels, consistent with damage older than the January 18 break-in (PRESENT). Duplicate invoices: no identical photo angle or treatment date against other claims on file (ABSENT). Result: 1 indicator present (pre-existing damage). Refer to SIU within 3 business days. Compile photo metadata, police report, and telematics before the referral.

See [backend/claims_tools.py](../../backend/claims_tools.py), `siu_checklist` function.

## How it is tested

- **Unit tests:** [tests/test_siu_checklist.py](../../tests/test_siu_checklist.py), 33 test functions.
- **Golden eval cases** (scored 2026-10-21, hybrid retrieval + reranking):
  - `chen-siu-checklist`: context precision 1.00, recall 1.00, faithfulness n/a (not scored), coverage 1.00. Tests the pre-existing-damage flag on a claim with rust-oxidation photos.
  - `brooks-siu-checklist`: context precision 0.50, recall 1.00, faithfulness 0.93, coverage 1.00. Tests multiple flags (chronology and paper-accident and pre-existing-damage present) on a new-policy claim with a late-night single-vehicle collision and rust through.
  - `rostova-siu-flag-unexplained`: context precision 0.33, recall 0.67, faithfulness 0.88, coverage 1.00. Hallucination probe: the claim record is flagged 'SIU Flagged' in the system, but none of the four SOP indicators are present. The tool must not invent a fraud reason; it correctly outputs "No SOP indicator is present" and notes the discrepancy.
  - `fraud-chronology-red-flag`: context precision 1.00, recall 1.00, faithfulness 0.57, coverage 1.00. Reference question on the chronology red-flag rule (filed within 10 days of policy inception or coverage upgrade).

## Limits

- The SOP tables are synthetic. The red-flag definitions, the 10-day chronology window, the late-night window (22:00–05:00, per the tool output), and the 3-business-day referral timeline shown are for demonstration only.
- The tool is advisory only. The SIU referral decision rests with the adjuster.
- The tool does not inform the policyholder or claimant of a referral; that is the adjuster's responsibility per SOP.
- Photo metadata, police reports, and telematics are required before referral. The tool does not verify that all documentation is available; it flags whether the indicators are present.
- If no LLM is loaded, the tool runs in simulation mode (`_run_simulated_agent` in [backend/agentic_router.py](../../backend/agentic_router.py)), and the explanation uses only the flag results and source citations.

## Where it fits

Fraud analytics platforms such as Shift Technology use this tool to prioritize claims for SIU review: an adjuster (or an automated workflow) can run the checklist on any incoming claim, and claims that flag one or more indicators are automatically routed to the fraud investigation team. The checklist also serves as a record of which SOP rules were applied and when the referral decision was made.
