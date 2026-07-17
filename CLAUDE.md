# AutoClaimsRAG — Engineering Guide

> **This is a living document.** Every session reads it at the start and updates it at the end.
> **GitHub Repository**: [jf1shh/auto-claims-rag](https://github.com/jf1shh/auto-claims-rag)

---

## What This Project Is

AutoClaimsRAG is a high-performance local RAG (Retrieval-Augmented Generation) system designed for auto insurance claims handlers to query reference documentation using natural language. It allows users to search, extract, and draft responses from guidelines, endorsements, state codes, and adjuster reports. The application runs entirely locally: it generates embeddings using a local CPU-based model (`sentence-transformers/all-MiniLM-L6-v2`), indexes them in a local SQLite database, and answers queries through a **stateful agentic RAG router** that plans, retrieves, self-corrects, and synthesizes.

Retrieval is **hybrid**: dense vector search over child chunks is fused with FTS5 keyword search over parent chunks via Reciprocal Rank Fusion (RRF), then re-ordered by a local cross-encoder reranker. Documents can be scoped globally or attached to a specific claim folder. Generation is routed to a local LM Studio server (OpenAI-compatible `/v1`) with a high-fidelity rule-based simulation fallback when no LLM is loaded.

---

## Always Read First

1. This file — architecture, constraints, and current state.
2. [backend/rag_engine.py](file:///C:/PERSONAL/backend/rag_engine.py) — Document parsing, parent/child chunking, embedding, cross-encoder reranking, and the hybrid (vector + FTS5 + RRF) vector store with an in-memory normalized embedding cache.
3. [backend/agentic_router.py](file:///C:/PERSONAL/backend/agentic_router.py) — Stateful agentic RAG router: query planning/decomposition, multi-sub-query retrieval, self-correction fallback, LLM synthesis, and the rule-based simulation engine (with demo `CLAIMS_DATA`).
4. [backend/app.py](file:///C:/PERSONAL/backend/app.py) — FastAPI routing endpoints, per-claim document scoping, physical file serving, LLM status checks, and OpenAI compatibility layers.

---

## Session Protocol — Mandatory

This document is the project's memory. Every session must follow this protocol exactly.

### Before starting any task
1. Read this entire CLAUDE.md. Do not skip sections.
2. Check **Current State** — confirms what's working, what's broken, what's next.
3. Check **Debugging History** for the area you're working in. If an approach has already failed, do not repeat it.
4. For any external API or service: fetch live documentation before writing a single line. Never rely on training data for API contracts.
5. State today's goal in one sentence. Do not begin until that goal is confirmed.

### During the session
6. Stay on the current Build Plan phase. Do not drift to other work without an explicit decision to change.
7. When an attempt fails, record it in Debugging History immediately — what was tried, what happened, root cause if known.
8. When an attempt succeeds, note it so Current State can be updated.
9. If you discover a constraint not yet documented, add it to Critical Constraints.
10. Review proposed changes before accepting. Do not accept changes to out-of-scope components.

### At the end of every session
11. Run the self-assessment: update Current State, Build Plan status, Debugging History, What's Next, and Session Log.
12. If new architectural decisions were made, add them to Locked Design Decisions with reasoning.
13. If a new API or component was successfully integrated, document its working contract here.

**The goal:** no session ever repeats a failure a previous session diagnosed.
The project moves forward along the plan — never sideways or backwards.

---

## Architecture Overview

### Components

* **DocumentParser** ([backend/rag_engine.py](file:///C:/PERSONAL/backend/rag_engine.py)): Extracts text from PDF (`pypdf`), DOCX (`python-docx`), Excel (`pandas`/`openpyxl`), and TXT files.
* **TextChunker** ([backend/rag_engine.py](file:///C:/PERSONAL/backend/rag_engine.py)): Hierarchical parent/child chunking. Parent chunks (~1200 chars / 200 overlap) preserve context and back the FTS5 index; child chunks (~250 chars / 50 overlap) are embedded for precise vector matching. Boundaries snap to whitespace.
* **EmbeddingEngine** ([backend/rag_engine.py](file:///C:/PERSONAL/backend/rag_engine.py)): Instantiates `sentence-transformers/all-MiniLM-L6-v2` locally to generate 384-dimensional vectors. Imported lazily so the vector store can be used without loading torch.
* **RerankingEngine** ([backend/rag_engine.py](file:///C:/PERSONAL/backend/rag_engine.py)): Local `cross-encoder/ms-marco-MiniLM-L-6-v2` on CPU. Re-scores the fused candidate pool against the raw query for final ordering.
* **SQLiteVectorStore** ([backend/rag_engine.py](file:///C:/PERSONAL/backend/rag_engine.py)): Stores documents, parent/child chunks, embedding BLOBs, and an FTS5 index. `search_similarity` runs **hybrid retrieval**: vectorized cosine over child chunks (mapped up to best parent) + FTS5 keyword search, fused via RRF, then cross-encoder reranked. Embeddings are held in an **in-memory, pre-normalized matrix cache** (built lazily, invalidated on add/delete) so queries avoid re-reading BLOBs and recomputing corpus norms.
* **AgenticRAGRouter** ([backend/agentic_router.py](file:///C:/PERSONAL/backend/agentic_router.py)): Orchestrates each query. Online mode asks the LLM for a JSON plan (which stores to search + sub-queries), retrieves per sub-query, self-corrects on empty results, and synthesizes a grounded answer. Simulated mode runs local retrieval plus a Python rule engine keyed to demo claims/audit types. Emits step-by-step `pipeline_logs`.
* **FastAPI Server** ([backend/app.py](file:///C:/PERSONAL/backend/app.py)): Exposes REST endpoints for global + per-claim upload, listing, deletion, document content/download, agentic chat, and LLM connection status.
* **Frontend Web Dashboard** ([frontend/index.html](file:///C:/PERSONAL/frontend/index.html)): HTML/CSS/JS UI with a claims queue, per-claim document folders, claims chat, clickable/viewable document citations, and real-time RAG pipeline logs.

### File Structure

```
C:\PERSONAL\
├── requirements.txt            ← Python project dependencies
├── rag_store.db                ← SQLite vector database (git-ignored)
├── create_sample_files.py      ← Programmatic auto claims guidelines generator
├── generate_auto_pdfs.py       ← reportlab PDF document generator
├── generate_massive_dataset.py ← Bulk seed-data generator (PDF/DOCX/XLSX/TXT)
├── ingest_all.py               ← Script to batch-index all sample documents
├── test_rag_pipeline.py        ← CLI hybrid-search verification script
├── sample_guidelines/          ← Generated source documents (git-ignored)
├── stored_documents/           ← Physical copies served to the UI (viewer/download)
├── backend/
│   ├── app.py                  ← FastAPI REST API + per-claim endpoints
│   ├── rag_engine.py           ← Parsing, chunking, embedding, reranking, hybrid store
│   └── agentic_router.py       ← Agentic planner/retriever/synthesizer + simulation engine
└── frontend/
    ├── index.html              ← Claims Handler Dashboard UI
    ├── style.css               ← ClaimCenter/Jutro light-mode styling
    └── app.js                  ← Dynamic client-side operations
```

---

## Running the Project

Ensure the virtual environment is used to run all python scripts.

```powershell
# 1. Start the FastAPI backend server (listens on port 8000)
.venv\Scripts\python -m uvicorn backend.app:app --reload --port 8000

# 2. Access the interactive web interface in your browser
http://localhost:8000

# 3. Generate sample auto insurance guidelines (PDF)
.venv\Scripts\python generate_auto_pdfs.py

# 4. Batch-index all guidelines into the vector database
.venv\Scripts\python ingest_all.py

# 5. Run the CLI pipeline verification tests
.venv\Scripts\python test_rag_pipeline.py
```

---

## Critical Constraints

* **100% Local Ingestion**: No document contents or raw vector embeddings may be sent to external cloud vector databases. All vector searches must be run on the host CPU/GPU locally.
* **Vectorized Cosine Similarity**: NEVER calculate cosine similarity using iterative Python loops over individual rows. Chunks must be compiled into a 2D numpy matrix and dot-products calculated in a single matrix multiplication to prevent CPU bottlenecks when scaling.
* **Normalized Embedding Cache Invalidation**: `search_similarity` reads from an in-memory, pre-normalized embedding matrix cache. Any code path that mutates `child_chunks`/`documents` MUST call `self._invalidate_vector_cache()` after commit, or searches will serve stale results. Cache rows are L2-normalized at build time, so the query vector must also be normalized and similarity is a plain dot product — do not reintroduce per-query corpus-norm computation.
* **UTF-8 Robustness**: PDF and TXT text extraction must handle decoding errors gracefully (`errors="ignore"` or similar safeguards) to prevent crashes on non-ASCII symbols.
* **Lazy ML Imports**: `sentence_transformers`/torch are imported inside `EmbeddingEngine`/`RerankingEngine.__init__`, not at module top, so the pure-numpy store stays importable without the ML stack. Keep it that way.

---

## Locked Design Decisions

| Question | Decision | Reasoning |
|---|---|---|
| Which local embedding model? | `all-MiniLM-L6-v2` | Lightweight (90MB), runs extremely fast on CPU, and achieves high similarity accuracy. |
| Which vector database? | SQLite (`sqlite3` + `numpy`) | Highly portable, serverless, self-contained, and requires no complex C++ binary builds on Windows. |
| LLM Integration Protocol? | OpenAI-compatible `/v1` | Connects to LM Studio (`port 1234`) using standard chat completion endpoints. |
| Retrieval strategy? | Hybrid (vector + FTS5) fused with RRF, then cross-encoder rerank | Dense recall + keyword precision; RRF needs no score calibration; the reranker sharpens final ordering for grounded answers. |
| Chunking scheme? | Parent/child (small embedded children → larger parent context) | Embed small chunks for precise matching, but return the larger parent so the LLM sees enough surrounding context. |
| Query orchestration? | Stateful agentic router (plan → retrieve → self-correct → synthesize) | Decomposes multi-part claims questions, scopes retrieval per claim, and recovers from empty results instead of failing. |
| Embedding search performance? | In-memory pre-normalized matrix cache, invalidated on write | Avoids re-reading BLOBs and recomputing corpus norms every query; keeps the "no per-row loops" constraint intact at scale. |

---

## Component / Service Status

| Component | File | Status | Notes |
|---|---|---|---|
| RAG Core Engine | `backend/rag_engine.py` | **Active** | Hybrid vector+FTS5+RRF retrieval with cross-encoder rerank; in-memory normalized embedding cache. |
| Agentic Router | `backend/agentic_router.py` | **Active** | Plan → retrieve → self-correct → synthesize; online (LM Studio) + simulated modes. |
| Reranking Engine | `backend/rag_engine.py` | **Active** | `cross-encoder/ms-marco-MiniLM-L-6-v2` on CPU. |
| FastAPI Backend | `backend/app.py` | **Active** | Serving on port 8000; per-claim scoping + document serving. |
| Web Frontend | `frontend/*` | **Active** | Claims queue, per-claim folders, viewable citations, pipeline logs. |
| Batch Ingest CLI | ingest_all.py | Active | Batch-indexes the sample/seed document set. |

---

## Build Plan

### Phase 1 — Core RAG & Ingestion (Completed)
* **Goal**: Parse PDF/Word/Excel, chunk text, generate embeddings, and verify similarity.
* **Builds**: `DocumentParser`, `TextChunker`, `EmbeddingEngine`
* **Done when**: Running the CLI test returns accurate match files and similarity scores.
* **Status**: Completed.

### Phase 2 — API Server & Database (Completed)
* **Goal**: Expose endpoints for uploading documents, listing, and querying local LLMs.
* **Builds**: `SQLiteVectorStore`, FastAPI endpoints, LM Studio connection.
* **Done when**: API endpoints successfully store embeddings and fetch LLM answers.
* **Status**: Completed.

### Phase 3 — Claims Handler Dashboard (Completed)
* **Goal**: Build a visual interface for claim adjusters to drag-and-drop files and chat.
* **Builds**: HTML/CSS/JS frontend (connected to FastAPI static mount).
* **Done when**: Navigating to `localhost:8000` allows full uploads, chatting, and displays visual logs.
* **Status**: Completed.

### Phase 4 — Optimized Vectorization & TXT Support (Completed)
* **Goal**: Optimize similarity checks to use matrix operations and support text files.
* **Builds**: Vectorized `search_similarity`, `.txt` file upload rules, periodic polling.
* **Done when**: Ingesting 15 documents completes under 1s, and LM Studio models are auto-detected in real time.
* **Status**: Completed.

### Phase 5 — Hybrid Retrieval, Reranking & Claim Scoping (Completed)
* **Goal**: Improve retrieval quality with hybrid search and per-claim document folders.
* **Builds**: Parent/child chunking, FTS5 index, RRF fusion, `RerankingEngine`, `claim_id` scoping, physical document serving, viewable citations.
* **Done when**: Hybrid search + reranking returns the correct source as the top match; per-claim uploads are isolated and viewable in the UI.
* **Status**: Completed.

### Phase 6 — Agentic RAG Router (Completed)
* **Goal**: Replace the single-shot chat call with a planning/retrieval/synthesis loop.
* **Builds**: `AgenticRAGRouter` (online + simulated), query decomposition, self-correction fallback, dynamic model-name resolution, structured `pipeline_logs`.
* **Done when**: Queries route through the planner and return grounded, cited answers in both online and simulated modes.
* **Status**: Completed.

### Phase 7 — Performance Hardening (Completed)
* **Goal**: Remove per-query CPU/I-O bottlenecks in the retrieval hot path.
* **Builds**: In-memory pre-normalized embedding cache with write-invalidation, `top_k`-aware candidate pool, lazy ML imports.
* **Done when**: Repeat queries reuse the cached matrix (verified ~1.7× faster on the vector stage at current corpus, widening with scale) with identical results.
* **Status**: Completed.

---

## MVP Scope

### In scope
* Offline ingestion and search over PDF, DOCX, XLSX, and TXT guidelines.
* Hybrid retrieval (dense vector + FTS5 keyword) with RRF fusion and cross-encoder reranking.
* Per-claim document scoping/folders and physical document viewing/download.
* Agentic query routing (planning, self-correction, synthesis) with a simulation fallback.
* API status check for LM Studio.
* Interactive Chat UI with clickable document citations and a visual pipeline-log visualizer.
* Batch ingestion CLI tool.

### Out of scope
* User login and multi-tenant authentication.
* OCR (Optical Character Recognition) for scanned image PDFs.
* Multi-user concurrent write locks for SQLite database.

---

## Current State

### Confirmed Working
* Ingestion of PDF, DOCX, XLSX, and TXT files (parent/child chunking + FTS5 index).
* SQLite database cascading deletion of vector chunks + FTS cleanup.
* Hybrid retrieval (vector + FTS5 + RRF) with cross-encoder reranking.
* In-memory pre-normalized embedding cache (invalidated on add/delete); verified against `rag_store.db`.
* Per-claim document scoping and physical document serving.
* Agentic router (online LM Studio + simulated) with structured pipeline logs.
* LM Studio API request forwarding and active status monitoring.
* Simulation mode fallback when LLM servers are offline.

### Known Issues
* `CLAIMS_DATA` demo fixtures are duplicated in both `backend/agentic_router.py` and `frontend/app.js` — keep them in sync until unified behind an endpoint (see What's Next).
* `stored_documents/` is not git-ignored; physical seed files would otherwise be committed (see What's Next).

---

## What's Next

1. **User Testing**: Launch the server, open the web dashboard, and test claims questions with Qwen-2.5-14B loaded in LM Studio (exercise the agentic online path end-to-end).
2. **Unify `CLAIMS_DATA`**: Serve the demo claims from a single backend endpoint the frontend consumes, eliminating the duplicated fixtures.
3. **Repo hygiene**: Decide whether `stored_documents/` should be git-ignored (regenerable seed data) or tracked; update `.gitignore` accordingly.
4. **Production Builds**: Package the application or prepare dockerized configs if distribution is desired.

---

## Debugging History

| Date | What Was Tried | What Happened | Root Cause |
|---|---|---|---|
| 2026-07-16 | `uv pip install --system` | Access Denied error in site-packages | Script attempted to write to system Python path without admin privileges. |
| 2026-07-16 | `Test-Path` on `task-69.log` | Returned `False` (file not found) | Output was buffered in Python, preventing log creation. Switched execution to unbuffered python `-u` flag. |
| 2026-07-16 | `test_rag_pipeline.py` run | PackageNotFoundError (DOCX not found) | The test looked for deleted Word guidelines. Modified test to target the actual generated PDF regulations. |
| 2026-07-16 | Import `sentence_transformers` in sandbox | `OSError [WinError 4551]`: Application Control policy blocked `torch/lib/shm.dll` | Environment (agent sandbox) blocks the torch DLL — not a code bug; the ML stack loads on the normal host. Made ML imports lazy and verified the numpy retrieval path standalone against `rag_store.db` without torch. |

---

## Session Log

### 2026-07-16
* **Phase**: Phase 4 — Optimized Vectorization & TXT Support.
* **Attempted**: Created core RAG utilities, FastAPI backend, and glassmorphic UI. Programmed programmatic auto-specific policy PDF generator and batch-indexing script. Vectorized search similarities to matrix multiplications. Overhauled visual styles to match Guidewire ClaimCenter's Jutro light-mode design system. Added drag splitter resizers to all columns.
* **Succeeded**: Indexed 46 documents in 1.47 seconds, verified search results, auto-detected active LM Studio models in the browser, verified text file support, and deployed resizable layouts.
* **New constraints discovered**: Python print stdout buffering can hide background task log creations on Windows; resolved by executing python with `-u` flag.
* **Plan changes**: Switched guidelines focus from property/water claims to auto insurance claims as requested by the user. Removed Ollama integration to focus solely on LM Studio. Added resizable columns.

### 2026-07-16 (session 2 — architecture sync & optimization audit)
* **Phase**: Phase 7 — Performance Hardening (plus documentation sync).
* **Attempted**: Audited the retrieval hot path and agentic router for optimization; brought CLAUDE.md current with the shipped hybrid-search + reranking + agentic-router architecture that earlier sessions had left undocumented.
* **Succeeded**:
  * Replaced the per-query "reload all BLOBs → `vstack` → full-corpus norm" vector step with an in-memory, pre-normalized matrix cache invalidated on add/delete. Verified against the live `rag_store.db` (299 child vectors, rows unit-normalized) and micro-benchmarked ~1.7× faster on the vector stage at current scale (gap widens with corpus size).
  * Fixed the candidate pool cap (`fused_results[:15]`) to `max(15, top_k)` so `top_k > 15` is honored (verified `top_k=20` returns 20).
  * Made `sentence_transformers`/torch imports lazy so the numpy store is importable/testable without the ML stack.
* **New constraints discovered**: Agent sandbox blocks `torch/lib/shm.dll` via Application Control policy — documented in Debugging History. Added cache-invalidation and lazy-import constraints to Critical Constraints.
* **Audit findings deferred (not yet actioned)**: `time.sleep(1.0)` demo delay in the simulated agent; `CLAIMS_DATA` duplicated across backend/frontend; per-query `_get_loaded_model` HTTP round-trip could be cached; `get_document_content` opens sqlite inline instead of via a store method; `stored_documents/` not git-ignored.
* **Plan changes**: Added Build Plan Phases 5–7 (hybrid retrieval/reranking, agentic router, performance hardening) to reflect already-shipped work.

---

## Authoritative Sources

| Service / Library | Documentation URL | Last verified |
|---|---|---|
| FastAPI | https://fastapi.tiangolo.com/ | 2026-07-16 |
| SentenceTransformers | https://www.sbert.net/ | 2026-07-16 |
| Cross-Encoder Reranking | https://www.sbert.net/examples/applications/cross-encoder/README.html | 2026-07-16 |
| SQLite FTS5 | https://www.sqlite.org/fts5.html | 2026-07-16 |
| LM Studio (OpenAI-compatible API) | https://lmstudio.ai/docs/api/openai-api | 2026-07-16 |
| ReportLab PDF | https://www.reportlab.com/docs/reportlab-userguide.pdf | 2026-07-16 |
