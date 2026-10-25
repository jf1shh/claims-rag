# ClaimsRAG — Architecture

> Moved out of `CLAUDE.md` on 2026-10-02 so the always-loaded guide stays small. Read when touching components or file layout.

## Architecture Overview

### Components

* **DocumentParser** ([backend/rag_engine.py](backend/rag_engine.py)): Extracts text from PDF (`pypdf`), DOCX (`python-docx`), Excel (`pandas`/`openpyxl`), and TXT files.
* **TextChunker** ([backend/rag_engine.py](backend/rag_engine.py)): Hierarchical parent/child chunking. Parent chunks (~1200 chars / 200 overlap) preserve context and back the FTS5 index; child chunks (~250 chars / 50 overlap) are embedded for precise vector matching. Boundaries snap to whitespace.
* **EmbeddingEngine** ([backend/rag_engine.py](backend/rag_engine.py)): Instantiates `sentence-transformers/all-MiniLM-L6-v2` locally to generate 384-dimensional vectors. Imported lazily so the vector store can be used without loading torch.
* **Reranker** ([backend/reranker.py](backend/reranker.py)): Provider-neutral cross-encoder seam. `LocalReranker` preserves the lazy CPU implementation; `RemoteReranker` sends a bounded candidate pool to a dedicated GPU HTTP service; `FallbackReranker` fails open to local.
* **SQLiteVectorStore** ([backend/rag_engine.py](backend/rag_engine.py)): Stores documents, parent/child chunks, embedding BLOBs, and an FTS5 index. A document's identity is `(claim_id, filename)` (2026-10-20), so the same filename can live in several claims and in global scope; lookups and deletes always carry the scope. `search_similarity` runs **hybrid retrieval**: vectorized cosine over child chunks (mapped up to best parent) + FTS5 keyword search, fused via RRF, then cross-encoder reranked. Embeddings are held in an **in-memory, pre-normalized matrix cache** (built lazily, invalidated on add/delete) so queries avoid re-reading BLOBs and recomputing corpus norms.
  **Rollout note**: the ingestion worker's async delete messages queued before the (claim_id, filename) deploy carry no `claim_id` key at all, which reads back as `None` (global scope) -- indistinguishable from a real global delete. Drain the delete queue before deploying this change.
* **AgenticRAGRouter** ([backend/agentic_router.py](backend/agentic_router.py)): Orchestrates each query. Online mode asks the LLM for a JSON plan (which stores to search + sub-queries), retrieves per sub-query, self-corrects on empty results, and synthesizes a grounded answer. Simulated mode runs local retrieval, then computes labor, OEM, total-loss and payout answers with `backend/claims_tools.py` (fraud/SIU narratives remain canned). Emits step-by-step `pipeline_logs`.
* **FastAPI Server** ([backend/app.py](backend/app.py)): Exposes REST endpoints for global + per-claim upload, listing, deletion, document content/download, agentic chat, and LLM connection status.
* **Frontend Web Dashboard** ([frontend/index.html](frontend/index.html)): HTML/CSS/JS UI with a claims queue, per-claim document folders, claims chat, clickable/viewable document citations, and real-time RAG pipeline logs.

### File Structure

```
claims-rag/
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
│   ├── claims_tools.py         ← Deterministic claims tools (labor caps, OEM, total loss, deductible) with cited inputs
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
│   ├── provenance_check.py     ← answer provenance check: redacts planted identifiers/filenames (2026-10-16)
│   ├── figure_check.py         ← live answers: money/percent/date figures checked against computed tool figures (2026-10-20)
│   ├── prompt_defense.py       ← sanitize / sandwich / datamark prompt-injection defenses (2026-09-26)
├── frontend/
│   ├── index.html              ← Claims Handler Dashboard UI
│   ├── style.css               ← ClaimCenter/Jutro light-mode styling
│   └── app.js                  ← Dynamic client-side operations
├── tests/                      ← pytest suite (torch-free, fake embedder)
└── .github/workflows/
    ├── tests.yml                ← pytest CI: pytest-linux + postgres on GitHub-hosted ubuntu-latest (self-hosted only while private; no Windows leg)
    └── claude-review.yml        ← Advisory Claude Opus PR review (posts a sticky comment, never merges)
```

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
| Enterprise deployment hardware? (2026-10-16) | Assume several on-prem DGX Spark units or high-memory Apple Silicon Macs running large models; keep the single-GPU local profile working | Owner's expectation for how a carrier would run it. Keeps every model call behind OpenAI-compatible endpoints, configurable per role, with a larger judge and a new eval baseline per model. Detail: `docs/enterprise-migration.md` target architecture. |
| Which license? | AGPL-3.0 (from 2026-10-17; MIT before) | Owner choice: keeps the code open and readable for reviewers, while anyone running a modified copy as a service must publish their source, which deters closed commercial reuse. Copies taken under MIT keep MIT terms. |
| How does the router say "not found"? (2026-10-25) | Optional answerability gate (`ANSWERABILITY_GATE=llm`): one local-model call reads the served passages together and returns P(yes) from first-token logprobs; below 0.5 the answer is refused before synthesis | Retrieval always returns top_k passages, so a question the corpus cannot answer was drafted from lookalikes. The best cross-encoder score could not separate them (keeping every answerable question it caught 3/16 absent ones); the joint judge caught 15/16 with 26/26 answerable kept (`eval/answerability-probe-2026-10-25.json`). One call over all passages beat one call per passage, which could not see answers that combine two passages. After Subworkflow's "RAG vs JAG" (judgement-augmented generation), run on the local model, never a cloud judge. |

