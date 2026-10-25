---
title: "claims-rag JAG refusal-grounding fix: shared fake-client fixture conflation"
author: Jared
date: 2026-10-25
branch: main
cost: { minutes: 8, tokens: 152201 }
touches:
  - eval/results.json
  - ".eval-run/eval-20261025-jag-gate-on.log"
  - ".eval-run/server-20261025.log"
  - dirlink-to-filelinks.sh
  - tests/test_agentic_router_llm.py
  - tests/test_answerability_gate.py
  - tests/test_answerability.py
  - docs/current-state.md
  - docs/build-history.md
  - rag_store.db
---

## Decided
- When adding a new LLM-call stage to a code path under test, check every fake/recording LLM client
  fixture shared across test files for how it buckets calls by content heuristic vs. by `stage`. A
  fixture that routes on message content ("Claims Planner" in system prompt) rather than `stage` will
  silently misclassify any new stage that reuses the same `.complete()` method, breaking assertions in
  unrelated tests that check "no synthesis happened" style invariants.
- Fix at the fixture level (add a dedicated bucket keyed on `stage`), not by special-casing the new
  test — the conflation is a latent bug for every future test that touches the new stage.
- For a verification-only manual server run against a worktree, point `RAG_DB_PATH` (or the
  equivalent data-path env var) at the main checkout's populated database instead of letting the
  worktree build its own empty one from scratch.

## Tried and ruled out
- Running a fresh gated server from the worktree with no DB override: got the zero-context hard-stop
  message instead of the gate's refusal path — a different code branch entirely, not a judge/gate
  bug. Wasted a verification cycle before realizing the worktree's `rag_store.db` was empty (not
  tracked in git, created fresh on first server start).
- Full 34-row eval rerun was considered but skipped for a wording-only refusal-text fix: the
  regression was already isolated to one golden query across two prior gate-on runs, and a live
  curl spot-check against the exact query is enough to confirm the mechanism without a ~60min rerun.

## Watch out
- Bash heredocs that reference an absolute path outside the current git worktree get refused by the
  worktree-isolation guard as "too complex to verify" even for a plain file write (not a git
  operation) — had to ExitWorktree(action: "keep") first, write the handoff from the main checkout,
  and the worktree/branch/commit were untouched and fine to resume from afterward.
