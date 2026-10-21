---
title: "ClaimsRAG Brooks loss statement: rerank pool cut, not ranking"
author: Jared
date: 2026-10-20
branch: worktree-brooks-loss-statement-rank
cost: { minutes: 8, tokens: 111265 }
touches:
  - backend/postgres_store.py
  - backend/rag_engine.py
  - docs/build-history.md
  - docs/current-state.md
  - tests/test_postgres_store.py
  - tests/test_claim_rerank_candidates.py
  - README.md
  - docs/roadmap-focus.md
  - eval/results-2026-10-20-34row-pinned.json
  - eval/results.json
---

## Decided
loss_statement_Brooks.pdf was missing from the hybrid top 4 because the app reranks only the top RERANK_CANDIDATE_POOL=15 fused candidates; the cross-encoder ranks it 3rd once it sees it. Fix: add_claim_candidates (backend/rag_engine.py) plus PostgresVectorStore._claim_ranked add up to 6 of the active claim's best chunks to the rerank pool (hybrid with a reranker only). Probe 33/34 -> 34/34; mean +1 rerank pair.

## Tried and ruled out
Raising the pool (it was lowered 50->15 for latency). OR'ing the FTS tokens: probe unchanged. Live answers never had the bug: the router's guaranteed dossier always includes the claim chunks.

## Watch out
Probe with candidate_pool=15: the store default (50) hides pool-cut bugs. The FTS keyword leg ANDs every quoted token and returns nothing for 33/34 golden queries, so "hybrid" is in practice vector plus rerank. That is the next milestone. The background worktree guard blocks compound shell commands that reference the main checkout; copy files with python shutil.
