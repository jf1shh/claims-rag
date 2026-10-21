---
title: "ClaimsRAG RAG technique screen: all flat, probe is the gate"
author: Jared
date: 2026-10-21
branch: feat/rag-retrieval-probe
cost: { minutes: 10, tokens: 99415 }
touches:
  - docs/build-history.md
  - docs/current-state.md
  - docs/research/2026-10-21-rag-techniques.md
  - scripts/probe_retrieval.py
  - tests/test_probe_retrieval.py
---

## Decided
- New retrieval techniques must beat scripts/probe_retrieval.py on this corpus before they ship. Baseline 2026-10-21: 33/33 rows served at top_k=4, MRR 0.8131, dossier-aware fact recall 0.8837.
- The probe mirrors production: the served set comes from a separate top_k=4 call, and fact recall includes store.get_claim_chunks(claim_id), because the router always adds the claim dossier.
- Remaining fact gaps are computed numbers (sums, differences, dates). Fix them in synthesis, not retrieval.

## Tried and ruled out
- xlsx rows as "Column: value" records (MRR 0.8086), filename/claim title prefixed to the child-chunk embedding input (0.814), BAAI/bge-small-en-v1.5 (0.812). Fact recall was unchanged for all three.
- The research also advised against RAPTOR/GraphRAG, bge-reranker-v2-m3 on CPU (~24x slower), HyDE, semantic caching and ColBERT for this corpus size.

## Watch out
- The corpus is too easy to separate variants: PDFs are prose with no tables, there are only 2 small xlsx files, and the DOCX files have no tables. Add distractor documents (the same SOP types across states) before trusting a small delta.
- rag_engine.py forces HF_HUB_OFFLINE=1, so pre-download any model you want to compare with huggingface_hub.snapshot_download.
- Research docs land in a public repo: grep them for local paths before committing.
