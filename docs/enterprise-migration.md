# Enterprise Migration Plan — SQLite → Postgres + pgvector + S3 + Async Ingest

> **Status: Phase 4 complete / Phase 5 complete (2026-08-30).** This
> document supersedes the "Enterprise multi-tenant scaling (DEFERRED)" note in
> CLAUDE.md's What's Next and is the source of truth for the migration. Each phase
> updates its milestone statuses here.

Target: a multi-tenant, production-grade version of AutoClaimsRAG. Everything is
provider-neutral except where noted (assumes AWS: managed Postgres on RDS/Aurora,
S3, SQS, Fargate/Lambda workers, KMS).

## Target architecture

- **Storage:** `documents` / `parent_chunks` / `child_chunks` in **Postgres + pgvector**
  (`embedding vector(384)` + HNSW `cosine_ops` index; Postgres FTS `tsvector`/GIN
  replacing the FTS5 leg; `tenant_id` + RLS on every row; unique on
  `(tenant_id, claim_id, filename)`).
- **Files:** source documents in **S3** (SSE-KMS, versioning, presigned GETs).
- **Ingest:** S3 put-event → queue → worker (parse → chunk → embed → incremental
  upsert). Kills the full-cache-rebuild O(n) bottleneck and request-thread blocking.
- **Serving:** stateless FastAPI, autoscaled; `/api/chat` async/streamed; planner,
  reranker, and LLM as dedicated services (vLLM/TGI or hosted OpenAI-compatible).
- **Tenancy/security:** SSO/RBAC, claim-level ACLs, immutable audit log, rate
  limits, encryption at rest/in transit, retention/deletion policy.

**What stays untouched:** `TextChunker`, `DocumentParser`, RRF math, rerank logic,
prompt templates, `AgenticRAGRouter` orchestration, eval golden queries, and the
frontend chat/trace UX. Callers consume a `VectorStore` interface
(`backend/rag_engine.py`); retrieval changes live behind `search_similarity` /
`get_claim_chunks`.

---

## Phase 0 — Foundations & parity harness (2–3 wks) — COMPLETE

| Milestone | Deliverable | Status |
|---|---|---|
| 0.1 | Extract `VectorStore` interface (`add_document`, `delete_document`, `search_similarity`, `get_claim_chunks`, `get_all_documents`, `get_claim_documents`) from `SQLiteVectorStore`; make DB/storage paths env-configurable (`RAG_DB_PATH`, `STORED_DOCUMENTS_DIR`) and anchored to the repo root (no more CWD-relative `rag_store.db`) | **Done 2026-08-20** — ABC in `backend/rag_engine.py`; `SQLiteVectorStore(storage_dir=…)`; env overrides verified; full suite green |
| 0.2 | `eval/parity_runner.py`: run identical corpora through two backends and compare results on the 19 `eval/golden_queries.py` queries + synthetic queries | **Done 2026-08-20** — self-check mode green in CI (`python eval/parity_runner.py`, mean recall@4 = 1.0); Postgres leg activates in Phase 1 with `--backend-b postgres --tolerance 0.9` |

**Exit criteria:** SQLite backend passes the full test suite unchanged in behavior;
parity harness runs in CI (self-check parity = 1.0); paths configurable + repo-anchored.

**Close-out evidence (2026-08-28, on `feat/enterprise-foundation`):**

- `pytest tests/ -q` → **90 passed, 1 skipped** (exit 0).
- `python scripts/run_foundation_gates.py --mode gate` → **0 findings, 0 blocking** (exit 0).
- `python eval/parity_runner.py` → **mean recall@4 = 1.0, mean exact-match@4 = 1.0** (exit 0).
- `python -m compileall -q backend app_factory.py config.py` → clean (exit 0).
- `git ls-files` → no runtime artifacts committed (no `rag_store.db`, `stored_documents/`,
  logs, caches, or secrets).
- The foundation implementation plan is marked complete with the two manual steps
  (live golden evaluation, human diff review) explicitly remaining.

Phase 0 is complete; the next work is **Phase 1 — Data plane: Postgres + pgvector**.

**Key decision:** pgvector HNSW is approximate vs SQLite's brute-force search — the
parity harness defines acceptable divergence (recall@k ≥ 0.9) *before* data migrates.

---

## Phase 1 — Data plane: Postgres + pgvector (3–4 wks)

