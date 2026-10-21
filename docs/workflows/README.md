# Claims workflows

One write-up per claims mode. Each mode is a button in the claim workspace that runs a deterministic tool over the claim's documents and the synthetic SOPs, then answers with the result and its citations. The README's [Claims workflows](../../README.md#claims-workflows) table summarises them.

| Mode | What it does | Tests |
|---|---|---|
| [Supplement Review](supplement-review.md) | Checks each supplement line against the labor caps, parts standard and betterment rules, and says whether reinspection is required. | 20 unit tests; golden cases `supplement-reinspection-threshold`, `betterment-tires` (rules only, not the button) |
| [Total Loss Explainer](total-loss-explainer.md) | Shows the state total-loss threshold math, the salvage deduction and the title-branding rule. | 18 unit tests; golden cases `rollover-total-loss-threshold`, `salvage-owner-retention` (rules only, not the button) |
| [Subrogation Triage](subrogation-triage.md) | Checks the referral criteria, and computes the expected recovery, the deductible reimbursement and the referral deadline. | 27 unit tests; golden cases `webb-subrogation-triage`, `jenkins-subrogation-no-indicator`, `subrogation-referral-trigger`, `subrogation-partial-deductible` |
| [SIU Indicator Checklist](siu-checklist.md) | Screens the four Fraud Red Flags SOP indicators, cites each one, and shows the referral procedure. | 33 unit tests; golden cases `chen-siu-checklist`, `brooks-siu-checklist`, `rostova-siu-flag-unexplained`, `fraud-chronology-red-flag` |

[demo-shot-list.md](demo-shot-list.md) is the plan for a 90-second screen recording.
