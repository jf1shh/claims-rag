# Threat model

## What this project does and where untrusted input enters
ClaimsRAG is a FastAPI service (`backend/api.py`, served by `backend/app.py`) plus a static
frontend (`frontend/`) for retrieval-augmented question answering over auto-insurance documents.
All data in the repository is synthetic. One application instance serves one tenant.

Untrusted input enters through:
- HTTP requests to `/api/*`: document upload (`/api/upload`, `/api/upload-claim-file`), document
  listing/content/download by filename (`/api/documents/...`), delete, jobs, chat (`/api/chat`,
  `/api/chat/stream` over SSE) and `/api/eval/search`. Every `/api/*` route authenticates via
  `backend/authn.py` (OIDC JWT or `X-API-Key` service keys) and is authorized by `backend/rbac.py`
  (roles plus claim-level ACLs) and `backend/tenant_context.py`.
- Uploaded document bytes (PDF, DOCX, XLSX, TXT, and ZIP-based Office containers), parsed in
  `backend/parse_document.py` / `backend/ingestion.py` with limits in `backend/document_limits.py`
  and `backend/body_limits.py`. Complex formats are parsed in a subprocess with a wall-clock timeout
  and, on POSIX, a 2 GiB address-space limit.
- Text retrieved from indexed documents, which is passed to an LLM. Retrieved text is treated as
  untrusted (prompt injection): `backend/prompt_defense.py`, `backend/answer_guard.py` and
  `backend/conflict_check.py` sit around generation in `backend/agentic_router.py`.
- Filenames and claim IDs used to build storage paths (`backend/source_storage.py`,
  `backend/blob_store.py`, filesystem or S3).
- Optional S3 event messages (`backend/s3_events.py`) and the job queue/worker
  (`backend/queue.py`, `backend/ingestion_worker.py`, `backend/job_store.py`).

Trusted: server configuration and environment (`config.py`, `.env`), configured model/reranker
endpoints (request data cannot choose an outbound URL), the operator, and the database itself.

## Components that matter most / least
Most important:
- Authentication, authorization and tenant isolation: `authn.py`, `rbac.py`, `tenant_context.py`,
  and Postgres row-level security in `postgres_store.py` / `alembic/versions/`. Any bypass that lets a
  principal read, download, ingest into, or delete documents outside their role, claim ACL or tenant.
- Path handling for uploads, downloads and deletes (traversal or escape from the storage root),
  including versioned citation downloads (`?version=`).
- Document parsing and upload limits (zip bombs, decompression size, parser resource exhaustion that
  escapes the subprocess limits, request-body limit bypass).
- SQL construction in `rag_engine.py` (SQLite) and `postgres_store.py`.
- Answer guard / prompt-defense bypasses that cause an answer to assert an unsupported claim decision
  or emit a URL/email outside `ANSWER_GUARD_ALLOWED_DOMAINS` without being withheld.
- Audit log integrity (`audit.py`) and whether it leaks question/answer text.

Less important / out of scope:
- `eval/`, `scripts/`, `generate_auto_pdfs.py`, `create_sample_files.py`, `ingest_all.py`,
  `pg_migrate.py`: operator-run tooling and evaluation harnesses, not exposed over HTTP.
- `docs/` (including `docs/agent-work/`), `stages/`, `_config/`: documentation only.
- The development authentication profile (`APP_ENV=development`/`test`) deliberately grants an
  administrator principal without credentials; this is documented and is not a finding unless it can be
  reached in `staging`/`production`.
- Model quality: a model that ignores delimiters or produces a wrong but guarded answer. Known,
  measured limits are in `docs/adversarial-evaluation.md` (e.g. attacks written with knowledge of the
  sanitizer still get through); report only a concrete bypass of a control's stated behavior.
- The in-process rate limiter is per-process by design, not distributed.

## How to exercise it
- `python -m pytest tests/ -q` runs the suite offline (no models, no network; Postgres-gated tests
  skip unless `POSTGRES_DSN` is set). `tests/conftest.py` has the `isolated_api_runtime` fixture that
  keeps real routing/auth/storage and replaces only the embedding model, which is the easiest way to
  drive the API with `fastapi.testclient.TestClient`.
- Relevant suites: `test_api_auth.py`, `test_api_rbac.py`, `test_api_limits.py`, `test_authn.py`,
  `test_rbac.py`, `test_tenant_context.py`, `test_blob_store.py`, `test_prompt_injection.py`,
  `test_answer_guard.py`, `test_ingestion_*.py`.
- The app can run with `SIMULATION_MODE=true` (no live LLM). Embedding/reranker models are
  pre-cached in the image.

## How you rate severity
- Critical: unauthenticated access to any `/api/*` data or action in a non-development profile;
  cross-tenant data access; remote code execution; arbitrary file read/write outside the storage root.
- High: authorization bypass within a tenant (reading or downloading a claim's documents without its
  ACL, an adjuster performing supervisor/admin actions, deleting documents without permission); SQL
  injection; path traversal limited to reads inside the app directory; serving replaced bytes for a
  pinned citation version.
- Medium: denial of service that bypasses a documented limit (body, upload, ZIP expansion, parser
  timeout/memory); prompt-injection that defeats the answer guard's stated withhold behavior;
  audit log record loss or leakage of chat text into the audit log.
- Low: information disclosure in error messages, missing hardening headers, issues only reachable by
  the operator through configuration.

## Anything to leave alone
- Synthetic sample data and evaluation result JSON files.
- Findings that require a malicious configured model endpoint, S3 bucket, OIDC issuer or database
  operator (these are trusted).
- Dependency CVEs with no demonstrated reachable path in this code.
- Please give a minimal synthetic reproduction (a pytest case using `isolated_api_runtime` is ideal)
  and a patch with a regression test.