| Milestone | Deliverable | Exit criteria | Status |
|---|---|---|---|
| 1.1 | Alembic migrations: `documents`, `parent_chunks`, `child_chunks` with `tenant_id`, HNSW index, GIN FTS, RLS policies, `(tenant_id, claim_id, filename)` unique (“NULLS NOT DISTINCT” so global/claim scopes are distinct) | Cross-claim filename collision impossible in the schema; RLS proven (tenant B sees nothing of tenant A) | **Done 2026-08-28** — migration (`alembic/versions/0001_initial_enterprise_schema.py`) executes cleanly against a real PG16+pgvector0.7.4, downgrade→upgrade round-trips; RLS proof + full gated suite pass |
| 1.2 | `PostgresVectorStore` implementing the interface: pgvector cosine scoped by tenant/claim + Postgres FTS (`websearch_to_tsquery`) + the same RRF (k=60) + unchanged cross-encoder rerank | Golden-query parity passes (Phase-0 harness with `--backend-b postgres`); token-quoting and `ORDER BY` (ts_rank) lessons carried over | **Done 2026-08-28** — `backend/postgres_store.py` + harness; `--backend-b postgres --tolerance 0.9` → mean recall@4 = 0.98 (3 exact-match dips to 0.75 are the expected HNSW approximation caught by the tolerance); all 8 PG-gated tests pass against live Postgres |
| 1.3 | `pg_migrate.py`: read `rag_store.db`, upsert docs/chunks/embeddings/metadata, checksum-verified (row counts, dims, no orphans) | Demo corpus migrates cleanly; counts match; results match pre-migration | **Done 2026-08-28** — migrates a SQLite corpus into PG, all checksum gates OK, idempotent on re-run, and the migrated rows are searchable/claim-visible through `PostgresVectorStore` under the same tenant |

> **Phase 1 status (2026-08-28):** the Postgres leg is now *executed and verified*
> against a real local PostgreSQL 16.4 + pgvector 0.7.4 (built from source into a
> user-local prefix in the dev sandbox, which has no root/Docker). Two latent bugs
> surfaced only on real execution and were fixed: (a) `alembic/env.py` fed the plain
> `postgresql://` `POSTGRES_DSN` to SQLAlchemy without a driver, which defaulted to
> the uninstalled psycopg2 (now normalized to `postgresql+psycopg`); and (b)
> `tests/test_postgres_store.py::_schema_ready` called `.fetchone()` on the
> psycopg3 *Connection* instead of the cursor returned by `execute()`, which silently
> made every PG test skip (fixed to use the cursor). With both fixed: migration +
> downgrade/upgrade round-trip green, 8/8 PG-gated tests pass, parity = 0.98 (tolerance
> 0.9), and the full suite is 98 passed / 1 skipped. Milestone 1.2 currently uses an
> **exact** grouped `max(child cosine)` scan (not HNSW KNN) so parity with SQLite is
> as close to 1.0 as possible; the HNSW index is created but a KNN-accelerated path
> is the natural Phase-6 optimization once correctness parity is locked.

---

## Phase 2 — Object storage: S3 (2–3 wks)

| Milestone | Deliverable | Exit criteria | Status |
|---|---|---|---|
| 2.1 | Upload writes to `s3://bucket/{tenant}/{claim-or-global}/{key}`; `documents.s3_key`; SSE-KMS; versioning | Server stateless w.r.t. files; delete removes object (or soft-delete + lifecycle) | **Done 2026-08-29** — `S3DocumentBlobStore` adapter + store wiring (see below) |
| 2.2 | Download/view via presigned URLs; `get_document_content` reads S3 or DB chunks | UI view/download parity; nosniff/attachment hardening carried over; per-scope overwrite semantics preserved | **Done 2026-08-29** — `/api/documents/download` redirects to a presigned GET when S3 is configured; `get_document_content` stays DB-chunk-based (backend-neutral); per-scope overwrite guard + file-after-commit ordering preserved in both stores |

> **Phase 2 status (2026-08-29):** the object-storage seam (already sketched in
> Phase 0 as `DocumentBlobStore`) is now real and wired end-to-end. `backend/blob_store.py`
> gained `S3DocumentBlobStore` (boto3; tenant-scoped object keys `{tenant}/{scope-or-global}/{filename}`;
> presigned GET via `create_download_url`; SSE-KMS optional via `s3_sse_kms_key_id`; endpoint-URL
> overridable for MinIO/LocalStack). Both `SQLiteVectorStore` and `PostgresVectorStore` accept an
> optional `blob_store`; when set, `add_document`/`delete_document` route source bytes through it
> **after** the DB commit (Phase 16 ordering preserved) and `get_blob_key()` resolves the download
> key by scope. `app_factory.build_dependencies()` + `backend/app.py` build the adapter from
> `OBJECT_STORAGE_PROVIDER`/`OBJECT_STORAGE_BUCKET`/`S3_REGION`/`S3_ENDPOINT_URL`/`S3_SSE_KMS_KEY_ID`.
> Tests: hermetic `moto`-based suites (`tests/test_blob_store_s3.py`, `tests/test_store_blob_wiring.py`,
> plus a PG blob-wiring case in `tests/test_postgres_store.py`) — no real cloud needed, CI runs them
> as ordinary pytest. `filesystem` remains the default and is byte-identical to before (the adapter
> returns None → legacy storage_dir writes). Verified: full suite 112 passed / 1 skipped with the PG
> leg; live moto smoke of `/api/documents/download` returns a 307 to a presigned URL
> (`{tenant}/global/labor.txt`).

