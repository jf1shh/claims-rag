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
5. [README.md](file:///C:/PERSONAL/README.md) — the portfolio-facing case study (architecture, eval methodology/results, setup). Keep it in sync with this file's Build Plan/Current State when either changes — this file is the working memory, README.md is the public-facing summary of it.

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
* **Never Synthesize With Zero Retrieved Context**: `AgenticRAGRouter._run_online_agent` must not call the LLM for synthesis when `top_matches` is empty after self-correction — this reliably produces a confident answer that fabricates citations to nonexistent filenames (caught by the eval harness). Any new retrieval-skip path (planner misclassification, new claim scoping logic, etc.) must still be caught by the zero-context hard stop before touching synthesis.

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
| Eval Harness | `eval/*` | **Active** | Naive-vs-hybrid+rerank Context Precision/Recall + live Faithfulness, judged locally via LM Studio. Requires backend server running. |

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

### Phase 8 — Domain-Grounded Evaluation Harness (Completed)
* **Goal**: Move from "it looks right" to a measured, repeatable retrieval/groundedness eval, using real adjusting judgment calls instead of generic Q&A.
* **Builds**: `eval/golden_queries.py` (19 domain-grounded queries verified against actual `rag_store.db` content, spanning global policy + claim-scoped + a deliberate hallucination probe), `eval/ragas_lm_studio.py` (Ragas wired to LM Studio as a fully local judge), `eval/run_eval.py` (naive-vs-hybrid+rerank Context Precision/Recall comparison plus live-answer Faithfulness), `/api/eval/search` debug endpoint (`backend/app.py`) exposing a `naive`/`hybrid`/`hybrid_rerank` toggle, and a `use_fts` parameter on `SQLiteVectorStore.search_similarity` to produce a true vector-only baseline.
* **Done when**: The harness runs end-to-end against the live server and produces per-query + aggregate Context Precision/Recall/Faithfulness scores.
* **Status**: Completed. Final scores: Context Precision naive=0.735/hybrid=0.772, Context Recall naive=0.884/hybrid=0.902, Faithfulness=0.811. See Debugging History for the ragas/langchain-community/instructor packaging incompatibility this surfaced. It also caught and led to fixing a zero-context hallucination path in the agentic router, ungrounded facts in the simulated demo mode, and a measurement gap in the harness's own Faithfulness scoring — all three documented in the session 3 log entry.

### Phase 9 — Guaranteed Claim-Dossier Retrieval (Completed)
* **Goal**: Fix the multi-hop retrieval gap the eval harness identified — claim-scoped multi-fact queries (e.g. "does this claim's total exceed the endorsement cap") need multiple documents surfaced together, and single-shot semantic search wasn't guaranteeing that.
* **Builds**: `SQLiteVectorStore.get_claim_chunks()` (`backend/rag_engine.py`) — returns every chunk belonging to a claim's own documents directly, unranked, bypassing semantic competition against global policy documents. `AgenticRAGRouter._run_online_agent` now always includes a claim's own chunks when `claim_id` is set (no longer gated on the planner's unreliable `needs_claim_dossier` flag), sorts and caps *global* matches at 4 separately, then prepends the guaranteed claim chunks. Self-correction now only triggers when both are empty. Added a system-prompt instruction to enumerate every line item before totaling, after confirming retrieval alone wasn't sufficient — the model had the correct $2,400 + $3,500 = $5,900 receipt in context and still calculated from one line item only.
* **Done when**: The canonical failing query (`chen-custom-equipment-cap`, "$5,900 total vs $3,500 cap") produces a correct, fully-grounded answer via the real online agentic path, verified by direct before/after comparison.
* **Status**: Completed and verified. Before: retrieval never surfaced `custom_equipment_receipts_Chen.xlsx`; the LLM fabricated a plausible-but-wrong $3,500 figure and concluded "fully covered." After the retrieval fix: the receipt was retrieved, but the LLM still only reasoned about one line item (fully *faithful* to what it received, but *incomplete* — see Debugging History for why this passed the existing Faithfulness metric despite being wrong). After the prompt fix: correctly enumerates both items, totals $5,900, and concludes the $3,500 cap is exceeded by $2,400. Also fixed `sterling-shop-estimate-detail` as a side effect (previously missed `shop_email_thread_Sterling.pdf` entirely in both naive/hybrid single-shot retrieval). Spot-checked for regressions on 3 other queries; no full eval rerun performed since Context Precision/Recall are unaffected (different endpoint) and Faithfulness alone doesn't capture the correctness improvement (see What's Next).

### Phase 10 — Full Security/Optimization/Accuracy Audit (Completed)
* **Goal**: Direct, systematic audit of the whole codebase (not a diff review) for security, performance, and correctness defects.
* **Found and fixed, security**:
  * **Critical — arbitrary file write/delete via unsanitized `filename`.** `add_document()`/`delete_document()` (`rag_engine.py`) joined a client-supplied filename directly into a filesystem path with no sanitization; `POST /api/delete` took it straight from a JSON body with no URL-routing restrictions at all, so `{"filename": "../../../x"}` could delete an arbitrary file. Fixed with `safe_filename()` (`os.path.basename`, rejects empty/`.`/`..`), applied in `add_document`, `delete_document`, and `download_document` (`app.py`).
  * **Critical — SSRF + data exfiltration via the `engine` field.** `engine_url = ... else engine` used any non-`"lm-studio"` value directly as a request URL, POSTing the full retrieved context (claim/policy text) to it. Fixed: `run_query` now only accepts `"simulated"` or `"lm-studio"`, rejecting anything else with a clean error instead of treating it as a target.
  * **High — stored XSS via document filenames**, two variants in `frontend/app.js`: `doc.filename` inserted into `innerHTML` completely unescaped (executes on list render), and an `onclick="viewSource('${filenameSafe}', ...)"` pattern where `escapeHtml()` wasn't sufficient — HTML-entity-escaped quotes decode back to `'` by the browser's HTML parser *before* reaching the JS engine, so single-quote breakout from the inline event-handler string still worked. Fixed by rebuilding `renderDocuments`, `renderClaimDocuments`, and `renderSources` to set filenames via `.textContent`/`.title` (DOM properties, never HTML-parsed) and attach handlers via `addEventListener` with real JS values — no string interpolation into HTML or inline JS at all.
* **Found and fixed, accuracy**:
  * **`TextChunker` duplicated the tail of nearly every document.** When the final chunk's `end` already reached `text_len`, the loop's "prevent infinite loop" fallback ran unconditionally anyway and appended an overlapping duplicate of the tail just added. Confirmed empirically pre-fix: a ~350-char document had 2 parent chunks (full text, then its own last ~200 chars again). Fixed by breaking immediately once a chunk reaches `text_len`, with a separate no-forward-progress guard for the genuine infinite-loop case.
  * **~42 of 46 global `stored_documents/` files were plain-text dumps with a fake `.pdf`/`.docx`/`.xlsx` extension**, not real binaries — `ingest_all.py`'s global-document loop never passed `file_path` to `add_document()`, so it silently fell into the "write extracted text as the file" fallback despite valid generated binaries existing in `sample_guidelines/`. Broke download/view for those documents entirely (a real PDF/Office viewer would reject the file). Root-caused, fixed the missing argument, then safely rebuilt via a temporary maintenance endpoint that reused the running server's loaded embedding model (removed immediately after one use) rather than a destructive `ingest_all.py` run from my shell, which would have dropped tables and lost claim-scoped documents that don't live in `sample_guidelines/`. Verified: 0/22 PDFs, 0/15 DOCX, 0/11 XLSX fake afterward (was 16/15/10); child chunk count dropped 299→243 confirming the chunker fix applied too.
* **Found and fixed, optimization**: `_get_loaded_model()` made a synchronous HTTP round-trip to LM Studio on every single chat request. Added a 10-second TTL cache on `AgenticRAGRouter` (failures aren't cached, so a temporarily-unreachable LM Studio doesn't get stuck showing "local-model" for the full TTL).
* **Verification note**: the Browser pane's preview tab cached `index.html`/`app.js` from earlier in this same session and kept serving stale code through repeated force-navigates, new tabs, and even a `fetch(..., {cache:'no-store'})` re-exec — confirmed via direct server-response inspection (both from my shell and from the browser's own `fetch()`) that the *served* files were correct throughout; the staleness was entirely in that one long-lived preview session, not the app. Bumped `app.js?v=1.0.1` → `v=1.0.2` in `index.html` as the actual fix (this app already versions its script tag for cache-busting; the version just hadn't been bumped for this change) — a fresh visitor was never affected.
* **Status**: All 7 findings fixed and verified (unit tests for `safe_filename`/`TextChunker`, live SSRF-rejection test, live multi-hop regression test, file-integrity re-check, chunk-count re-check).

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
* `eval/results.json`'s Context Precision/Recall numbers reflect single-shot retrieval via `/api/eval/search` only — they do **not** include the guaranteed claim-chunk inclusion described below, since that's a property of the full agentic pipeline (`/api/chat`), not of raw `search_similarity`. Re-scoring those two metrics against the real pipeline is unscoped work, not a bug; the eval harness deliberately isolates retrieval-strategy comparison from full-pipeline behavior.
* Faithfulness (0.811) is judged by a single local 14B model — a real tradeoff vs. a larger hosted judge, made deliberately to keep evaluation consistent with the "runs entirely locally" constraint. Documented as a limitation in README.md rather than treated as a defect.
* **`eval/results.json` and the README's reported numbers now predate the Phase 10 audit's corpus rebuild** (child chunk count went 299→243 after the `TextChunker` duplicate-tail fix, and ~42 documents' physical files were corrected from fake-text-with-a-real-extension to actual binaries). Extracted text content should be very similar, but the eval suite hasn't been rerun against the corrected corpus — the reported scores are not guaranteed to still be exactly accurate. See What's Next.

---

## What's Next

1. **Rerun the eval harness against the rebuilt corpus** (Phase 10 fixed `TextChunker`'s duplicate-tail bug and ~42 documents' fake binaries — chunk boundaries and physical files both changed) and update `eval/results.json` + README with fresh numbers.
2. **Quantify the multi-hop fix in the eval harness**: add an answer-correctness metric (Ragas has options beyond Faithfulness/Context Precision/Recall) so the `chen-custom-equipment-cap`-style improvement shows up as a number, not just a manually-verified before/after. Faithfulness alone didn't capture this fix's value — see Debugging History.
3. **User Testing**: Launch the server, open the web dashboard, and test claims questions with Qwen-2.5-14B loaded in LM Studio (exercise the agentic online path end-to-end).
4. **Unify `CLAIMS_DATA`**: Serve the demo claims from a single backend endpoint the frontend consumes, eliminating the duplicated fixtures.
5. **Production Builds**: Package the application or prepare dockerized configs if distribution is desired.

---

## Debugging History

| Date | What Was Tried | What Happened | Root Cause |
|---|---|---|---|
| 2026-07-16 | `uv pip install --system` | Access Denied error in site-packages | Script attempted to write to system Python path without admin privileges. |
| 2026-07-16 | `Test-Path` on `task-69.log` | Returned `False` (file not found) | Output was buffered in Python, preventing log creation. Switched execution to unbuffered python `-u` flag. |
| 2026-07-16 | `test_rag_pipeline.py` run | PackageNotFoundError (DOCX not found) | The test looked for deleted Word guidelines. Modified test to target the actual generated PDF regulations. |
| 2026-07-16 | Import `sentence_transformers` in sandbox | `OSError [WinError 4551]`: Application Control policy blocked `torch/lib/shm.dll` | Environment (agent sandbox) blocks the torch DLL — not a code bug; the ML stack loads on the normal host. Made ML imports lazy and verified the numpy retrieval path standalone against `rag_store.db` without torch. |
| 2026-07-17 | `import ragas` (v0.4.3) | `ModuleNotFoundError: No module named 'langchain_community.chat_models.vertexai'` | ragas 0.4.3 unconditionally imports `ChatVertexAI` from a module langchain-community removed when VertexAI support was split into a standalone package. Never actually used (only listed in an isinstance-check tuple). Fixed with a `sys.modules` shim injected before importing ragas (`eval/ragas_lm_studio.py`) — not a site-packages edit, so it survives a fresh `pip install`. Downgrading langchain-community instead was tried first and broke langchain-core/langgraph/langchain-openai version compatibility; reverted. |
| 2026-07-17 | Ragas `Faithfulness`/`ContextPrecision`/`ContextRecall` against LM Studio | `openai.BadRequestError: 'response_format.type' must be 'json_schema' or 'text'` | `ragas.llms.llm_factory(provider="openai")` hardcodes `instructor.Mode.JSON` (plain `json_object`), which LM Studio's endpoint rejects. Fixed by bypassing `llm_factory` and constructing the `InstructorLLM` directly with `instructor.Mode.JSON_SCHEMA`. |
| 2026-07-17 | Ragas `Faithfulness` scoring on claim-scoped queries | 4/19 queries: `InstructorRetryException: output incomplete due to max_tokens limit` | `InstructorModelArgs` defaults to `max_tokens=1024`, too small for the judge to enumerate every atomic statement + verdict on longer claim-scoped contexts. Fixed by passing `InstructorModelArgs(max_tokens=4096)` in `eval/ragas_lm_studio.py`. |
| 2026-07-17 | Global-policy eval query (`rear-impact-adas-fee`) returned `sources: []` from `/api/chat` | LLM produced a confident, well-formatted answer citing filenames that don't exist anywhere in the corpus | Real bug in `AgenticRAGRouter._run_online_agent`: the planner classified the query as `needs_global_policies=False, needs_claim_dossier=True`; since `claim_id` was `None`, the dossier branch's `plan["needs_claim_dossier"] and claim_id` guard also evaluated falsy, so **both** retrieval branches were skipped and synthesis ran with zero context. The existing self-correction fallback only triggered `if not all_matches and claim_id` — global-only queries had no recovery path. Fixed: added a fallback branch for the `claim_id is None` case (retries the original query against global policies), and added a hard stop that refuses to call the LLM at all if `top_matches` is still empty after self-correction, returning a "couldn't find supporting documents" message instead of letting the model fabricate one. |
| 2026-07-17 | Edited `backend/agentic_router.py` 4-5 times in quick succession while `uvicorn --reload` was running; queried `/api/chat` afterward | Response still reflected pre-edit text/response shape (missing a newly-added `claim_dossier` field) despite log lines reading `StatReload detected changes ... Reloading...` | StatReload's file-watcher reload is unreliable under rapid successive saves to the same file on this Windows setup — it logs a reload but the running worker process doesn't actually pick up the latest content. Don't trust hot-reload after several fast edits; stop and restart the preview server (`preview_stop` + `preview_start`) and confirm via a live request before relying on the response shape. |
| 2026-07-17 | Fixed multi-hop retrieval for `chen-custom-equipment-cap`; verified with direct before/after `/api/chat` calls rather than the eval harness | Faithfulness for this query was already 1.0 *before* the fix, on a factually wrong answer (LLM ignored the $2,400 wheels line item, calculated only from the $3,500 infotainment console, concluded "fully covered, no excess" — actual excess is $2,400) | **Faithfulness measures groundedness, not correctness.** The wrong answer was fully grounded in a real, retrieved line item (`CLAIMS_DATA`'s dossier already listed the $3,500 infotainment repair) — it just ignored a second, equally relevant line item that was also present in context. A judge scoring "is every statement supported by the context" correctly says yes; it doesn't check "did you use *all* the relevant context." Don't infer answer correctness from a high Faithfulness score on multi-fact queries — verify the actual conclusion directly. Recorded as a `What's Next` item: add an answer-correctness metric. |
| 2026-07-17 | Re-uploaded documents read from `stored_documents/` via `/api/upload` to test a fix, expecting real PDF/DOCX/XLSX | ~80% failed: `pypdf`/`docx`/`pandas` all rejected the files as corrupt/unrecognized | Not a bug in the upload code -- ~42 of 46 global `stored_documents/` files were never real binaries. `ingest_all.py`'s global-document loop didn't pass `file_path` to `add_document()`, so it silently fell into the "write extracted text as the file" fallback despite valid binaries existing in `sample_guidelines/`. Root-caused via `head -c 5` header checks (`%PDF-`/`PK` signatures) rather than assuming the re-upload script was broken. Fixed the missing argument, then rebuilt via a temporary maintenance endpoint (removed after one use) that reused the running server's already-loaded embedding model, rather than running `ingest_all.py` directly, which drops all tables and would have destroyed claim-scoped documents that live outside `sample_guidelines/`. |
| 2026-07-17 | Verified a frontend XSS fix by clicking through the live app in the Browser pane; DOM inspection kept showing the old vulnerable `onclick` attribute after the edit | Direct server-response checks (both from the shell and from the browser's own `fetch(..., {cache:'no-store'})`) confirmed the served `app.js` was correct throughout; force-navigate, a brand-new tab, and re-executing the freshly-fetched script in-page all still showed stale behavior | The preview tab's cache for this origin was pinned from earlier in the same long session and didn't respect normal cache-busting signals (force-navigate, new tab, explicit no-store fetch). Root cause: `index.html` versions its script tag (`app.js?v=X`) for cache-busting, and the version hadn't been bumped for this change -- bumping it (`v=1.0.1`→`v=1.0.2`) is the actual fix; a fresh visitor was never affected. When browser-side verification disagrees with direct server-response verification, trust the server response and suspect the browser tool's cache before assuming the code is wrong. |

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

### 2026-07-17 (session 3 — portfolio pivot, resume/LinkedIn, eval harness)
* **Phase**: Phase 8 — Domain-Grounded Evaluation Harness.
* **Context established**: This project's real purpose is a portfolio piece (user has 17 years as an auto insurance claims/appraisal handler). Committed the prior session's uncommitted work as 3 scoped commits first (`9f4fd1b` agentic router + hybrid retrieval + perf, `45cfeae` CLAUDE.md sync, `11797c4` gitignore/launch config).
* **Attempted**: Built a domain-grounded eval harness instead of generic Q&A — `eval/golden_queries.py` (19 queries verified against actual `rag_store.db` content: global policy lookups, claim-scoped multi-hop questions, one deliberate hallucination probe), `eval/ragas_lm_studio.py` (Ragas wired to LM Studio as a fully local judge), `eval/run_eval.py` (naive-vs-hybrid+rerank Context Precision/Recall, plus live-answer Faithfulness via `/api/chat`). Added `/api/eval/search` debug endpoint and a `use_fts` toggle on `search_similarity` to produce a true naive baseline.
* **Succeeded**:
  * Got Ragas 0.4.3 working against LM Studio despite two real packaging/compatibility bugs (see Debugging History) — verified against the actually-installed package API, not the docs, which described a different metrics surface than what's actually importable.
  * Ran the full 19-query suite three times across this session as fixes landed. Final numbers: Context Precision naive=0.735 / hybrid+rerank=0.772; Context Recall naive=0.884 / hybrid+rerank=0.902; Faithfulness=**0.811** (all 19/19 queries scored, no drops).
  * Found and fixed a real production bug via the eval harness: `AgenticRAGRouter` could skip retrieval entirely (planner misclassification + `claim_id=None` short-circuiting the dossier branch) and let the LLM synthesize with zero context, which reliably fabricated citations to nonexistent filenames. Added a fallback path for the `claim_id is None` case and a hard stop that refuses synthesis when no context was retrieved.
  * Found **and fixed** the simulated agent's hardcoded narrative facts unsupported by the actual seed documents — Custom Equipment cap ($5,000→$3,500), OEM Parts Guarantee threshold (3yr→5yr), a fabricated rear-impact frame-pull hour cap (replaced with the real ADAS recalibration fee requirement), the Jenkins hail/PDR "duplicative charge" mischaracterization (rewritten to match the real case-study precedent, total corrected $4,750→$6,800/$6,300 net), and a fabricated DUI citation for Rostova with zero supporting document (removed; consequential-damage exclusion alone is sufficient grounds and was already correct).
  * Found **and fixed** the eval harness's own dossier-scoring gap: `/api/chat` now returns a `claim_dossier` field (all three `_run_online_agent`/`_run_simulated_agent` return paths) alongside `sources`, and `eval/run_eval.py` scores Faithfulness against both. Verified the fix targeted the right thing: every claim-scoped query's Faithfulness score improved (e.g. `chen-custom-equipment-cap` 0.364→1.0, `sterling-oem-eligibility` 0.818→1.0) while global-only queries stayed flat.
  * Identified a genuine multi-hop retrieval failure (`chen-custom-equipment-cap`, 0.0/0.0 both modes) that motivates the agentic planner's existence rather than undermining it — left as a documented limitation, not fixed this session.
  * Wrote `README.md` as the portfolio-facing case study: architecture, hybrid-search rationale, honest eval methodology and results (including the three bugs found above, framed as evidence the eval harness does real work), local setup instructions, known limitations.
* **New constraints discovered**: `ragas==0.4.3` requires a `sys.modules` shim for a `langchain_community.chat_models.vertexai` import it doesn't actually use, and must bypass `llm_factory`'s hardcoded `instructor.Mode.JSON` (use `JSON_SCHEMA` for LM Studio compatibility) with `max_tokens` raised well above the library default of 1024. `uvicorn --reload` is unreliable after several rapid successive saves to the same file on this setup — see Debugging History; restart the preview server and verify live rather than trust the reload log line.
* **Plan changes**: Added Build Plan Phase 8. Prioritized a domain-grounded eval over generic Q&A and over building out a full "claims system" (multi-user auth, financial ledger) — the latter direction was explicitly deprioritized once the portfolio purpose was established, since it doesn't showcase RAG/AI skill.
* **Follow-up (same session)**: Built a portfolio-facing evaluation chart (`assets/eval_results.png`, embedded in README.md), and fixed the multi-hop retrieval gap (Build Plan Phase 9): `SQLiteVectorStore.get_claim_chunks()` guarantees a claim's own documents are always included rather than competing semantically against global policy docs, and a system-prompt instruction fixed a separate model-reasoning gap the retrieval fix exposed (the LLM had the correct multi-line-item receipt in context and still only reasoned about one line item). Verified via direct before/after `/api/chat` comparison, not the eval harness — Faithfulness doesn't capture this class of error (see Debugging History). Discovered and recorded the general principle: Faithfulness measures groundedness, not correctness.
* **Follow-up (same session) — full security/optimization/accuracy audit (Build Plan Phase 10)**: Direct systematic pass across the whole codebase (not a diff review, per user request to "audit and examine all code"). Found and fixed 7 real issues: 2 critical security (arbitrary file write/delete via unsanitized filenames; SSRF + context exfiltration via the `engine` field), 1 high security (2 stored-XSS variants in the frontend, one of which survived `escapeHtml()` due to HTML-entity-decode-before-JS-parse), 2 accuracy (`TextChunker`'s duplicate-tail-chunk bug, corpus-wide; ~42 of 46 global documents in `stored_documents/` were plain-text dumps with a fake binary extension due to a missing `file_path` argument in `ingest_all.py`), and 1 optimization (`_get_loaded_model` HTTP round-trip on every chat request, now TTL-cached). Safely rebuilt the corpus (real binaries + clean chunking) via a temporary maintenance endpoint reusing the running server's embedding model, removed immediately after one use, rather than a destructive direct `ingest_all.py` run that would have dropped claim-scoped documents. Lost significant time to a Browser-pane tab-caching artifact while verifying the XSS fix visually (see Debugging History) before recognizing it as tooling, not application, and resolving it by bumping the app's own cache-busting script version.
* **New constraints discovered**: Frontend event handlers must never be built via string-interpolated inline `onclick="..."` attributes — `escapeHtml()` alone doesn't survive the HTML-attribute-then-inline-JS double-parse. Use `addEventListener` with real JS values (or `data-*` + `.dataset`) instead. Any filename touching a filesystem path must go through `safe_filename()` first. `run_query`'s `engine` parameter must stay a strict allowlist (`"simulated"` / `"lm-studio"`), never used as a request URL. `ingest_all.py`'s global-document loop must pass `file_path` to `add_document()`. Browser-pane preview tabs can retain stale cached assets across a long session independent of normal cache-busting signals — verify via direct server-response inspection when browser-side checks disagree.
* **Plan changes**: Added Build Plan Phase 10. `eval/results.json` and README's reported numbers now predate this session's corpus rebuild (chunk count and physical files both changed) — flagged in Known Issues/What's Next as needing a rerun, not silently updated without re-running the actual harness.

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
| Ragas | https://docs.ragas.io/en/stable/ | 2026-07-17 — docs describe an older/newer API shape than the installed 0.4.3; verify against the actually-installed package (`ragas.metrics.collections`, not the top-level `ragas.metrics` names) before trusting the docs literally. |
| Instructor (structured LLM output) | https://python.useinstructor.com/ | 2026-07-17 |
