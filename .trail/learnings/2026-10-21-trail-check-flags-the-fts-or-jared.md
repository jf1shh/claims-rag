---
title: trail check flags the FTS OR fix against superseded learnings
author: Jared
date: 2026-10-21
branch: worktree-fts-or-query
cost: { minutes: 1, tokens: 0 }
touches:
  - backend/postgres_store.py
  - backend/rag_engine.py
  - docs/build-history.md
  - tests/test_postgres_store.py
  - tests/test_rag_engine.py
  - docs/agent-work/enterprise-audit-2026-10-21.md
  - tests/test_fts_query.py
  - audit.log.jsonl
---

## Decided
- The 6 trail-check conflicts on the M11 FTS change (fts_match_query in backend/rag_engine.py, backend-constraints.md, test_rag_engine.py, test_postgres_store.py) are keyword false positives. The change replaces the implicit AND that the Oct 21 learning ruled out, and the stopword filter supersedes the Oct 20 note that OR alone changed nothing (hybrid recall 0.929 -> 0.960). Explain this in the commit and PR body; do not change the code.
- A detailed audit copy goes in docs/agent-work/, following security-review-2026-10-19.md, even though the repo is public.

## Tried and ruled out
- Nothing new; the two Oct 21 FTS learnings cover the ranking choices.

## Watch out
- The M12b test suite leaves audit.log.jsonl and its .lock (ignored files) in the worktree root. Delete them before closeout.
- The live 34-row eval logs nothing until it exits (stdout buffering), so wait on its pid, not on the log.