---

## Phase 3 — Async ingestion (3–4 wks)

| Milestone | Deliverable | Exit criteria | Status |
|---|---|---|---|
| 3.1 | S3 put-event → queue → worker: parse → chunk → embed (batched) → incremental pgvector upsert | Ingestion no longer O(n) per write; embed failures → retryable job, never a half-state | **In progress 2026-08-29** — queue seam, S3-event bridge, and worker implemented + tested (see status below) |
| 3.2 | `/api/upload` → `202 {job_id}`; `GET /api/jobs/{id}`; frontend progress binds to real job state; delete/overwrite are jobs | UI reflects true pipeline state; corrupt-file 400 detail preserved | **Done 2026-08-29** — 202 + jobs API + durable job records + frontend polling + delete-as-job (see status below) |
| 3.3 | Idempotency by `(tenant, s3_key, etag)`, DLQ + retry/backoff, job metrics | Failure drills pass (no orphan rows on mid-embedding crash); 10k-doc throughput load test | **Done 2026-08-29** — etag idempotency, DLQ/retry drills (in-process + SQS), worker job metrics, 10k-doc load test (see status below) |

> **Phase 3 status (2026-08-29):** milestone 3.1 is implemented behind a
> provider-neutral queue seam, mirroring how Phase 2's blob storage was done.
> `Queue` ABC (`backend/queue.py`) with `InProcessQueue` (deterministic
> dev/test loopback, injectable clock) and `SQSQueue` (boto3 adapter;
> visibility-timeout retry via `change_message_visibility`; explicit DLQ
> forwarding when `SQS_DLQ_URL` is set — moto-tested). `S3EventIngestBridge`
> (`backend/s3_events.py`) converts S3 put-event records into tenant-validated
> queue messages (skips wrong bucket / foreign-tenant / malformed / non-created
> records; the bucket-notification → Lambda hop is deployment wiring, the
> bridge is the tested unit). `IngestionWorker` (`backend/ingestion_worker.py`)
> runs fetch-blob → parse → chunk → batch-embed → `vector_store.add_document`
> (one transaction = the incremental upsert; replay overwrites in place) with
> retryable-vs-permanent failure classification, exponential backoff, and DLQ
> on budget exhaustion. The no-half-state guarantee is tested directly: a
> mid-embedding failure rolls back and leaves nothing searchable, on both
> SQLite and Postgres (PG-gated cases in `tests/test_postgres_store.py`).
> Wiring: `_build_queue` in `app_factory.py` (mirrors `_build_blob_store`);
> `QUEUE_PROVIDER`/`SQS_QUEUE_URL`/`SQS_DLQ_URL`/`SQS_REGION`/`SQS_ENDPOINT_URL`
> + worker tuning (`WORKER_MAX_RETRIES`, `WORKER_BACKOFF_BASE_SECONDS`,
> `WORKER_POLL_INTERVAL_SECONDS`) in `config.py`/`.env.example`. Test suite:
> 35 new hermetic cases (queue semantics, bridge, worker incl. failure drill)
> + 2 PG-gated; full suite 137 passed / 12 skipped (PG-gated, no local PG),
> ruff clean, foundation gates 0 blocking.

> **Phase 3 status (2026-08-29, milestone 3.2):** upload now has a real async
> contract. `SqliteJobStore` (`backend/job_store.py`) makes job records durable
> in their own SQLite file (`JOBS_DB_PATH`, default `jobs.db`) -- independent of
> the vector-store backend, so `GET /api/jobs/{id}` stays truthful across
> restarts and across processes (SQS + separate worker). `IngestionService`
> (`backend/ingestion.py`) evolved from the in-memory Phase-0 form: with a queue
> attached, `submit` persists a `queued` job and enqueues (dedupe by explicit
> idempotency key or content checksum); without one it keeps the legacy inline
> path; `record_result` lets the sync endpoint record an already-finished job so
> both modes return a `job_id`. The worker (`backend/ingestion_worker.py`) now
> advances the job record queued → parsing → embedding → indexed/failed,
> bumps `retry_count` on each rejected attempt, and creates records for
> S3-event messages that arrive without one. API: `GET /api/jobs/{job_id}`
> (tenant-guarded, 404/403); `/api/upload` + `/api/upload-claim-file` return
> **202 + job_id** in `INGESTION_MODE=async` (cheap 400s -- extension/empty --
> preserved; corrupt files become `failed` jobs) and keep the legacy timing
> response plus `job_id`/`status` in sync mode. Async mode stages bytes in the
> blob store (`_async_blob_store`; local `ingest_queue` dir for the filesystem
> provider) and starts an in-process worker so local async dev works end-to-end;
> SQS deployments run the worker as its own process via `build_ingestion_worker`.
> Frontend: `pollIngestionJob` drives the upload progress bar from the real job
> record (`app.js?v=1.0.6`). Fixed en route: both upload endpoints passed the
> raw `embedding_engine` module global (None until a chat call lazily loaded it)
> -- a fresh server's first upload crashed with a 500; now `_get_embedding_engine()`.

