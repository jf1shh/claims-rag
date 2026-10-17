---
title: P1 claims tools replace canned simulation math
author: Jared
date: 2026-10-17
branch: feat/p1-claims-tools
cost: { minutes: 8, tokens: 136342 }
touches:
  - README.md
  - backend/agentic_router.py
  - docs/architecture.md
  - docs/build-history.md
  - docs/current-state.md
  - docs/roadmap-focus.md
  - HANDOFF.md
  - backend/claims_tools.py
  - tests/test_claims_tools.py
  - tests/test_simulated_claims_tools.py
---

## Decided
- backend/claims_tools.py holds the rule tables (labor caps, OEM age rules, state total-loss rules, plan deductibles, glass waiver, Form 402 limit) as constants copied from the generator text; tests/test_claims_tools.py asserts each value still appears in generate_auto_pdfs.py / create_sample_files.py / scripts/rebuild_golden_source_docs.py.
- Tools compute from CLAIMS_DATA line items, never totalEst; the answer states the gap. Non-math findings (custom-equipment lines, SIU exclusions) live in CLAIM_EVIDENCE in agentic_router.py.
- Missing inputs (ACV, salvage, mileage) are reported as needs_input, never assumed. Fraud/SIU narratives stay canned.

## Tried and ruled out
- Matching the old canned numbers: they cited a nonexistent Regional_Labor_Rates_2026.xlsx with per-state caps that contradict SOP_Auto_Repair_Labor_Rates.pdf. Computed results deliberately differ.

## Watch out
- Every claim's totalEst disagrees with its line-item sum.
- SOP OEM rule is silent at exactly 3 years (sop_gap). Chen's $3.5k custom limit conflicts with Form 402's $5,000 standard.
- Electrical lines are checked against the mechanical cap (SOP has no electrical class).
