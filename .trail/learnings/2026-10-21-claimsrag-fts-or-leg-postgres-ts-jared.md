---
title: "ClaimsRAG FTS OR leg: Postgres ts_rank normalization for parity"
author: Jared
date: 2026-10-21
branch: worktree-fts-or-query
cost: { minutes: 7, tokens: 4340 }
touches:
  - backend/postgres_store.py
  - backend/rag_engine.py
  - tests/test_postgres_store.py
  - tests/test_rag_engine.py
  - tests/test_fts_query.py
  - pg_migrate.py
---

## Decided
- After the FTS keyword leg switched to OR'ed terms (fts_match_query), Postgres ranks with `ts_rank(content_tsv, q, 1)` (length normalization, like bm25's length term). parity_runner --backend-b postgres: 0.912 with default ts_rank -> 0.940 with normalization 1 (CI floor 0.9). Before the OR change it was 0.9907 only because the keyword leg returned nothing in both stores.
- Acceptance is golden recall per backend, not parity alone: on the 48-doc golden corpus both stores give hybrid recall 0.960 and rerank recall 1.0; they differ only in unlabelled slots (file overlap 0.94 after rerank).

## Tried and ruled out
- `ts_rank_cd(...,1)` (parity 0.935, PG hybrid recall dropped to 0.944) and `ts_rank(...,8)` (0.935).

## Watch out
- parity_runner uses a hash-based fake embedder, so its vector leg is noise and any keyword-ranking difference is exaggerated. The remaining gap is IDF (ts_rank has none).
- To probe PG on the real corpus: `TENANT_ID=golden-probe pg_migrate.py --db <copy of rag_store.db>` into the local pgvector container, then call both stores' search_similarity with candidate_pool=15.
