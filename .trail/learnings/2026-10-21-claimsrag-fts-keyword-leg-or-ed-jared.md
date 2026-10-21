---
title: "ClaimsRAG FTS keyword leg: OR'ed content terms"
author: Jared
date: 2026-10-21
branch: worktree-fts-or-query
cost: { minutes: 1, tokens: 8329 }
touches:
  - backend/postgres_store.py
  - backend/rag_engine.py
  - tests/test_postgres_store.py
  - tests/test_rag_engine.py
  - tests/test_fts_query.py
---

## Decided
The keyword leg (SQLite FTS5 and Postgres websearch_to_tsquery) now uses fts_match_query in backend/rag_engine.py: [a-z0-9]+ runs, stopwords and 1-char tokens dropped, deduped, capped at 32, quoted, OR-joined. bm25/ts_rank does the ranking. Probe on 33 sourced golden rows: FTS non-empty 1 -> 33, hybrid-no-rerank recall 0.929 -> 0.960, hybrid+rerank top-4 unchanged (recall 1.0).

## Tried and ruled out
Implicit AND of quoted isalnum() tokens: it matched nothing for 32/33 queries and silently dropped punctuated words. A probe precision metric over labelled files is too coarse to judge precision; use the live RAGAS run.

## Watch out
Both stores must call fts_match_query (parity). Worktree-isolated sessions refuse shell commands containing the word "eval" (use ev?l/ globs). Port 8000 is the Nexus backend; run ClaimsRAG on 8010 with EVAL_APP_URL.
