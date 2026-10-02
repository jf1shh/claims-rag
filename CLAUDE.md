# ClaimsRAG — Engineering Guide

> **This is a living document.** Every session reads it at the start and updates it at the end.
> **GitHub Repository**: [jf1shh/claims-rag](https://github.com/jf1shh/claims-rag)

---

## What This Project Is

ClaimsRAG is a high-performance local RAG (Retrieval-Augmented Generation) system designed for auto insurance claims handlers to query reference documentation using natural language. It allows users to search, extract, and draft responses from guidelines, endorsements, state codes, and adjuster reports. The application runs entirely locally: it generates embeddings using a local CPU-based model (`sentence-transformers/all-MiniLM-L6-v2`), indexes them in a local SQLite database, and answers queries through a **stateful agentic RAG router** that plans, retrieves, self-corrects, and synthesizes.

Retrieval is **hybrid**: dense vector search over child chunks is fused with FTS5 keyword search over parent chunks via Reciprocal Rank Fusion (RRF), then re-ordered by a local cross-encoder reranker. Documents can be scoped globally or attached to a specific claim folder. Generation is routed to a local LM Studio server (OpenAI-compatible `/v1`) with a high-fidelity rule-based simulation fallback when no LLM is loaded.

---
---

## Always Read First

Read `IDENTITY.md` and `CONTEXT.md` before exploring the repository. They are the ICM navigation layer and route to the foundation spec, implementation plan, stage contracts, and verification commands; they supplement this guide rather than replace it.

1. [docs/current-state.md](docs/current-state.md) — build plan, Current State, Known Issues, What's Next. **Read at session start, update at session end.**
2. [docs/architecture.md](docs/architecture.md) — components, file structure, locked design decisions, component status. Read before touching a component.
3. [docs/build-history.md](docs/build-history.md) — phases 1-18, Debugging History, full session log. Not loaded by default; append at session end.
4. [README.md](README.md) — portfolio-facing case study. Keep it in sync with Current State.

Key code: [backend/rag_engine.py](backend/rag_engine.py) (parse, chunk, embed, rerank, hybrid store), [backend/agentic_router.py](backend/agentic_router.py) (plan/retrieve/synthesize + simulation), [backend/app.py](backend/app.py) (FastAPI endpoints).

## Critical Constraints

* **100% Local Ingestion**: No document contents or raw vector embeddings may be sent to external cloud vector databases. All vector searches run on the host CPU/GPU locally.
* Detailed constraints load by path: [.claude/rules/backend-constraints.md](.claude/rules/backend-constraints.md) (backend, tests, eval, ingest) and [.claude/rules/frontend-constraints.md](.claude/rules/frontend-constraints.md) (frontend). Tools that don't read `.claude/rules/` (Codex) must open them before editing those paths.
* API contract tests must explicitly isolate model dependencies: a router stub alone does not prevent argument evaluation from loading an embedding model. The PR suite runs with model downloads disabled; tests must also pass with an empty model cache.

## Session Protocol — Mandatory

This document is the project's memory. Every session must follow this protocol exactly.

### Before starting any task
1. Read this file, then `docs/current-state.md`.
2. Check **Current State** (in `docs/current-state.md`) — confirms what's working, what's broken, what's next.
3. Check **Debugging History** in `docs/build-history.md` for the area you're working in. If an approach has already failed, do not repeat it.
4. For any external API or service: fetch live documentation before writing a single line. Never rely on training data for API contracts.
5. State today's goal in one sentence. Do not begin until that goal is confirmed.

### During the session
6. Stay on the current Build Plan phase. Do not drift to other work without an explicit decision to change.
7. When an attempt fails, record it in Debugging History (`docs/build-history.md`) immediately — what was tried, what happened, root cause if known.
8. When an attempt succeeds, note it so Current State can be updated.
9. If you discover a constraint not yet documented, add it to the matching `.claude/rules/` file (or Critical Constraints above).
10. Review proposed changes before accepting. Do not accept changes to out-of-scope components.

### At the end of every session
11. Run the self-assessment: update **Current State**, **Current Phase**, and **What's Next** in `docs/current-state.md`; append the session's Debugging History and Session Log entries to `docs/build-history.md`.
12. If new architectural decisions were made, add them to Locked Design Decisions with reasoning.
13. If a new API or component was successfully integrated, document its working contract in `docs/architecture.md`.

**The goal:** no session ever repeats a failure a previous session diagnosed.
The project moves forward along the plan — never sideways or backwards.

---

## Running the Project

Ensure the virtual environment is used to run all python scripts. The project runs
on Linux (self-hosted CI, all dev sessions since 2026-08-29) -- use `.venv/bin/`,
not `.venv\Scripts\`.

```bash
# 1. Generate synthetic seed guidelines (PDF + DOCX/XLSX/TXT) and ingest them
.venv/bin/python generate_auto_pdfs.py
.venv/bin/python create_sample_files.py
.venv/bin/python ingest_all.py

# 2. Start the FastAPI backend server (serves the frontend too, port 8000)
.venv/bin/python -m uvicorn backend.app:app --reload --port 8000
# -> http://localhost:8000

# 3. Verify
.venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/run_foundation_gates.py --mode gate
```

Or in Docker: `docker compose up --build` (see `docs/operations/local-and-production.md`).
