---
title: "ClaimsRAG P5 workflow write-ups: Haiku docs need fact review"
author: Jared
date: 2026-10-21
branch: feat/p5-show-it
cost: { minutes: 24, tokens: 117849 }
touches:
  - README.md
  - docs/workflows/README.md
  - docs/workflows/demo-shot-list.md
  - docs/workflows/siu-checklist.md
  - docs/workflows/subrogation-triage.md
  - docs/workflows/supplement-review.md
  - docs/workflows/total-loss-explainer.md
  - ".handoff/loop-1773366/HANDOFF.md"
  - tests/test_simulated_claims_tools.py
  - backend/agentic_router.py
---

## Decided
- P5 write-ups (docs/workflows/*.md) quote numbers only from real simulation-mode output, captured with AgenticRAGRouter()._run_simulated_agent and stub store/embedder (same pattern as tests/test_simulated_claims_tools.py run()).
- Vendors are named only as "fits teams such as..."; never partnership, integration, or "X uses this tool".
- README states the eval gap: supplement review and total-loss explainer have no claim-scoped golden question; unit tests cover the button output.

## Tried and ruled out
- Haiku docs worker for fact-heavy write-ups: it passed a structural check (links, ids, word counts, banned words) yet got facts wrong in 3/4 files (wrong cap name, invented state rule, wrong deductible math, mis-described golden ids). Use Sonnet or write in-session.

## Watch out
- A structural docs check doesn't catch factual drift; review every number against captured output.
- `_run_simulated_agent` lives in backend/agentic_router.py, not claims_tools.py.
- gh pr merge needs in-session AskUserQuestion consent; the classifier blocks it otherwise.
