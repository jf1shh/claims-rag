# Claims Workflows

This directory contains write-ups for each of the four claims-handling modes in ClaimsRAG: review and triage tools that read standards of practice, policy riders, and claim documents to assist adjusters with structured decision-making.

| Mode | Write-up | Coverage |
|---|---|---|
| [Supplement Review](supplement-review.md) | Assess reinspection thresholds, labor caps, parts standards and betterment on supplemental repair estimates. | 20 unit tests; 2 golden eval cases (`supplement-reinspection-threshold`, `betterment-tires`). |
| [Total Loss Explainer](total-loss-explainer.md) | Determine total-loss status, compute salvage deduction, and cite state valuation and title-branding rules. | 18 unit tests; 2 golden eval cases (`rollover-total-loss-threshold`, `salvage-owner-retention`). |
| [Subrogation Triage](subrogation-triage.md) | Identify third-party fault and referral criteria; compute expected recovery and deductible reimbursement. | 27 unit tests; 4 golden eval cases (`webb-subrogation-triage`, `jenkins-subrogation-no-indicator`, `subrogation-referral-trigger`, `subrogation-partial-deductible`). |
| [SIU Indicator Checklist](siu-checklist.md) | Screen for four fraud red flags (chronology, paper accident, pre-existing damage, duplicate invoices) and cite SOP referral procedures. | 33 unit tests; 4 golden eval cases (`chen-siu-checklist`, `brooks-siu-checklist`, `rostova-siu-flag-unexplained`, `fraud-chronology-red-flag`). |

See also [demo-shot-list.md](demo-shot-list.md) for the 90-second screen-record plan.
