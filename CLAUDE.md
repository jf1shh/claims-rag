# AutoClaimsRAG — Engineering Guide

> **This is a living document.** Every session reads it at the start and updates it at the end.
> **GitHub Repository**: [jf1shh/auto-claims-rag](https://github.com/jf1shh/auto-claims-rag)

---

## What This Project Is

AutoClaimsRAG is a high-performance local RAG (Retrieval-Augmented Generation) system designed for auto insurance claims handlers to query reference documentation using natural language. It allows users to search, extract, and draft responses from guidelines, endorsements, state codes, and adjuster reports. The application runs entirely locally: it generates embeddings using a local CPU-based model (`sentence-transformers/all-MiniLM-L6-v2`), indexes them in a local SQLite database, and routes queries to local LM Studio servers with an active fallback simulation mode.

---

## Always Read First

1. This file — architecture, constraints, and current state.
2. [backend/rag_engine.py](file:///C:/PERSONAL/backend/rag_engine.py) — Core document parsing, chunking, embedding, and vectorized vector store similarity.
3. [backend/app.py](file:///C:/PERSONAL/backend/app.py) — FastAPI routing endpoints, LLM status checks, and OpenAI compatibility layers.

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
* **TextChunker** ([backend/rag_engine.py](file:///C:/PERSONAL/backend/rag_engine.py)): Splits document text into overlapping segments of 800 characters with 150 characters overlap.
* **EmbeddingEngine** ([backend/rag_engine.py](file:///C:/PERSONAL/backend/rag_engine.py)): Instantiates `sentence-transformers/all-MiniLM-L6-v2` locally to generate 384-dimensional vector representations.
* **SQLiteVectorStore** ([backend/rag_engine.py](file:///C:/PERSONAL/backend/rag_engine.py)): Saves document records and maps chunk texts to binary embedding BLOBs. Computes cosine similarities using a vectorized `numpy` matrix dot-product.
* **FastAPI Server** ([backend/app.py](file:///C:/PERSONAL/backend/app.py)): Exposes REST endpoints for uploading, listing, deleting documents, querying LLMs, and checking connection statuses.
* **Frontend Web Dashboard** ([frontend/index.html](file:///C:/PERSONAL/frontend/index.html)): HTML/CSS/JS user interface presenting sidebar upload widgets, document lists, claims chat, and real-time RAG pipeline logs.

### File Structure

```
C:\PERSONAL\
├── requirements.txt         ← Python project dependencies
├── rag_store.db             ← SQLite vector database (git-ignored)
├── create_sample_files.py   ← Programmatic auto claims guidelines generator
├── generate_auto_pdfs.py    ← reportlab PDF document generator (15 files)
├── ingest_all.py            ← Script to batch-index all sample documents
├── test_rag_pipeline.py     ← CLI verification script
├── backend/
│   ├── app.py               ← FastAPI REST API
│   └── rag_engine.py        ← Extraction, Embedding, Vector Store
└── frontend/
    ├── index.html           ← Claims Handler Dashboard UI
    ├── style.css            ← Glassmorphic Dark-Mode styling
    └── app.js               ← Dynamic client-side operations
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
* **UTF-8 Robustness**: PDF and TXT text extraction must handle decoding errors gracefully (`errors="ignore"` or similar safeguards) to prevent crashes on non-ASCII symbols.

---

## Locked Design Decisions

| Question | Decision | Reasoning |
|---|---|---|
| Which local embedding model? | `all-MiniLM-L6-v2` | Lightweight (90MB), runs extremely fast on CPU, and achieves high similarity accuracy. |
| Which vector database? | SQLite (`sqlite3` + `numpy`) | Highly portable, serverless, self-contained, and requires no complex C++ binary builds on Windows. |
| LLM Integration Protocol? | OpenAI-compatible `/v1` | Connects to LM Studio (`port 1234`) using standard chat completion endpoints. |

---

## Component / Service Status

| Component | File | Status | Notes |
|---|---|---|---|
| RAG Core Engine | `backend/rag_engine.py` | **Active** | Fully functional. Vectorized search optimized. |
| FastAPI Backend | `backend/app.py` | **Active** | Serving on port 8000. |
| Web Frontend | `frontend/*` | **Active** | UI completed. Periodic LLM polling enabled. |
| Batch Ingest CLI | ingest_all.py | Active | Processes 46 files in 1.47 seconds. |

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

---

## MVP Scope

### In scope
* Offline ingestion and search over PDF, DOCX, XLSX, and TXT guidelines.
* Cosine similarity scoring.
* API status check for LM Studio.
* Interactive Chat UI with document citations and visual log visualizer.
* Batch ingestion CLI tool.

### Out of scope
* User login and multi-tenant authentication.
* OCR (Optical Character Recognition) for scanned image PDFs.
* Multi-user concurrent write locks for SQLite database.

---

## Current State

### Confirmed Working
* Ingestion of PDF, DOCX, XLSX, and TXT files.
* SQLite database cascading deletion of vector chunks.
* Vectorized cosine similarity.
* LM Studio API request forwarding and active status monitoring.
* Simulation mode fallback when LLM servers are offline.

### Known Issues
* None currently identified.

---

## What's Next

1. **User Testing**: Launch the server, open the web dashboard, and test claims questions with Qwen-2.5-14B loaded in LM Studio.
2. **Production Builds**: Package the application or prepare dockerized configs if distribution is desired.

---

## Debugging History

| Date | What Was Tried | What Happened | Root Cause |
|---|---|---|---|
| 2026-07-16 | `uv pip install --system` | Access Denied error in site-packages | Script attempted to write to system Python path without admin privileges. |
| 2026-07-16 | `Test-Path` on `task-69.log` | Returned `False` (file not found) | Output was buffered in Python, preventing log creation. Switched execution to unbuffered python `-u` flag. |
| 2026-07-16 | `test_rag_pipeline.py` run | PackageNotFoundError (DOCX not found) | The test looked for deleted Word guidelines. Modified test to target the actual generated PDF regulations. |

---

## Session Log

### 2026-07-16
* **Phase**: Phase 4 — Optimized Vectorization & TXT Support.
* **Attempted**: Created core RAG utilities, FastAPI backend, and glassmorphic UI. Programmed programmatic auto-specific policy PDF generator and batch-indexing script. Vectorized search similarities to matrix multiplications.
* **Succeeded**: Indexed 15 documents in 0.65 seconds, verified search results, auto-detected active LM Studio models in the browser, and verified text file support.
* **New constraints discovered**: Python print stdout buffering can hide background task log creations on Windows; resolved by executing python with `-u` flag.
* **Plan changes**: Switched guidelines focus from property/water claims to auto insurance claims as requested by the user.

---

## Authoritative Sources

| Service / Library | Documentation URL | Last verified |
|---|---|---|
| FastAPI | https://fastapi.tiangolo.com/ | 2026-07-16 |
| SentenceTransformers | https://www.sbert.net/ | 2026-07-16 |
| ReportLab PDF | https://www.reportlab.com/docs/reportlab-userguide.pdf | 2026-07-16 |
