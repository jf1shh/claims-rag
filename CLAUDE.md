# AutoClaimsRAG — Engineering Guide

> **This is a living document.** Every session reads it at the start and updates it at the end.
> **GitHub Repository**: [jf1shh/auto-claims-rag](https://github.com/jf1shh/auto-claims-rag)

---

## What This Project Is

AutoClaimsRAG is a high-performance local RAG (Retrieval-Augmented Generation) system designed for auto insurance claims handlers to query reference documentation using natural language. It allows users to search, extract, and draft responses from guidelines, endorsements, state codes, and adjuster reports. The application runs entirely locally: it generates embeddings using a local CPU-based model (`sentence-transformers/all-MiniLM-L6-v2`), indexes them in a local SQLite database, and answers queries through a **stateful agentic RAG router** that plans, retrieves, self-corrects, and synthesizes.

Retrieval is **hybrid**: dense vector search over child chunks is fused with FTS5 keyword search over parent chunks via Reciprocal Rank Fusion (RRF), then re-ordered by a local cross-encoder reranker. Documents can be scoped globally or attached to a specific claim folder. Generation is routed to a local LM Studio server (OpenAI-compatible `/v1`) with a high-fidelity rule-based simulation fallback when no LLM is loaded.

---

## Always Read First

Read `IDENTITY.md` and `CONTEXT.md` before exploring the repository. They provide the ICM navigation layer and route to the approved foundation spec, implementation plan, stage contracts, and verification commands; they supplement this engineering guide rather than replacing it.


1. This file — architecture, constraints, and current state.
2. [backend/rag_engine.py](backend/rag_engine.py) — Document parsing, parent/child chunking, embedding, cross-encoder reranking, and the hybrid (vector + FTS5 + RRF) vector store with an in-memory normalized embedding cache.
3. [backend/agentic_router.py](backend/agentic_router.py) — Stateful agentic RAG router: query planning/decomposition, multi-sub-query retrieval, self-correction fallback, LLM synthesis, and the rule-based simulation engine (with demo `CLAIMS_DATA`).
4. [backend/api.py](backend/api.py) — Factory-bound FastAPI routing endpoints, per-claim document scoping, physical file serving, LLM status checks, and OpenAI compatibility layers.
5. [docs/build-history.md](docs/build-history.md) — completed phases 1-18, Debugging History, and the full 36-session log. Not loaded by default; consult it when you need prior context, and append to it at session end.
6. [README.md](README.md) — the portfolio-facing case study (architecture, eval methodology/results, setup). Keep it in sync with this file's Build Plan/Current State when either changes — this file is the working memory, README.md is the public-facing summary of it.

---

API contract tests must explicitly isolate model dependencies: a router stub alone does not prevent argument evaluation from loading an embedding model. The PR suite runs with model downloads disabled; tests must also pass with an empty model cache.

## Session Protocol — Mandatory

This document is the project's memory. Every session must follow this protocol exactly.

### Before starting any task
1. Read this entire CLAUDE.md. Do not skip sections.
2. Check **Current State** — confirms what's working, what's broken, what's next.
3. Check **Debugging History** in `docs/build-history.md` for the area you're working in. If an approach has already failed, do not repeat it.
4. For any external API or service: fetch live documentation before writing a single line. Never rely on training data for API contracts.
5. State today's goal in one sentence. Do not begin until that goal is confirmed.

### During the session
6. Stay on the current Build Plan phase. Do not drift to other work without an explicit decision to change.
7. When an attempt fails, record it in Debugging History (`docs/build-history.md`) immediately — what was tried, what happened, root cause if known.
8. When an attempt succeeds, note it so Current State can be updated.
9. If you discover a constraint not yet documented, add it to Critical Constraints.
10. Review proposed changes before accepting. Do not accept changes to out-of-scope components.

### At the end of every session
11. Run the self-assessment: update **Current State**, **Current Phase**, and **What's Next** here; append the session's Debugging History and Session Log entries to `docs/build-history.md`.
12. If new architectural decisions were made, add them to Locked Design Decisions with reasoning.
13. If a new API or component was successfully integrated, document its working contract here.

**The goal:** no session ever repeats a failure a previous session diagnosed.
The project moves forward along the plan — never sideways or backwards.

---

## Architecture Overview

### Components

* **DocumentParser** ([backend/rag_engine.py](backend/rag_engine.py)): Extracts text from PDF (`pypdf`), DOCX (`python-docx`), Excel (`pandas`/`openpyxl`), and TXT files.
* **TextChunker** ([backend/rag_engine.py](backend/rag_engine.py)): Hierarchical parent/child chunking. Parent chunks (~1200 chars / 200 overlap) preserve context and back the FTS5 index; child chunks (~250 chars / 50 overlap) are embedded for precise vector matching. Boundaries snap to whitespace.
* **EmbeddingEngine** ([backend/rag_engine.py](backend/rag_engine.py)): Instantiates `sentence-transformers/all-MiniLM-L6-v2` locally to generate 384-dimensional vectors. Imported lazily so the vector store can be used without loading torch.
* **Reranker** ([backend/reranker.py](backend/reranker.py)): Provider-neutral cross-encoder seam. `LocalReranker` preserves the lazy CPU implementation; `RemoteReranker` sends a bounded candidate pool to a dedicated GPU HTTP service; `FallbackReranker` fails open to local.
* **SQLiteVectorStore** ([backend/rag_engine.py](backend/rag_engine.py)): Stores documents, parent/child chunks, embedding BLOBs, and an FTS5 index. `search_similarity` runs **hybrid retrieval**: vectorized cosine over child chunks (mapped up to best parent) + FTS5 keyword search, fused via RRF, then cross-encoder reranked. Embeddings are held in an **in-memory, pre-normalized matrix cache** (built lazily, invalidated on add/delete) so queries avoid re-reading BLOBs and recomputing corpus norms.
* **AgenticRAGRouter** ([backend/agentic_router.py](backend/agentic_router.py)): Orchestrates each query. Online mode asks the LLM for a JSON plan (which stores to search + sub-queries), retrieves per sub-query, self-corrects on empty results, and synthesizes a grounded answer. Simulated mode runs local retrieval plus a Python rule engine keyed to demo claims/audit types. Emits step-by-step `pipeline_logs`.
* **FastAPI Server** ([backend/app.py](backend/app.py)): Exposes REST endpoints for global + per-claim upload, listing, deletion, document content/download, agentic chat, and LLM connection status.
* **Frontend Web Dashboard** ([frontend/index.html](frontend/index.html)): HTML/CSS/JS UI with a claims queue, per-claim document folders, claims chat, clickable/viewable document citations, and real-time RAG pipeline logs.

### File Structure

```
auto-claims-rag/
├── requirements.txt            ← Python project dependencies
├── config.py / app_factory.py  ← Typed settings + dependency-injected app factory (Phase 0)
├── Dockerfile / docker-compose.yml  ← Self-contained container image (Phase 6 infra)
├── rag_store.db                ← SQLite vector database (git-ignored)
├── create_sample_files.py      ← Programmatic DOCX/XLSX/TXT guidelines generator
├── generate_auto_pdfs.py       ← reportlab PDF document generator
├── ingest_all.py               ← Script to batch-index all sample documents
├── sample_guidelines/          ← Generated source documents (git-ignored)
├── stored_documents/           ← Physical copies served to the UI (viewer/download)
├── assets/
│   └── eval_results.png        ← Portfolio-facing eval chart (regenerate via eval/plot_results.py)
├── eval/
│   ├── golden_queries.py       ← 19 domain-grounded queries + verified reference answers
│   ├── ragas_lm_studio.py      ← Wires Ragas to LM Studio as a local judge
│   ├── run_eval.py             ← Runs the full suite, writes eval/results.json
│   └── plot_results.py         ← Regenerates assets/eval_results.png from results.json
├── backend/
│   ├── app.py                  ← FastAPI REST API + per-claim endpoints
│   ├── rag_engine.py           ← Parsing, chunking, embedding, reranking, hybrid store
│   ├── agentic_router.py       ← Agentic planner/retriever/synthesizer + simulation engine
│   ├── reranker.py             ← Local/remote provider-neutral cross-encoder reranking seam
│   ├── postgres_store.py       ← Postgres + pgvector backend behind the VectorStore interface (Phase 1)
│   ├── blob_store.py           ← DocumentBlobStore ABC: local + S3 adapters (Phase 2)
│   ├── queue.py                ← Queue ABC: in-process + SQS adapters, DLQ (Phase 3)
│   ├── s3_events.py            ← S3 put-event → queue bridge, tenant-validated (Phase 3)
│   ├── ingestion_worker.py     ← Async worker: blob → parse → embed → upsert, retry/DLQ (Phase 3)
│   ├── ingestion.py            ← IngestionService job contract seam (Phase 0)
│   ├── authn.py                ← Authenticator ABC: dev / OIDC (JWT+JWKS) / service accounts / chain (Phase 4.1)
│   ├── rbac.py                 ← role→permission matrix + ClaimAccessPolicy claim ACLs (Phase 4.2)
│   ├── audit.py                ← append-only JsonlAuditSink + read_events() (Phase 4.3)
│   ├── rate_limit.py           ← per-principal sliding-window request limiter (Phase 4.4)
│   ├── llm_client.py           ← ChatClient seam: OpenAICompatibleClient + per-stage model routing (Phase 5.1); complete_stream() SSE token streaming (Phase 5.3)
│   ├── health.py / audit.py / contracts.py / tenant_context.py / harness.py  ← Phase 0 foundation
│   ├── answer_guard.py         ← post-generation guard: unsupported claim outcomes / off-allowlist contacts (2026-09-26)
│   ├── conflict_check.py       ← evidence conflict check → conflicting_evidence (2026-09-26)
│   ├── prompt_defense.py       ← sanitize / sandwich / datamark prompt-injection defenses (2026-09-26)
├── frontend/
│   ├── index.html              ← Claims Handler Dashboard UI
│   ├── style.css               ← ClaimCenter/Jutro light-mode styling
│   └── app.js                  ← Dynamic client-side operations
├── tests/                      ← pytest suite (torch-free, fake embedder)
└── .github/workflows/
    ├── tests.yml                ← pytest CI: pytest-linux + postgres, both on a self-hosted runner (no Windows leg — see Current State)
    └── claude-review.yml        ← Advisory Claude Opus PR review (posts a sticky comment, never merges)
```

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

---

## Critical Constraints

* **100% Local Ingestion**: No document contents or raw vector embeddings may be sent to external cloud vector databases. All vector searches must be run on the host CPU/GPU locally.
* **Vectorized Cosine Similarity**: NEVER calculate cosine similarity using iterative Python loops over individual rows. Chunks must be compiled into a 2D numpy matrix and dot-products calculated in a single matrix multiplication to prevent CPU bottlenecks when scaling.
* **Normalized Embedding Cache Invalidation**: `search_similarity` reads from an in-memory, pre-normalized embedding matrix cache. Any code path that mutates `child_chunks`/`documents` MUST call `self._invalidate_vector_cache()` after commit, or searches will serve stale results. Cache rows are L2-normalized at build time, so the query vector must also be normalized and similarity is a plain dot product — do not reintroduce per-query corpus-norm computation.
* **UTF-8 Robustness**: PDF and TXT text extraction must handle decoding errors gracefully (`errors="ignore"` or similar safeguards) to prevent crashes on non-ASCII symbols.
* **Lazy ML Imports**: `sentence_transformers`/torch are imported inside `EmbeddingEngine`/`RerankingEngine.__init__`, not at module top, so the pure-numpy store stays importable without the ML stack. Keep it that way.
* **Never Synthesize With Zero Retrieved Context**: `AgenticRAGRouter._run_online_agent` must not call the LLM for synthesis when `top_matches` is empty after self-correction — this reliably produces a confident answer that fabricates citations to nonexistent filenames (caught by the eval harness). Any new retrieval-skip path (planner misclassification, new claim scoping logic, etc.) must still be caught by the zero-context hard stop before touching synthesis.
* **Sanitize Every Filesystem-Bound Filename**: any client-supplied filename that touches a filesystem path (upload, delete, download) MUST go through `safe_filename()` (`backend/rag_engine.py`) first. Unsanitized `os.path.join("stored_documents", filename)` is an arbitrary file write/delete vector — this was a real, critical vulnerability, not a hypothetical one. Sanitize once, early, and use the sanitized value consistently for both the DB `filename` column and the physical path, or lookups will mismatch.
* **`engine` Must Stay a Strict Allowlist**: `AgenticRAGRouter.run_query`'s `engine` parameter must only ever resolve to `"simulated"` or the hardcoded local LM Studio URL. Never derive a request URL from client input here — this was a real SSRF + context-exfiltration vector (the full retrieved claim/policy context got POSTed to whatever URL a caller supplied).
* **No String-Interpolated Inline Event Handlers in the Frontend**: never build `onclick="func('${value}')"` (or any inline handler) by interpolating a value into an HTML/JS string. `escapeHtml()` does not survive the HTML-attribute-then-inline-JS double-parse (entity-escaped quotes decode back to real quotes before the JS engine sees them). Use `addEventListener` with real JS values, or `data-*` attributes read via `.dataset`.
* **All SQLite Connections in `SQLiteVectorStore` Must Go Through `self._connect()`**: SQLite ships with `PRAGMA foreign_keys` OFF per-connection, so the schema's `ON DELETE CASCADE` clauses silently never fire on a raw `sqlite3.connect()` — deleting a document orphaned its chunk rows instead of cascading (verified empirically pre-fix). `_connect()` enables the pragma; never add a raw `sqlite3.connect(self.db_path)` inside the store.
* **FTS5 Queries Feeding RRF Must `ORDER BY rank`**: without it, FTS5 returns matches in rowid (insertion) order, so the keyword leg's rank positions in Reciprocal Rank Fusion are arbitrary and `LIMIT` truncates by document age instead of relevance (verified: bm25 order differed completely from returned order on the real corpus).
* **Immutable source publication**: stage a new uniquely keyed source before committing its index/reference transaction. Never overwrite the previous source. Failed publication preserves the prior index and source; delete current bytes after a committed deletion. Reclaim orphan versions with the offline collector. This supersedes the Phase 16 ordering rule; see `docs/portfolio-hardening.md`.
* **Dynamic Content Rendered into the Claims Queue/Estimate Table Must Use `textContent`, Not Interpolated `innerHTML`**: `CLAIMS_DATA` (served by `GET /api/claims`) is currently a hardcoded backend constant, so this isn't exploitable today, but `setupClaimsCases()`/`loadCaseFolder()` in `frontend/app.js` were building list/table markup via `innerHTML` template literals with raw `${c.id}`/`${c.status}`/`${row.op}`-style interpolation -- the same stored-XSS shape already fixed for document filenames in Phase 10, just not applied to this later (Phase 13) code path. Any future feature that makes a claim field user-editable would reopen it. Fixed by building nodes and setting `.textContent`/`.classList`, matching `renderDocuments`/`renderSources`.
* **Per-Scope Filename Isolation**: the `documents` table keys on `filename` alone (no `claim_id` in the UNIQUE constraint), so an overwrite must only be allowed when the existing row belongs to the SAME scope (same claim, or both global). Before this guard, uploading `report.pdf` to claim B silently deleted claim A's `report.pdf` -- row, chunks, and physical file (verified empirically). `add_document` raises `ValueError` on a scope mismatch, and the upload endpoints map it to a 400. The real fix at enterprise scale is namespacing storage/keys by `(claim_id, filename)`; this guard is the minimal stopgap that makes silent cross-claim data loss impossible.
* **FTS5 Query Tokens Must Be Quoted**: the keyword-search leg joins alphanumeric query tokens into the `MATCH` string bare. FTS5 operator words (AND/OR/NOT/NEAR) in user queries are then parsed as query syntax -- a leading/trailing operator raises a syntax error the `except sqlite3.OperationalError` swallows, silently dropping the whole keyword leg (verified: `MATCH 'OR windshield'` and `MATCH 'glass OR'` both error). Wrap every token in double quotes (`'"glass" "or"'`) so tokens are literal terms with implicit-AND semantics.

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
| Answer Guard | `backend/answer_guard.py` | **Active** | Post-generation: withholds unsupported claim-outcome assertions and off-allowlist contacts (`ANSWER_GUARD_MODE`, default withhold). |
| Conflict Check | `backend/conflict_check.py` | **Active** | Model extracts per-source values; code decides numeric disagreement, giving `conflicting_evidence` (`CONFLICT_CHECK`, default llm). |
| Prompt Defense | `backend/prompt_defense.py` | **Active** | `PROMPT_DEFENSE` default `sanitize`; `sandwich`/`datamark` opt-in (measured quality cost). |
| Adversarial Eval | `eval/adversarial/*`, `eval/run_adversarial_eval.py` | **Active** | Known and white-box held-out injection/conflict suites against a scratch store; `eval/golden_guard_check.py` for false positives. |
| Eval Harness | `eval/*` | **Active** | Naive-vs-hybrid+rerank Context Precision/Recall + live Faithfulness + live Factual Correctness (vs. reference), judged locally via LM Studio. Requires backend server running. |

---

## Build Plan — Current Phase

September 6: portfolio hardening implemented and verified; see [validation and remaining work](docs/portfolio-hardening.md). Earlier phase notes below are historical.

> Phases 1-18 are complete. Their full build records live in [docs/build-history.md](docs/build-history.md).

### Phase 17 — Enterprise Data Plane: Postgres + pgvector (PG leg executed & verified)
* **Goal**: Start Phase 1 of `docs/enterprise-migration.md` — replace/augment the SQLite data plane with Postgres + pgvector for multi-tenant correctness, without disturbing anything above the `VectorStore` seam.
* **Builds**:
  * `alembic/` + `alembic.ini` — Alembic migration `0001_initial_enterprise_schema.py`: `documents`/`parent_chunks`/`child_chunks` each carrying `tenant_id`, a **`(tenant_id, claim_id, filename)` unique index with `NULLS NOT DISTINCT`** (so cross-claim filename collisions are impossible *in the schema* — the schema-level fix that the SQLite per-scope overwrite guard was only a stopgap for), an **HNSW pgvector index** on `child_chunks.embedding`, a **generated `content_tsv` + GIN index** replacing the FTS5 leg, and **RLS per table keyed on the `app.tenant_id` GUC**.
  * `backend/postgres_store.py` — `PostgresVectorStore` implementing the exact `VectorStore` interface (a `postgres` backend is registered in `eval/parity_runner.py`), scoped to one configured tenant. Byte-for-byte the SQLite retrieval recipe: vector leg maps every child to its max-scoring parent (`max(1 - (embedding <=> q)) GROUP BY parent_id` — an **exact** scan, deliberately, since HNSW recall drift would complicate the parity proof this phase is about), Postgres FTS leg top-40 by `ts_rank` with **token-quoted** `websearch_to_tsquery` (the FTS5 token-quoting lesson), RRF (k=60) over `max(15, top_k)`, then the same cross-encoder rerank. Preserves every SQLite invariant: per-scope overwrite guard, cascade delete, `score=1.0` sentinel claim chunks, and immutable source staging before index publication (September 6 hardening).
  * `config.py`/`.env.example` — `VECTOR_STORE` (`sqlite`|`postgres`), `POSTGRES_DSN`, `TENANT_ID`; `app_factory.build_dependencies()` selects the store from settings.
  * Backend-neutral `get_document_content()` on the `VectorStore` ABC + both implementations — fixes the long-documented known issue where `app.py`'s `/api/documents/content` opened its own inline `sqlite3.connect` (which could not work on Postgres).
  * `pg_migrate.py` — one-time, idempotent scope-aware migration of `rag_store.db` into Postgres preserving original embeddings (no re-embed), with checksum gates (row counts, embedding dims, orphan-free).
  * CI — new `tests.yml` `postgres` job (ubuntu-only; GH Actions has no service containers on Windows) spinning up a `pgvector/pgvector:pg16` service that runs `pytest tests/test_postgres_store.py` and `parity_runner --backend-b postgres --tolerance 0.9`. The PG test module self-provisions the schema via `alembic upgrade head` (so the migration is itself exercised).
* **Verification status**: the Postgres leg is now **executed and verified against a real local PostgreSQL 16.4 + pgvector 0.7.4** (built from source into a user-local prefix in the dev sandbox, which has no root/Docker). Against it: `alembic upgrade head` clean + downgrade→upgrade round-trip clean; **all 8 PG-gated tests pass** (incl. the restricted-role RLS isolation proof); `eval/parity_runner.py --backend-b postgres --tolerance 0.9` → **mean recall@4 = 0.98** (3 exact-match dips to 0.75 are the expected HNSW/approximation divergence the tolerance exists for); `pg_migrate.py` migrated a live SQLite corpus, all checksum gates OK, idempotent on re-run, and the migrated rows are searchable through `PostgresVectorStore`. Full suite: `pytest tests/ -q` → **98 passed / 1 skipped**.
* **Status**: Implementation + CI wiring complete; PG-leg execution **done locally** (the CI `postgres` job repeats the same runs on the `pgvector/pgvector:pg16` service). New project constraint: **PG indexes/DDL differences are enforced by the tests, not rely-on-me** — the ONLY dynamic SQL in `PostgresVectorStore` that mixes `%(named)s` params with a single reuse is well-scoped; identifiers are never interpolated from client input (they're constants/`set_config` values).

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

Local environment maintenance (2026-09-23): removed 18 orphan NVIDIA/CUDA distributions left after the earlier ROCm migration. PyTorch remains `2.13.0+rocm7.2`; all 180 remaining distributions pass `uv pip check`, and an RX 9070 XT tensor operation plus `sentence_transformers` import pass. Both Triton distributions were retained because their file namespaces can overlap. This is environment cleanup, not a new retrieval benchmark or phase change.

As of September 6, the authoritative state is [portfolio-hardening.md](docs/portfolio-hardening.md): factory-owned dependencies, tenant/claim authorization, bounded parsing, concurrent audit appends, immutable versioned sources, privacy defaults, and isolated PR CI are implemented. Live answer-quality evaluation **was rerun on 2026-09-22** with the generator/judge loaded and `SIMULATION_MODE=false` — see the baseline entry under Known Issues and `docs/portfolio-hardening.md`. The adversarial evaluation ran on 2026-09-26, and three measured fixes followed (answer guard, conflict check, retrieved-text sanitizer): see [adversarial-evaluation.md](docs/adversarial-evaluation.md). Human-reviewed cases remain unrun. Earlier accomplishments below describe their original sessions, not current deployment or certification.

### Confirmed Working
* Ingestion of PDF, DOCX, XLSX, and TXT files (parent/child chunking + FTS5 index), producing real binaries in `stored_documents/` and clean, non-duplicated chunks.
* SQLite database cascading deletion of vector chunks + FTS cleanup (true as of Phase 14 — cascades silently never fired before the `_connect()`/foreign-keys fix; verified orphan-free on delete now).
* Hybrid retrieval (vector + FTS5 + RRF) with cross-encoder reranking, with the FTS leg properly bm25-ordered (`ORDER BY rank`) as of Phase 14.
* In-memory pre-normalized embedding cache (invalidated on add/delete); verified against `rag_store.db`.
* Per-claim document scoping and physical document serving, with all filenames sanitized against path traversal before touching the filesystem.
* Agentic router (online LM Studio + simulated) with structured pipeline logs, a strict `engine` allowlist, and a hard stop against zero-context synthesis.
* LM Studio API request forwarding and active status monitoring, with the loaded-model lookup cached (10s TTL) instead of hit on every query.
* Simulation mode fallback when LLM servers are offline.
* Domain-grounded eval harness (`eval/`) producing repeatable Context Precision/Recall/Faithfulness/Factual-Correctness scores against a fully local judge.
* Frontend document rendering via `addEventListener`/DOM properties (no string-built inline event handlers), closing a stored-XSS class of bug.
* Claims queue served from a single source of truth (`GET /api/claims` in `backend/app.py`, backed by `agentic_router.CLAIMS_DATA`) — the frontend fetches it at startup instead of holding its own hardcoded copy.
* Verified live end-to-end against a real LM Studio model (`qwen2.5-14b-instruct-1m`): planner decomposition, guaranteed claim-dossier chunk loading, and grounded synthesis with a cited source all confirmed via the actual Pipeline Trace panel, not just unit-level checks.
* 42-case pytest suite (`tests/`) committed and passing in GitHub Actions CI on both `ubuntu-latest`/`windows-latest`; README CI badge renders passing. Repo is public with an MIT `LICENSE`, pinned `requirements.txt`, a recorded demo GIF, and 8 discoverability topics.
* Tagged `v1.0.0` GitHub release (commit `b564b04`) — first stable, citable version. Release notes summarize retrieval architecture, the agentic router, the measured eval numbers (Context Precision/Recall/Faithfulness/Correctness), and the tests/CI/security hardening.
* Source publication now stages immutable bytes before the database transaction; regressions verify a failed replacement preserves the old source/index.
* All claim-queue/estimate-table rendering in `frontend/app.js` uses `.textContent`/`.classList` rather than `innerHTML` string interpolation (Phase 16 closed the one remaining gap in the Phase 10 XSS-prevention pattern) — no dynamic field anywhere in the frontend is interpolated into HTML unescaped.
* CI now includes an advisory Claude Opus PR review (`.github/workflows/claude-review.yml`, posts a sticky review comment, never auto-merges) alongside the existing pytest matrix.
* **Lint gate (ruff, added 2026-08-28 with the Phase-1 close-out)**: `ruff==0.16.5` pinned in `requirements.txt`, configured in `pyproject.toml` (`[tool.ruff]`; selected E/F/W/B with E501 + E741 ignored as non-blocking style churn), wired into the `pytest` and `postgres` CI jobs as a `ruff check .` step, and added as a `lint` gate in `scripts/run_foundation_gates.py` (resolves ruff from the interpreter's venv, blocking on any violation). Adopting it surfaced and fixed ~20 real correctness items (unused imports, `raise ... from`, explicit `zip(strict=)` for length-matched pairs, a dead loop index, FastAPI `# noqa: B008` on required-body `File`/`Form` defaults, and `# noqa: E402` on the deliberate sys.path/shim-ordered imports). `ruff check .` is clean; foundation gates pass (0 blocking).

* **Reranker batching (session 38, 2026-09-13)**: `LocalReranker` now supports bounded, process-local cross-request micro-batching via `RERANK_BATCHING_ENABLED` (default true), with explicit score partitioning, bounded queue/overload handling, lifecycle shutdown, and a direct-path rollback. Deterministic equivalence is green; a warm same-process GPU probe showed median 50.03ms → 47.58ms and throughput 199.90 → 210.18 requests/sec (~5.1%) for 10 concurrent synthetic requests. The authoritative Postgres/HTTP Locust A/B remains unrun because the documented local Postgres instance at port 55432 was unavailable; this does not close the Phase 6.1 concurrent p95 bar.

* **Phase 6.1 batching follow-up (session 38) supersedes the prior “next lever” note**: local batching is implemented and safely rollbackable, but the authoritative Postgres/HTTP concurrent-load comparison still needs to run with the corrected factory and an available database. The in-process result is directional only (~5.1% throughput improvement), not an SLO certification.
### Known Issues
* **Postgres-leg execution depends on the local dev build** — a real PostgreSQL 16.4 + pgvector 0.7.4 (built from source under `~/pg`/`~/pgdata`, port 55432) was used to run the Phase-1 PG leg locally. None of that is committed or required by CI; the CI `postgres` job reproduces the same runs on a `pgvector/pgvector:pg16` service container. Two latent bugs surfaced only against a real database and were fixed this session: `alembic/env.py` fed SQLAlchemy the plain `postgresql://` DSN (defaulted to the uninstalled psycopg2 → normalized to `postgresql+psycopg`), and `tests/test_postgres_store.py::_schema_ready` called `.fetchone()` on the psycopg3 Connection instead of the cursor (made every PG test silently skip → now uses the cursor).
* **Phase-1 vector leg deliberately does not use the HNSW index**: it runs an exact grouped `max(child cosine)` scan so parity with SQLite's brute-force search stays as close to 1.0 as possible (correctness-first). A KNN-accelerated path is the natural Phase-6 optimization once parity is locked — the HNSW index is already created.
* `eval/results.json`'s Context Precision/Recall numbers reflect single-shot retrieval via `/api/eval/search` only — they do **not** include the guaranteed claim-chunk inclusion described below, since that's a property of the full agentic pipeline (`/api/chat`), not of raw `search_similarity`. Re-scoring those two metrics against the real pipeline is unscoped work, not a bug; the eval harness deliberately isolates retrieval-strategy comparison from full-pipeline behavior. Factual Correctness (Phase 11) *is* scored against the real `/api/chat` pipeline, so it's the metric that actually reflects the guaranteed-dossier fix.
* Faithfulness and Factual Correctness are both judged by a single local 14B model — a real tradeoff vs. a larger hosted judge, made deliberately to keep evaluation consistent with the "runs entirely locally" constraint. Documented as a limitation in README.md rather than treated as a defect. Concretely: judge NLI-verification flakiness sporadically produces outlier 0.0 scores on manually-verified-correct answers, and Faithfulness moved 0.854→0.875→0.823→0.845→0.754→0.887 across runs with no code changes to that metric — treat single-run scores as noisy, trust trends across reruns. Latest run (2026-09-03, after the `RERANK_CANDIDATE_POOL` 50→15 tuning, session 34): Context Precision naive=0.797/hybrid=0.876, Context Recall naive=0.912/hybrid=0.947, Faithfulness 0.887, Factual Correctness 0.658 — all within the normal noise band established above; no eval-verified regression from the pool change (see Session Log session 34 for the full before/after, including the `pool=8` regression that was caught and reverted before shipping).
* **Eval numbers before and after 2026-09-03 are not comparable — the judge AND generator model changed.** `eval/results.json` up to that date was produced with `qwen2.5-14b-instruct-1m`; that model has since been deleted from the maintainer's machine, so those numbers cannot be reproduced. The app sends `model: "local-model"` (`default_model = settings.llm_model or "local-model"`, `app_factory.py`), so LM Studio serves **whatever is currently loaded** — meaning the generator silently follows the loaded model, and `eval/run_eval.py`'s judge is `/v1/models` `data[0]`. Both are now `qwen3-coder-30b-a3b-instruct`. Baseline as re-established 2026-09-03: naive P/R 0.779/0.947, hybrid+rerank P/R **0.885/0.965**, Faithfulness 0.812, Correctness 0.749, 1917s. **Superseded 2026-09-22 by a same-model rerun** (`qwen3-coder-30b-a3b-instruct` for both generator and judge, so this one *is* comparable): naive P/R 0.727/0.895, hybrid+rerank P/R **0.832/0.965**, Faithfulness **0.892**, Correctness **0.797**, 1965.1s, 19/19 scored on every metric with 0 failures. Faithfulness and correctness both improved; context precision fell in *both* arms by near-identical amounts (−0.053 hybrid, −0.052 naive). The session-38 reranker batching change touches only the hybrid arm and so cannot explain a matched drop in the naive baseline — read it as judge variance or corpus drift, consistent with the 0.754→0.887 no-code-change swing recorded above, and do not call it a regression without a confirming run. Per-query correctness moved by up to ±0.8 versus the old run purely from the model swap, so **never read a cross-judge delta as a regression**; re-baseline instead whenever the loaded model changes. Set `LLM_MODEL` explicitly if the generator ever needs pinning.

* **Cross-encoder reranking is the retrieval-latency bottleneck, not corpus scale or the vector index — sequential leg closed on GPU in session 36, concurrent leg still open** (Phase 6.1 load test, sessions 33–36): confirmed flat across 200 to 100,000 docs — naive/hybrid (no rerank) stays ~15-18ms at any scale tested, `hybrid_rerank` costs ~155-400ms depending on candidate pool size regardless of corpus size. `RERANK_CANDIDATE_POOL` (50→15) closes most of the *sequential* gap with no measured quality cost. **Session 35 profiled the concurrent-load p95 directly with `py-spy` instead of another config sweep** (a controlled harness reusing `scripts/run_retrieval_load_test.py`'s own helpers, `sudo py-spy record --nonblocking` attached to the live server during a real Locust run — `--native` had to be dropped, it's incompatible with `--nonblocking`). The flame data ruled out tokenization (1.1% of sampled stack time) and confirmed the cost is genuine BERT forward-pass compute (`Linear.forward` 72%, attention 14%, layer_norm/activations 6% — 91% total). Two follow-up controlled A/B tests then ruled out both standing hypotheses from sessions 33-34: capping PyTorch intra-op threads per call 8→1 left p95 unchanged (3500ms→3500ms), and even 4 fully separate worker processes (own GIL, own memory, threads capped to 1 each) left it unchanged too (3500ms→3300ms) — a `--workers 4` run at *default* threads made it far worse (11000ms, real oversubscription confirmed harmful, consistent with session 33's inconclusive prior attempt at this same test). Neither thread count nor process count matters, which rules out both "GIL-blocking pattern" and "too many threads per call" as the mechanism — latency scales with the number of *simultaneous* cross-encoder forward passes regardless of how each one is threaded/processed, consistent with a shared CPU resource (memory bandwidth/cache) that no software threading knob adds more of. **Partial fix shipped**: `RERANK_MAX_CONCURRENCY` (default 2) bounds `LocalReranker` with a `threading.Semaphore`, trading unlimited concurrency for queuing — measured p95 at concurrency 1/2/3/4/6 = 4000/3100/3200/3000/3600ms vs. ~3500ms unbounded (concurrency=1 over-serializes and is worse than baseline; 2-4 is a real but modest ~15-20% win, well short of the 100ms bar; these are single runs on Jared's shared desktop machine, not clean SLO-grade numbers — see the session 33 caveat about unisolated infrastructure, which still applies). The sequential per-call floor (~155-400ms) is still above the bar on its own, so no amount of concurrency-shaping fully closes this — a GPU remains the real fix. **Session 36 got one.** ROCm 7.2.4 was installed (RX 9070 XT = `gfx1201`) and the venv's torch swapped from the CUDA build (`2.13.0+cu130`, which reports no GPU on an AMD card) to `2.13.0+rocm7.2`. The fix taken was `LocalReranker` on the GPU via a new `RERANK_DEVICE` setting (default `auto`: GPU when torch sees one, CPU fallback otherwise), **not** `RemoteReranker` — every earlier entry named the remote service as "the real fix" only because a *local* GPU didn't exist; with one in the box a device string is the whole change, and an HTTP hop to reach the same silicon buys nothing. Measured A/B with only `RERANK_DEVICE` changed: rerank call at pool=15 149ms→53ms, at pool=50 436ms→135ms; **end-to-end `/api/eval/search` `hybrid_rerank` p95 179ms→65ms — under the 100ms bar for the first time**, with the naive/hybrid modes unchanged at 4-5ms as the control. Concurrent p95 (10 simultaneous) 1367ms→475ms — better, still over the bar, and **the mechanism has changed**: sweeping `RERANK_MAX_CONCURRENCY` 1/2/4/8/16 on GPU gives 492/477/482/512/589ms, essentially flat and worse above 4, because ~10 queued calls × ~50ms each ≈ 500ms is simply un-batched forward passes serializing on the GPU — not the CPU memory-bandwidth contention the semaphore was built for. Default stays 2 (near-neutral on GPU, still real on the CPU fallback path). Next lever if the concurrent bar is ever prioritized: cross-request batching, genuinely different from anything tried in sessions 33-35.

---

---

## What's Next

Priority after hardening: the answer/evidence leg of this is **done** (2026-09-22 full-suite rerun, numbers under Known Issues); what remains is a confirming run to separate judge variance from drift. The **adversarial baseline was measured 2026-09-26** ([adversarial-evaluation.md](docs/adversarial-evaluation.md)): injection through retrieved documents succeeded 14/16 with `decision_status` still `not_a_decision`, and `conflicting_evidence` is never emitted. **Fix 1 landed 2026-09-26**: `backend/answer_guard.py` (`ANSWER_GUARD_MODE=withhold` default) withholds answers asserting unsupported claim outcomes or off-allowlist contacts — decision forgery 0/4→4/4 blocked, attack success 87.5%→56.3%, 0/19 golden false positives. **Fix 2 landed 2026-09-26**: `backend/conflict_check.py` (`CONFLICT_CHECK=llm` default) — the model extracts each source's value (labelled policy_value/claim_amount), code decides numeric disagreement → `conflicting_evidence` on 9/9 real conflicts, 0/2 controls and 0/19 golden answers mislabelled, ≈ +3–4.5 s per answer. **Fix 3 landed 2026-09-26**: `backend/prompt_defense.py`, default `PROMPT_DEFENSE=sanitize` (owner decision) — removes 0 honest sentences, cuts known attacks 56%→25%, but 0/12 of a white-box held-out set (`eval/adversarial/cases_holdout.py`); `sandwich` generalises (held-out 42%→17%) but cost ~0.12 golden correctness, `datamark` hurt. A reworded sandwich (no "answer only") recovered quality (0.882/0.789) but lost all held-out protection (50%) — the narrowing *is* the protection, so the strict wording stays, opt-in (`eval/prompt_defense_ab.json`). Adversarial work on this branch is complete; remaining evidence items are human-reviewed cases and the Postgres performance rerun — rerunning `eval/run_adversarial_eval.py` against the baseline after each. Also: repeat real Postgres performance measurements with the corrected factory; validate deployment-specific IdP, retention, and infrastructure controls before any real claims data. The earlier roadmap below is historical context.

**Start here in a fresh session**: read this section, then the Phase 6.1 status paragraphs in `docs/enterprise-migration.md`, the session 38 entry in `docs/build-history.md`, and the batching spec/plan if continuing reranker performance work.

1. **Phases 0–5 of `docs/enterprise-migration.md` are complete.** Phase 4: 4.1 (auth), 4.2 (RBAC), 4.3 (audit), 4.4 (rate limiting + upload/input caps). Phase 5: 5.1 (inference gateway), 5.2 (dedicated cross-encoder reranker service), 5.3 (streamed `/api/chat` SSE + capped context assembly + prompt-injection delimiters).
2. **Phase 6.1 (load test) is measured and partially closed** (sessions 33–35, 2026-09-02/03):
   - **Root cause found**: CPU-bound cross-encoder reranking, not corpus scale or the vector index — confirmed flat across 200 to 100,000 docs.
   - **Sequential latency: fixed (partially).** `RERANK_CANDIDATE_POOL` lowered 50→15 (~365ms→~155-167ms), verified via 3 full eval-suite runs to cost nothing on the metrics that matter — a more aggressive `pool=8` was tried first and reverted after the full eval suite (not just a quick golden-query check) caught it regressing the exact query Phase 9's guaranteed-dossier-inclusion fix was built around.
   - **Concurrent-load batching: implemented but not SLO-verified.** Local bounded micro-batching is enabled by default and has deterministic equivalence coverage plus a ~5.1% warm in-process throughput signal. The Postgres/HTTP Locust A/B is still required; the local database on port 55432 was unavailable during session 38, so the concurrent p95 bar remains open.
3. **Roadmap after 6.1**: reranker batching is implemented as a guarded local optimization, but Phase 6.1 remains open until the corrected Postgres/HTTP load comparison is run. Then continue with 6.2 (eval→drift monitoring), 6.3 (compliance evidence), and 6.4 (cutover), or tune batching from measured load results.
4. **Remaining deployment items** (infra/account-setup, not code): general KMS integration beyond the already-configured `S3_SSE_KMS_KEY_ID`, live OIDC authorization-code wiring against a real IdP (deliberately deferred by the maintainer — needs picking a provider and creating a dev tenant), live golden eval against a real IdP. Docker packaging, dependency-audit/bandit harness wiring, and the self-hosted-runner-vs-public-repo CI safeguard are all done (see Session Log sessions 26–28, 30).
5. **Production builds**: still optional: package/deploy beyond the existing `Dockerfile`/`docker-compose.yml` if wider distribution is ever desired.

---

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
