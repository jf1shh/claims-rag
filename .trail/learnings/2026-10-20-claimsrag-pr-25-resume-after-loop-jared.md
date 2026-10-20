---
title: "ClaimsRAG PR #25 resume after loop handoff"
author: Jared
date: 2026-10-20
branch: fix/security-hardening
cost: { minutes: 9, tokens: 152175 }
touches:
  - ".env.example"
  - backend/api.py
  - backend/document_limits.py
  - config.py
  - docs/build-history.md
  - docs/current-state.md
  - tests/test_api_chat_stream.py
  - tests/test_hardening_lows.py
  - tests/test_web_edge.py
  - eval/golden_queries.py
---

## Decided
- Resumed from ~/.handoff/loop-1633722/HANDOFF.md. Verified state first: branch fix/security-hardening at 924f296 is in sync with origin. PR #25 is open and MERGEABLE, and its CI run 39447796325 is in progress.
- Wait for CI with a background `gh run watch <id> --exit-status`, not polling. Merge only after both pytest-linux and postgres are green. Revert point is f433740, merge-commit style.
- Hub checkpoint format: copy Checkpoints/20261020T143845Z-desktop-live-tools-pr24-merged.md (sections: Progress and decisions, Verification evidence, Artifacts and action items, Resume, Sync).

## Tried and ruled out
- Starting the next milestone (golden queries for subrogation, coverage and SIU) while #25 is open. A commit pushed to the shared branch would land in PR #25, so the next milestone's edits wait until after the merge and fast-forward.

## Watch out
- In a fresh session the merge instruction exists only in memory, so the auto-mode classifier may deny `gh pr merge 25`. If it does, ask the owner to say "merge #25".
- postgres has flaked once on the ingestion load-test floor (46.2 < 50). Rerun once with `gh run rerun <id> --failed` before suspecting code.
- eval/golden_queries.py already has some coverage and SIU rows (lines ~44-178) plus a P2 gap block at line ~200. Extend that block; don't duplicate it.
