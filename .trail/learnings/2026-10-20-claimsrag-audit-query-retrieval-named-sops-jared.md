---
title: "ClaimsRAG audit-query retrieval: named SOPs rank below claim files"
author: Jared
date: 2026-10-20
branch: fix/security-hardening
cost: { minutes: 1, tokens: 20622 }
---

## Decided
- Probe retrieval without the LLM judge: start uvicorn on port 8010 (never 8000, that's Nexus) and POST /api/eval/search with mode hybrid_rerank, top_k 15 for every golden row, then report the rank of each expected `source`. It takes about a minute, against about 50 minutes for a RAGAS run.
- Baseline 2026-10-20: all expected sources are in the top 4 for 29/34 rows. The misses are long claim-scoped audit queries that name an SOP: Coverage Verification at rank 6, Subrogation at 7, plus brooks loss_statement_Brooks.pdf, which is not in the top 15 at all. rostova-dui-hallucination-probe has no source by design.
- Fix in progress: pin_named_documents() after rerank. A global doc whose title type word (sop, guideline, rider...) plus at least half of its distinctive title words appear in the query gets a slot, with at most 2 pins. Only candidates already in the scope-filtered pool qualify. Naive mode (use_fts=False) stays unpinned.
## Tried and ruled out
- Prepending the filename-derived title to the cross-encoder input (ms-marco-MiniLM-L-6-v2): Sterling went 6→5, subrogation 7→8 and 7→9. Reverted.
## Watch out
- `pkill -f "<pattern>"` inside a Bash call kills that call's own shell (exit 144). Use pgrep and kill the PID.
- The reranker's pairs are built in three places: RerankingEngine.rerank, _RerankBatchCoordinator._process and RemoteReranker.rerank. Change all of them together.
