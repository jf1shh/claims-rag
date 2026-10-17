---
title: Recruiter README rewrite and About timing
author: Jared
date: 2026-10-17
branch: docs/recruiter-readme
cost: { minutes: 6, tokens: 105136 }
touches:
  - HANDOFF.md
  - docs/handoffs/2026-10-16.md
  - docs/build-history.md
  - docs/current-state.md
  - README.md
  - docs/adversarial-evaluation.md
  - tests/test_documentation_contract.py
---

## Decided
- README is recruiter-first: pitch + author, "For a hiring manager", a real computed labor_rate_check answer (#2026-99382), dated golden/adversarial tables, "What testing found", then quickstart and details.
- Every README number must trace to docs/current-state.md, docs/adversarial-evaluation.md, eval JSON, or a live tool run; no new metrics.
- GitHub About homepage points at README#results; change About only when the README anchor it targets is live on main.

## Tried and ruled out
- Applying the About before merge: the old homepage anchor (#evaluation--because-it-looks-right-isnt-good-enough) breaks or the new one 404s, depending on order.

## Watch out
- claims_tools are called only from _run_simulated_agent; _run_online_agent never calls them. Don't claim live-model tool use in public docs.
- tests/test_documentation_contract.py requires "pytest tests/", "/health/live" and "ICM workflow" in README.
- trail check raises keyword false positives on README prose; explain in the PR rather than rewording.
