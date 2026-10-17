---
title: P2 golden eval domains and running the eval on Lemonade
author: Jared
date: 2026-10-17
branch: feat/p2-domain-eval
cost: { minutes: 15, tokens: 100344 }
touches:
  - README.md
  - docs/build-history.md
  - docs/current-state.md
  - docs/roadmap-focus.md
  - eval/results.json
  - HANDOFF.md
  - docs/handoffs/2026-10-16.md
  - eval/results-2026-10-16-run1.json
  - eval/results-2026-10-16-run2.json
  - eval/run_eval.py
---

## Decided
- New golden sources go in scripts/rebuild_golden_source_docs.py (seed_demo.py does not run it); eval/adversarial/ingest.py calls the same main(), so the adversarial corpus picks them up on its next ingest.
- Keep each eval run as eval/results-YYYY-MM-DD-runN.json; results.json is the latest run. Report both runs, and the original-19 vs new subsets separately.
- run_eval.py takes EVAL_APP_URL; .claude/launch.json "claimsrag-eval" starts the app with SIMULATION_MODE=false.

## Tried and ruled out
- Gating on /api/v0/models state: Lemonade now returns state null for every model. Read /api/v1/health all_models_loaded (as backend/llm_client.py does).

## Watch out
- rag_store.db can be empty; rebuild with seed_demo.py then rebuild_golden_source_docs.py before any eval.
- Port 8000 may be held by another local app; the preview tool moves to 8002.
- Check gpu-preflight's exit code before `lemonade load`, in a separate step.
- 2026-10-16 Lemonade scores are not comparable to the 2026-09-22 LM Studio baseline.