## Claims tools contract (2026-10-16)

`backend/claims_tools.py` is pure Python with no I/O. `labor_rate_check(estimate)`,
`oem_rider_check(vehicle=, loss_year=, endorsements=, mileage=)`,
`total_loss_check(state=, repair_cost=, acv=, salvage=)` and
`deductible_apply(lines=, plan=, deductible=, endorsements=, custom_ops=, excluded=)` each return
a dict with `tool`, a status or result, and `inputs`: a list of `{name, value, source, section}`.
`render_inputs()` turns those into the citation table shown in chat. Rule values mirror the
generator text; `tests/test_claims_tools.py` fails on drift. Missing inputs (ACV, salvage,
mileage) are reported, never assumed. Findings that are not math (custom-equipment lines, SIU
exclusions) live in `CLAIM_EVIDENCE` in `agentic_router.py`.

## Answerability gate contract (2026-10-25)

`OpenAICompatibleClient.yes_probability(messages, *, model, stage="answerability_check")` posts
`max_tokens: 1, temperature: 0, logprobs: true, top_logprobs: 10` and returns
`yes_share(top_logprobs)` = P(yes) / (P(yes) + P(no)), with case and leading spaces folded, or
None when the server sends no logprobs. Stages ending in `_check` use `LLM_CHECK_TIMEOUT_SECONDS`.
Lemonade (llama.cpp) returns `choices[0].logprobs.content[0].top_logprobs`.

`backend/answerability.py` is pure apart from that duck-typed client. `judge_joint(client, query,
passages)` sends every served passage (up to 12, 900 chars each, HTML-escaped inside
`<passage file=...>` delimiters) in one call. `judge()` scores each passage separately and is kept
for the probe. Both return None on any failure and never raise. The prompt tells the model it is not
answering the question: a passage that settles it with "no" or "excluded" counts as yes. Without
that line, yes/no questions whose answer is no scored ~0.

`AgenticRAGRouter._answerability_gate` runs after `_assemble_context`, on the exact `top_matches`
synthesis would see. It is skipped when the gate is off, when a computed tool section exists
(SIU, subrogation, coverage and other claim audits answered by `claims_tools`), and when the judge
returns None (fail open, logged). A refusal returns `sources: []` (so `structured_projection` reports
`insufficient_evidence`) plus `answerability: {p_yes, threshold, judged}`. Measure with
`scripts/probe_answerability.py` (the absent set is `eval/absent_queries.py`).

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
| Provenance Check | `backend/provenance_check.py` | **Active** | Runs after the answer guard on live answers. Unretrieved filenames are checked in code; code-like identifiers found in sources get one narrow model call ("does this passage direct the reader to reproduce it?"), and code redacts those lines. Response carries `provenance` {action none/redacted/withheld, findings, judged, errors}; audit gets `provenance_action`/`provenance_findings` (`PROVENANCE_CHECK`, default llm). |
| Figure Check | `backend/figure_check.py` | **Active** | Live answers only, when the router attached a `tool_section` (known claim, audit-type query). `api.tools_result` runs last on `/api/chat` and the stream: it checks every money amount, percentage and date in the prose against the section, the sources and the claim dossier (exact after normalization), adds a visible notice plus `figure_check` {action flagged/none, checked, unverified} when something doesn't match, and appends the section after guard/provenance so its citations are never redacted. A withheld answer gets the section without a check; a tool or check error drops only the section or check, with a WARNING. |
| Prompt Defense | `backend/prompt_defense.py` | **Active** | `PROMPT_DEFENSE` default `sanitize`; `sandwich`/`datamark` opt-in (measured quality cost). |
| Adversarial Eval | `eval/adversarial/*`, `eval/run_adversarial_eval.py` | **Active** | Known and white-box held-out injection/conflict suites against a scratch store; `eval/golden_guard_check.py` for false positives. |
| Eval Harness | `eval/*` | **Active** | Naive-vs-hybrid+rerank Context Precision/Recall + live Faithfulness + live Factual Correctness (vs. reference), judged locally via LM Studio. Requires backend server running. |
