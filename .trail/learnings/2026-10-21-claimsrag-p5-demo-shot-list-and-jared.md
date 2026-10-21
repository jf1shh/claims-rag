---
title: "ClaimsRAG P5 demo shot list and SIU write-up: what's real in the UI"
author: Jared
date: 2026-10-21
branch: feat/p5-show-it
cost: { minutes: 5, tokens: 118019 }
touches:
  - docs/build-history.md
  - docs/current-state.md
  - docs/roadmap-focus.md
  - docs/workflows/README.md
  - docs/workflows/demo-shot-list.md
  - docs/workflows/siu-checklist.md
  - backend/agentic_router.py
  - config.py
  - frontend/app.js
---

## Decided
- The demo's refusal shot is the SIU checklist on #2026-55912 (record says 'SIU Flagged', no SOP indicator, answer says so). The live zero-context refusal (`_no_context` in backend/agentic_router.py) needs a loaded model and a query that retrieves nothing, so a short recording can't trigger it reliably.
- The README golden "coverage" column is reference-fact coverage = `correctness` in eval/results-*.json; context precision is `hybrid_precision`.

## Tried and ruled out
- A "Sign in (any email)" demo step: the local `development` auth provider (config.py AUTH_PROVIDERS default) returns the development principal, so no login gate appears; the gate takes only an OIDC token or service API key.

## Watch out
- The Explain Total Loss button sends its own prompt (frontend/app.js ~L1273), so its numbers differ from total-loss-explainer.md's typed-prompt example (ACV $14,000, salvage $2,500).
- The Fraud Red Flags SOP says do NOT inform the policyholder of an SIU referral; drafts reversed it. The 22:00-05:00 late-night window is the tool's assumption, not the SOP's.
- check_p5 caps write-ups at 700 words and needs an "advisory" statement per mode doc.