> **Phase 3 status (2026-08-29, milestone 3.2 close-out):** delete-as-job landed
> to finish 3.2. `/api/delete` returns **202 + job_id** in async mode
> (`IngestionService.submit_delete` creates a queued delete job and enqueues an
> `action: "delete"` message; the worker removes the document and advances the
> job to `deleted`) and keeps inline behavior + a recorded `deleted` job in sync
> mode. Delete dedupes only while in flight (content-independent, so a completed
> delete must not suppress a later one). Missing-document deletes become failed
> jobs (`DOCUMENT_NOT_FOUND`), mirroring the sync endpoint's 404; unsafe
> filenames 400 at the API before enqueue. The frontend polls delete jobs to
> completion (`pollIngestionJob` treats `deleted` as terminal success). Fixed en
> route: `/api/delete` never mapped `safe_filename` `ValueError`s to 400 (traversal
> names 500'd) -- now guarded like the upload endpoints.

> **Phase 3 status (2026-08-29, milestone 3.3):** the remaining 3.3 items landed
> together with the 3.2 close-out. **Idempotency by (tenant, s3_key, etag):**
> the worker stores `s3:{blob_key}:{etag}` as the dedupe key on jobs it creates
> from S3 events and checks it before processing -- a re-delivered put-event for
> an already-handled object version is acked as a `DUPLICATE` with no work (a
> new etag re-indexes in place). **DLQ + retry drills:** recoverable-embed
> failure drill (fails N times then succeeds → job indexed with retry_count),
> and the SQS leg (worker + moto SQS + DLQ: visibility-timeout rejects exhaust
> into the dead-letter queue with no half-state). **Job metrics:** the worker
> keeps thread-safe counters (`processed/indexed/deleted/rejected/dead_lettered/
> duplicates/malformed`) exposed via `stats()`. **10k-doc throughput load test:**
> `scripts/load_test_ingestion.py` pushes N synthetic docs through submit →
> queue → worker → store with a torch-free fake embedder and gates on zero
> failures + a throughput floor; measured 10k docs at **~1,115 docs/sec, 0
> failures** (8.97s total); CI runs a 500-doc smoke of the real script
> (`tests/test_ingestion_load.py`). Full suite 181 passed / 12 skipped (PG-gated,
> no local PG), ruff clean, foundation gates 0 blocking. **Phase 3 is complete;**
> next is Phase 4 (tenancy, auth, audit).

---

## Phase 4 — Tenancy, auth, audit (3–4 wks)

| Milestone | Deliverable | Exit criteria | Status |
|---|---|---|---|
| 4.1 | SSO/OIDC (Okta/Entra/Google) + service accounts; FastAPI `get_current_tenant` on every route; frontend login + returnTo | No endpoint reachable without auth | **Done 2026-08-29** — provider-neutral `Authenticator` seam (dev / OIDC / service accounts / chain), `get_current_tenant` on every `/api/*` route, `/api/auth/me`, frontend login gate (see status below) |
| 4.2 | RBAC: adjuster / supervisor / SIU / admin; claim-level ACLs | Permission matrix tested | **Done 2026-08-29** — `backend/rbac.py` permission matrix + `ClaimAccessPolicy` (claim-level ACLs from `CLAIM_ACLS_FILE`); enforced on every route (see status below) |
| 4.3 | Immutable audit log: upload/delete/chat/download — who, tenant, claim, query, sources returned, timestamps | Completeness test on sampled actions; chat answers + source IDs logged | **Done 2026-08-29** — the Phase-0 `JsonlAuditSink` seam is now wired to every action route (see status below) |
| 4.4 | Rate limiting, upload size caps, `/api/eval/search` gated to CI/internal, secrets via KMS | Abuse drill (huge `top_k`, giant uploads) → 429/413 | **Done 2026-08-29** — per-principal rate limiter + enforced upload/input caps (see status below); `/api/eval/search` role-gating landed in 4.2; KMS remains deployment wiring |

> **Phase 4 status (2026-08-29, milestone 4.1):** every `/api/*` endpoint is now
> behind a `get_current_tenant` FastAPI dependency that resolves the request to a
> `PrincipalContext` via a provider-neutral `Authenticator` seam (`backend/authn.py`,
> built by `_build_authenticator` in `app_factory.py`, mirroring the Phase-2/3
> blob-store/queue pattern). `AUTH_PROVIDERS` is a comma-separated chain:
> **`development`** (default — the explicit Phase-0 local identity, rejected by
> config validation in production), **`oidc`** (Bearer JWT verified against the
> issuer's JWKS: signature via the token's `kid`, plus `exp`/`iss`/`aud` checks;
> tenant/role claims configurable via `OIDC_TENANT_CLAIM`/`OIDC_ROLES_CLAIM` for
> Entra `tid`/Okta `groups` etc.; JWKS fetched lazily and cached per
> `OIDC_CACHE_TTL_SECONDS`), and **`service-accounts`** (static `X-API-Key` keys
> from `SERVICE_ACCOUNTS_FILE`, constant-time compared). `GET /api/auth/me`
> reports the resolved principal so the frontend can show who is signed in. The
> frontend attaches the stored credential to every request (`apiFetch`), shows a
> login gate on 401, and reloads after sign-in so the user returns to the page
> they were on. `/health/*` and the static frontend mount stay open deliberately
> (probes and the login surface itself); the OIDC **authorization-code redirect**
> (the "Sign in with Okta" button) is deployment wiring — the client consumes the
> resulting access token, exactly like the S3 bucket-notification → Lambda hop in
> Phase 3. OIDC is verified hermetically: tests generate a real RSA keypair, mint
> signed JWTs, and inject the JWKS fetcher — expired/wrong-issuer/wrong-audience/
> bad-signature/unknown-kid all exercised against the real verification logic
> (moto-style, no live IdP needed). Full suite 238 passed / 12 skipped, ruff
> clean, gates 0 blocking. Next: 4.2 RBAC.

> **Phase 4 status (2026-08-29, milestone 4.2):** RBAC is enforced across the
> API. `backend/rbac.py` defines the role→permission matrix (roles: adjuster /
> supervisor / siu / admin; permissions: `documents:read|upload|delete`,
> `claims:read|write`, `eval:search`; hierarchy so admin inherits everything and
> supervisor inherits adjuster) plus `ClaimAccessPolicy`, which loads claim-level
> assignments from `CLAIM_ACLS_FILE` (JSON: `{claim_id: [role or subject, ...]}`).
> admin / supervisor / siu hold all-claims access; an adjuster can only touch
> claims explicitly assigned to them. Routes now enforce: `documents:*`
> permissions on upload / delete / list / content / download; `claims:read` +
> claim access on claim-scoped document listing and chat claims; `claims:write`
> + claim access on claim-file upload; `GET /api/claims` returns **only the
> claims the principal may see** (`filter_claims`) so the queue itself is
> ACL-filtered; `/api/eval/search` is gated to `eval:search` (siu/admin).
> `require_role` / `require_tenant_scope` remain the low-level primitives.
> Permissions map to 403 with the role name in the detail. Verified hermetically:
> 39 new tests — matrix/hierarchy/ACL-file unit tests plus an API-level matrix
> (OIDC principals minted for each role; assigned vs unassigned adjuster both
> directions; dev default untouched). Full suite 279 passed / 12 skipped, ruff
> clean, gates 0 blocking. Next: 4.3 audit.

> **Phase 4 status (2026-08-29, milestone 4.3):** the Phase-0 `JsonlAuditSink`
> seam (append-only JSONL: atomic temp-file + fsync + rename, so a crash never
> leaves a partial record and events are never rewritten in place) is now wired
> to every action route via a single `_audit(request, principal, event, …)`
> helper. Every event carries **who** (`subject`), **tenant** (`tenant_id`),
> `request_id`, a UTC `recorded_at` timestamp, and an `outcome`:
> **upload** (indexed / queued / failed — with filename, optional claim_id,
> file_size, chunks_count, job_id) on `/api/upload` and `/api/upload-claim-file`
> in both sync and async modes, **delete** (deleted / queued / not_found —
> filename, job_id) on `/api/delete`, **chat** (query, claim_id, engine,
> **answer**, and the **source filenames returned**) on `/api/chat`, and
> **download** (served / redirected / not_found — filename) on
> `/api/documents/download/{filename}`. Failed attempts (unsupported format,
> empty/corrupt document, missing file, invalid filename) are recorded too, so
> the log is a complete who-did-what trail rather than only successes. The log
> lives at `AUDIT_LOG_PATH` (default `audit.log.jsonl`, gitignored); built by
> `_build_audit_sink` in `app_factory.py` (mirrors the other builders) and
> exposed on `AppDependencies`. Verified hermetically: 11 new API-level
> completeness tests drive sampled actions through TestClient and assert the
> full event stream — who/tenant/claim/query/answer/sources/timestamps per
> action, append-only ordering, no partial writes — plus `read_events()` on the
> sink and 2 config cases. Full suite 292 passed / 12 skipped, ruff clean,
> gates 0 blocking. Next: 4.4 rate limiting / upload caps / KMS.

> **Phase 4 status (2026-08-29, milestone 4.4):** rate limiting and enforced
> resource caps close Phase 4. `backend/rate_limit.py` adds a per-principal
> `SlidingWindowRateLimiter` (injectable clock, mirroring the OIDC/queue clock
> pattern; distributed state like Redis is deployment wiring on the same axis
> as SQS-vs-loopback). It is applied via a `_rate_limit_429` guard on the
> sensitive/costly endpoints — `/api/chat`, `/api/eval/search`, both upload
> routes, `/api/delete`, and `/api/documents/content` — keyed by
> `tenant_id:subject` (configurable `RATE_LIMIT_MAX_REQUESTS` /
> `RATE_LIMIT_WINDOW_SECONDS`, defaults 60/60s), answering **429** with a
> `Retry-After` header when a principal exceeds its sliding-window allowance.
> Uploads now enforce the previously-unused `MAX_UPLOAD_BYTES`: each file is
> read up to the cap + 1 byte and answered **413** when over, on both
> `/api/upload` and `/api/upload-claim-file` in sync and async modes (an
> oversized file is never staged or enqueued). `/api/eval/search` finally
> calls the previously-dead `SearchRequest.validate_limits`, so a huge `top_k`
> / oversized query answers **413** before any embedding work. `S3_SSE_KMS_KEY_ID`
> covers secrets-at-rest for object storage; as with the OIDC redirect (4.1)
> and the S3 bucket-notification hop (3.1), the general KMS integration is
> deployment wiring and the milestone's "secrets via KMS" is scoped to that
> SSE-KMS config already in place. The abuse drill passes hermetically: giant
> uploads / huge `top_k` → **413**, an intra-window burst on chat/delete/eval
> → **429** that recovers after the window elapses, and per-principal limits
> stay isolated. 21 new tests: 8 sliding-window unit (window boundary,
> partial-window pruning, burst-without-counting, multi-key isolation, reset,
> construction), 7 API-level abuse (sync+async / claim upload 413, huge
> `top_k`/query 413, in-limit top_k not falsely limited, burst → 429 +
> `Retry-After`, window recovery, per-principal isolation), 3 config
> parsing/validation cases (the existing 2 `validate_limits` unit tests
> retained). Full suite **313 passed / 12 skipped**, ruff clean, gates 0
> blocking. **Phase 4 is complete** — next is Phase 5 (serving & LLM layer).

**Note:** `tenant_id` + RLS land in Phase 1, *before* real tenants exist — retrofitting
RLS onto live multi-tenant data is the most expensive mistake in this plan.

---

## Phase 5 — Serving & LLM layer (3–4 wks)

| Milestone | Deliverable | Exit criteria | Status |
|---|---|---|---|
| 5.1 | LM Studio → vLLM/TGI (self-hosted GPU) or hosted OpenAI-compatible endpoint; `engine` stays a server-side allowlist (SSRF constraint intact) | Planner + synthesis via new backend; model cache adapted | **Done 2026-08-29** — provider-neutral `ChatClient` seam (`backend/llm_client.py`), per-stage model catalog, wiring (see status below) |
| 5.2 | Dedicated cross-encoder reranker service (GPU, batched); pgvector returns top 50–100 candidates, rerank cuts to `top_k` | Rerank cost bounded by candidate pool, not corpus | **Done 2026-08-30** — provider-neutral `Reranker` seam, remote HTTP adapter, local fail-open fallback, configurable candidate pool, and hermetic verification complete |
| 5.3 | `/api/chat` streamed (SSE) or worker-pool async; context assembly capped (dossier cap + global-match cap) | p95 time-to-first-token target; prompt-injection delimiters in place | **Done 2026-08-30** — `/api/chat/stream` SSE endpoint, capped + injection-delimited context assembly, `ttf_ms` reporting, frontend streaming reader with JSON fallback (see status below) |

> **Phase 5 status (2026-08-30, milestone 5.3 — Phase 5 complete):** the LLM,
> reranker, and streaming/context-cap seams are all done; Phase 5 (serving &
> LLM layer) is closed out. `backend/llm_client.py`'s `ChatClient` gained
> `complete_stream()` (abstract on the ABC; `OpenAICompatibleClient` posts
> `stream: true` and parses OpenAI-compatible SSE `data:` lines into content
> deltas, terminating on `[DONE]`, wrapping transport failures in
> `ChatClientError`) with the same stage-based timeout selection as
> `complete()`. `config.py`/`.env.example` add `CONTEXT_MAX_CLAIM_CHUNKS`
> (default 8), `CONTEXT_MAX_GLOBAL_MATCHES` (default 4, was hardcoded `:4`),
> and `CONTEXT_MAX_PROMPT_CHARS` (default 60000). `AgenticRAGRouter` gained
> `_assemble_context()` — builds the capped, `<source file="..." score="...">`
> -delimited prompt (dossier chunks first, uncapped by score; then the
> highest-scored global matches up to the cap; trims lowest-scored global
> matches first, then hard-truncates, to respect the char budget) with a
> system-prompt rule that source-block text is data, never instructions.
> Filename/content are HTML-escaped (`<` → `&lt;`, `"` → `&quot;`) before
> interpolation so a document containing a literal `</source>` or
> `<source ...>` can't break out of its own delimiter — a real bug found and
> fixed mid-implementation (initial version was vulnerable; a regression test
> with a crafted `</source>` embedded in content now locks in the escaping,
> and the existing containment test was strengthened from three independent
> substring checks to an actual open-before-content-before-close ordering
> assertion). Planning/retrieval/self-correction was extracted out of
> `_run_online_agent` into a shared `_online_pipeline()` so the JSON
> (`run_query`/`_run_online_agent`) and new streaming (`run_query_stream`)
> entrypoints share one implementation and can't drift — including the
> two-branch self-correction fallback (claim-scoped query rewrite vs. global
> retry) that fixed the 2026-07-17 zero-context hallucination bug, preserved
> exactly. `run_query_stream` yields `{"type": "chunk"}` events as
> `complete_stream()` tokens arrive, then one `{"type": "final"}` event with
> the joined answer, sources, engine, pipeline logs, and `ttf_ms`
> (time-to-first-token). New `POST /api/chat/stream` in `backend/app.py`
> mirrors `/api/chat`'s auth/RBAC/rate-limit checks synchronously *before*
> the generator starts (so 401/403/429 stay normal HTTP statuses, never
> swallowed mid-stream) and audits the assembled final answer exactly once,
> on the `final` event. Frontend (`frontend/app.js`, `app.js?v=1.0.8`):
> `apiFetchStream` reuses `apiFetch` for credential-attach/401-handling
> (fixed post-review — an earlier version duplicated that logic instead of
> reusing it) and only layers on non-ok → throw before reading the SSE body;
> token deltas render incrementally into an open assistant bubble via the
> existing `formatMarkdown` escape-first path; the final event stamps
> sources/engine/pipeline-logs through the same `logChatPipeline()` helper
> the JSON path uses; a 5s no-bytes-or-error window falls back to the JSON
> `/api/chat` call; a Stop button cancels the SSE reader; simulated engine
> keeps the JSON path unchanged. Verified hermetically (fake HTTP / stub
> clients / stubbed router, no ML/network): 12 new tests — context-cap
> assembly + dossier/global-match precedence, `complete_stream` SSE parsing
> (2), `run_query_stream` chunk-then-final ordering, `/api/chat/stream` SSE
> emission + audit (2), prompt-injection containment + delimiter-escaping
> regression (2), `ttf_ms` reported and precedes `final` + a p95-leniency
> statistical guard (2), config parsing (2) — plus a pre-flight review
> catch: the plan's scripted-latency TTF test took a `monkeypatch` fixture
> but never called `monkeypatch.setattr`, which would have sent 20 requests
> through the real agentic router / embedding engine / vector store; fixed
> before landing to stub the router the same way the other streaming tests
> do, keeping the whole suite hermetic. Full suite **354 passed / 12
> skipped**, ruff clean (5 lint findings surfaced and fixed during this
> Task 8 close-out verification pass: an unused local in the truncation
> loop, an unused import in two test files, and two semicolon-joined
> one-liners in a third), gates 0 blocking, `node --check frontend/app.js`
> clean, `compileall` clean. **Phase 5 is complete** — next is Phase 6
> (scale, drift monitoring, compliance, cutover).
> `backend/llm_client.py` adds a provider-neutral `ChatClient` ABC (`models()`,
> `complete()`) with one `OpenAICompatibleClient` that POSTs
> `/v1/chat/completions` and GETs `/v1/models` against the allowlisted
> `LLM_BASE_URL` (LM Studio locally; a private vLLM/TGI/SGLang gateway in
> production), with **per-stage model routing** — `PLANNING_MODEL` /
> `SYNTHESIS_MODEL` / optional `EVAL_MODEL`, each falling back to `LLM_MODEL` —
> via `model_for_stage(stage)`. `LLM_API_KEY` (Bearer, injected from secrets)
> is attached only when set; the 10s `MODEL_CACHE_TTL_SECONDS` model cache moved
> out of the router into `client.models()`, and the router's hardcoded
> `requests.post` / `engine_url` / in-router cache are gone. `engine` stays a
> strict server-side allowlist (`simulated` | `lm-studio`) — a URL/unknown value
> is rejected with zero outbound calls (SSRF regression tested). Config:
> `planning_model`/`synthesis_model`/`eval_model`/`llm_api_key`/
> `llm_synthesis_timeout_seconds`/`llm_plan_timeout_seconds`/
> `model_cache_ttl_seconds` (`.env.example`), built by `_build_llm_client` in
> `app_factory.py` (mirrors the other builders) and exposed on
> `AppDependencies`; `/api/status` probes via `client.models()` and `/api/chat`
> threads the client through `run_query(llm_client=…)`. Verified hermetically
> (fake HTTP / stub clients, no ML/network): 13 new tests — client contract,
> per-stage resolution, bearer-auth, TTL caching, failure-decay, factory
> wiring, router planner/synthesis through the injected client, engine
> allowlist, `/api/status` — reconstituting the existing router tests against
> the injected seam. Full suite **329 passed / 12 skipped**, ruff clean, gates 0
> blocking. Next: 5.2 reranker service.

---

## Phase 6 — Scale, drift monitoring, compliance, cutover (3–4 wks)

| Milestone | Deliverable | Exit criteria |
|---|---|---|
| 6.1 | k6/Locust load test: 100k+ docs, multi-tenant, concurrent users | p95 retrieval < 100 ms (HNSW); ingest throughput sustained |
| 6.2 | Eval → drift monitoring: golden suite (expanded per tenant) scheduled against production retrieval; alert on recall/faithfulness regression | One-off `eval/run_eval.py` becomes a live signal |
| 6.3 | Compliance: encryption in transit/at rest, retention/deletion (S3 lifecycle + PG archival), access reviews, incident runbook | Evidence pack for SOC2-type review |
| 6.4 | Cutover: both backends feature-flagged, parity in staging, blue/green, rollback drill | Production on Postgres; rollback < 1 hr |

---

## Deferred findings (close-out record, 2026-08-28)

| Finding | Owner | Reason | Review date |
|---|---|---|---|
| Phase 6 (scale, drift monitoring, compliance, cutover) + deployment-wiring items (live S3→Lambda→SQS hop, live OIDC redirect, real KMS, Redis rate-limit state, connecting a bought/built model on the company's GPU gateway, golden eval against a live judge, SOC2-type evidence) | Repository maintainer (Jared Fisher) | Sequenced roadmap; hermetic code lands first, then live-infrastructure + compliance verification (needs provisioning and human/security review, not just code) | Start of each milestone (next: Phase 6) |
| Dependency audit (`pip-audit`) wired into the harness | Repository maintainer (Jared Fisher) | Plan Task 8 listed it as a P0 gate; the shipped harness runs secrets/specs/docs gates, and CI runs the isolation/grounding tests — the audit remains advisory until wired | Phase 1 planning |
| Static security analysis (bandit) as a harness sensor | Repository maintainer (Jared Fisher) | Plan Task 8 listed it as a P1 sensor; not yet invoked by the harness | Phase 1 planning |
| Docker packaging | Repository maintainer (Jared Fisher) | Optional distribution work; Phase 5 serving work (5.1-5.3) is now complete without it | Phase 6 / release packaging |
| Golden evaluation with a live LM Studio judge | Repository maintainer (Jared Fisher) | Requires a running local model server; automated suite covers the remaining release checks | Before first production release |

## Order rationale & effort

0 (parity) → 1 (data plane, incl. `tenant_id`) → 2 (S3) → 3 (async ingest — writes
to the Phase-1 schema) → 4 (auth/audit — before real tenants) → 5 (serving) →
6 (prove + cut over). Roughly **4–6 months for 1–2 engineers**.

## Top risks

1. **HNSW recall drift** vs brute-force SQLite — mitigated by Phase-0/6 parity harness + HNSW tuning (`m`, `ef_construction`).
2. **Embedding model change** = full re-embed — pin the model and add a `model_id` column to version embeddings.
3. **LLM latency variance** at production concurrency — streaming + queueing + revisiting the 120s timeout under load.
4. **Regulatory/compliance scope** for insurance data — audit trail and retention designed in (Phases 4/6), not bolted on.
