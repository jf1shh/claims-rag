# M12 audit: ClaimsRAG at 60c0111, read-only

I found 25 issues: 1 high, 11 medium and 13 low. The worst is the S3 event bridge: once bucket notifications are wired up, the app re-ingests its own copies of every document in an endless loop. Of the items listed as closed, one wasn't fixed for Postgres: the short `add_document` transaction (finding 7). The other closed items I traced are still fixed. I ran no tests or probes. The only check outside the repo code was reading the installed Starlette source to confirm that the server re-raises errors after the 500 handler runs.

## Findings (highest severity first)

1. - [high] backend/s3_events.py:45-52, 84-86 — The bridge treats any key shaped `{tenant}/{scope}/{rest}` as an upload. But the app writes its own objects under the same tenant prefix: `versions/<uuid>/<file>` (source_storage.py:18) and `staging/<uuid>/<file>` (api.py:844). Those come through as fake claims named `versions` and `staging`. **Scenario:** bucket notifications are wired as `handle_event` expects, with no prefix filter (none is documented). Claim A uploads `loss_statement_Brooks.pdf`. The worker indexes it again as claim `"versions"` with filename `<uuid>/loss_statement_Brooks.pdf`, which `safe_filename` turns into `loss_statement_Brooks.pdf`. That indexing writes a new `versions/<uuid2>/…` object, which fires another event, so it loops forever. Every claim's documents collect in one fake claim that any admin, supervisor or SIU user can chat over, and deleting the original never removes that copy. A `staging/` event also races the real job: whichever job finishes first deletes the staging blob, and the other is marked failed with `BLOB_NOT_FOUND`. **Fix:** accept only an explicit inbound prefix such as `inbound/{scope}/…`, reject the reserved scopes `versions` and `staging`, check claim ids against known claims, and document an S3 notification prefix filter. (PLAUSIBLE: the code path is traced, but nothing in the repo wires notifications yet.)

2. - [medium] backend/api.py:612-638, 868-896, 733-741 — Several reads of claim data leave no audit record:
   - `GET /api/documents/content/{filename}` returns a claim document's full text.
   - `POST /api/eval/search` returns raw chunk text.
   - `GET /api/documents/claim/{id}` lists a claim's files.
   - Claim-access refusals on chat, stream and content never reach `_audit`; only upload, download and delete record denials.

   **Scenario:** an adjuster reads every document on an assigned claim through the content endpoint, and nothing is logged. Repeated 403s while probing other claims' ids aren't logged either. **Fix:** call `_audit` (outcome served / denied / not_found) on all of these. (CONFIRMED)

3. - [medium] backend/rag_engine.py:455-458, 952-954 — The vector cache lives inside each process. `_invalidate_vector_cache` only clears the cache of the process that made the change, and nothing checks the database for changes made elsewhere (no `PRAGMA data_version`). **Scenario:** SQLite run with `uvicorn --workers 4` (the docs show this was tried). A delete handled by worker 1 leaves the document's chunks in workers 2-4. They keep putting deleted text into chat context and sources until they make a write of their own. **Fix:** compare a generation value stored in the database (or `PRAGMA data_version`) before using the cache, or refuse more than one worker on SQLite. (CONFIRMED code path; depends on deployment)

4. - [medium] backend/ingestion_worker.py:171-175 (and the dead-letter returns at 158-166, 279-308, 322-351); scripts/collect_source_garbage.py:4-5, 30-33 — Staging blobs are deleted only when indexing succeeds. Uploads that are corrupt, empty, rejected, fail their checksum or run out of retries keep their staged bytes. The garbage collector deliberately skips staging (it scans only `versions/`), and the in-process queue loses messages on restart. **Scenario:** a password-protected claimant PDF is dead-lettered with `CORRUPT_FILE`. Its bytes stay in `stored_documents/ingest_queue/staging/…` or `s3://…/tenant/staging/…` forever. **Fix:** delete staging on every final outcome, and have the collector remove staging keys whose job has finished or is older than a TTL. (CONFIRMED)

5. - [medium] backend/rag_engine.py:730-735; backend/postgres_store.py:135-142 — Re-uploading a file in the same scope deletes the old database row but never the old `storage_key` blob. Old versions are reclaimed only by running the collector by hand, with writers stopped. **Scenario:** an adjuster replaces a statement that held the wrong person's PII. The old bytes stay in storage until someone runs that offline script. **Fix:** after commit, delete the replaced `storage_key` (the same way `delete_document` does), and schedule the collector. (CONFIRMED)

6. - [medium] backend/job_store.py:54-69; no code path for retention — There is no way to delete a whole claim and no expiry for documents, job rows, staging or audit records. Job rows keep `filename`, `claim_id` and the file's `sha256` forever, and `GET /api/jobs/{id}` still returns them after the document is deleted. **Scenario:** a claim closes and policy requires purging after N years. There is no supported way to do it, and the job store can still confirm (via the hash) that a given file was uploaded. **Fix:** add a claim-purge operation covering documents, versions, staging and jobs, plus age-based retention for jobs. (CONFIRMED)

7. - [medium] backend/postgres_store.py:129-190 — **Not fixed for Postgres.** The short-transaction fix was applied only to SQLite. On Postgres, `add_document` runs `DELETE FROM documents …` first, then chunks and embeds every child inside the open transaction, then uploads to S3, then commits. **Scenario:** a 2M-character upload holds the old document's row lock and an open transaction for the whole embedding run. A delete or re-upload of that document blocks with no lock timeout, request threads pile up, and VACUUM is held back. **Fix:** chunk and embed before `_connect()`, as `SQLiteVectorStore.add_document` now does. (CONFIRMED)

8. - [medium] backend/postgres_store.py:94-109 — Every store call opens a new connection (`psycopg.connect`) with no pool, no `connect_timeout` (libpq waits forever by default) and no `statement_timeout`. One chat opens about 3-6 connections, and an authorized delete opens 3. **Scenario:** the database stalls or a network path drops. Request threads hang inside `connect`, the 8 chat slots and the thread pool fill up, and `/health/ready` hangs too. **Fix:** use a `psycopg_pool.ConnectionPool` with `connect_timeout` and `options=-c statement_timeout=…`, and set `app.tenant_id` when a connection is checked out. (CONFIRMED)

9. - [medium] backend/api.py:744-754; backend/llm_client.py:107; provenance_check.py:217; conflict_check.py:183 — Non-streaming `/api/chat` has no overall deadline. Planning (5 s), synthesis (120 s), the provenance check (120 s, because any stage other than `planning` gets the synthesis timeout) and the conflict check (120 s) run back to back. `requests` timeouts also only limit each read, not the whole call. **Scenario:** a slow or byte-trickling LLM gateway holds a chat slot for 6+ minutes per request; 8 such requests return 503 to everyone. **Fix:** give each chat an overall deadline, pass the remaining time to each stage, and give the check stages their own short timeout. (CONFIRMED)

10. - [medium] backend/queue.py:187, 219 — The SQS visibility timeout is fixed at 30 s and is never extended, but parsing alone may take 120 s (`request_timeout_seconds`) before embedding starts. **Scenario:** two API replicas in async mode, each running the worker thread from lifespan. A large document stays in progress past 30 s, replica B receives the same message and indexes it at the same time. Replica A's ack then uses an old receipt handle, so the message comes back again. The third delivery finds the staging blob already deleted and marks the job `BLOB_NOT_FOUND` failed, even though the document is indexed. **Fix:** extend visibility while a message is being processed (or set it above the worst-case processing time), and make jobs claim messages by job_id. (PLAUSIBLE)

11. - [medium] backend/rag_engine.py:476-500, 952-954 — The vector cache rebuild isn't single-flight. Every search that finds the cache empty rebuilds it with its own `fetchall()` of every chunk embedding plus parent content. Each parent's text is also stored once per child row (about 5-7 copies). **Scenario:** right after an upload, 8 chats in flight each load the whole corpus at once, so peak memory is roughly 8× the corpus times the duplication. That can OOM the API at enterprise corpus sizes. **Fix:** rebuild once per generation under a lock and have other searches wait for it; store parent content once, keyed by parent id. (CONFIRMED)

12. - [medium] backend/document_limits.py:124-128 (called from api.py:533 and ingestion_worker.py:294 before the subprocess starts) — `ZipFile(path).infolist()` parses the whole zip index (central directory) in the API process, outside the subprocess memory limit (`RLIMIT_AS`) and wall timeout. The 10,000-entry cap is checked only after that. **Scenario:** a 25 MB `.docx` holding about 500k minimal index entries builds about 500k `ZipInfo` objects (roughly 200 MB and seconds of CPU) per upload. With 4 concurrent uploads plus the worker, that inflates memory in the main process. **Fix:** read the entry count from the end-of-archive record (EOCD/ZIP64) before building the list, or move `check_container` into the parser subprocess. (PLAUSIBLE: not measured)

13. - [low] Dockerfile:41 (CMD); config.py:199; backend/ingestion_worker.py:204, 328, 338, 349, 359 — PII reaches the logs. Uvicorn's access log is on by default and records full paths and query strings, e.g. `/api/documents/content/loss_statement_Brooks.pdf?claim_id=%232026-…`. Filenames in this corpus carry claimant surnames. The worker logs filenames and claim ids, and its warnings include exception text. `LOG_LEVEL` is parsed but never applied (there is no logging setup anywhere), so no setting controls any of this. The worker's INFO lines are dropped only by accident; uvicorn's access log is unaffected. **Fix:** configure logging from `LOG_LEVEL`, drop or redact path and query in the access log, and log job ids instead of filenames. (CONFIRMED)

14. - [low] backend/api.py:117-121, 602-603 — The 500 handler logs only the exception type, but Starlette's error middleware re-raises after the handler runs (starlette/middleware/errors.py:183-186). Uvicorn then logs the full traceback and message, and the upload path also calls `traceback.print_exc()`. **Scenario:** a psycopg unique-key error prints `DETAIL: Key (tenant_id, claim_id, filename)=(…, #2026-…, loss_statement_Brooks.pdf)`, and pydantic errors print `input_value=…`. **Fix:** turn the 500 path into a response instead of re-raising (e.g. a pure-ASGI wrapper), remove `print_exc`, and log with redaction. (re-raise CONFIRMED; PII in messages PLAUSIBLE)

15. - [low] backend/blob_store.py:158-161 — `delete_object` is called without a `VersionId`. **Scenario:** on a bucket with versioning or object lock (common for compliance), a "deleted" claim document only gets a delete marker, and every earlier version stays readable to anyone with bucket access. **Fix:** document the bucket requirements, or delete all versions of the key (`list_object_versions`). (PLAUSIBLE)

16. - [low] backend/rag_engine.py:451-453, 798-804 — SQLite runs without `PRAGMA secure_delete` and is never vacuumed. **Scenario:** deleted claim text stays in the free pages of `rag_store.db` and the FTS shadow tables, and file-level forensics or a backup copy can recover it. **Fix:** set `secure_delete=ON` in `_connect()`, and run FTS `optimize` and `VACUUM` as maintenance. (PLAUSIBLE)

17. - [low] backend/rag_engine.py:733-767 — `stage_source`, which does a 25 MB local fsync or an S3 PUT, runs after the first DELETE/INSERT and before commit, so it holds SQLite's write lock. The journal is the default rollback mode, not WAL. **Scenario:** a slow S3 PUT pushes concurrent uploads and deletes past the 30 s busy timeout, and they return 500 `database is locked`. **Fix:** stage before the transaction (the immutable keys already make this safe) and enable WAL. (CONFIRMED lock held; timeout PLAUSIBLE)

18. - [low] backend/job_store.py:44-47; backend/api.py:569-575, 601-605 — The job store uses the default 5 s busy timeout without WAL, and the API and the worker share it. **Scenario:** a sync upload commits to the index, then `record_result` hits `database is locked`. The client gets a 500 and the audit log says `failed`, but the document is live. **Fix:** WAL plus a longer `timeout=`, and write the job record in its own try block after indexing. (CONFIRMED path)

19. - [low] backend/llm_client.py:47-56, 186-197; backend/reranker.py:303-318 — Calls go through module-level `requests.post/get`, so every planning, synthesis, check and rerank call opens a new TCP/TLS connection. `models()` doesn't cache failures. **Scenario:** with `LLM_MODEL` unset and the gateway down, every `/api/status` call and every stage call pays 2×2 s of probes. **Fix:** one shared `requests.Session` with a bounded pool, and a short cache for failed probes. (CONFIRMED)

20. - [low] backend/api.py:53-57 → 788-805 → audit.py:152-179 — When a stream times out or the client disconnects, `_TimeBoxedStream` calls `generator.close()` on the event loop. That runs `gen`'s `finally`, which takes the audit file lock (`flock`), writes and fsyncs, all on the event loop. `weakref.finalize` can do the same during garbage collection. **Scenario:** a slow disk, or audit-lock contention between processes, stalls every connection. **Fix:** run the close with `anyio.to_thread.run_sync`. (CONFIRMED)

21. - [low] backend/ingestion.py:127-128, 192-193, 232-233; backend/queue.py:163 — `IngestionService._jobs` stores every job in memory even when the durable job store is configured, and `InProcessQueue._dead_letters` keeps growing. **Scenario:** a long-running API process leaks one job object per upload and delete. **Fix:** skip `_jobs` when `_job_store` is set; bound the dead-letter list. (CONFIRMED)

22. - [low] backend/api.py:392-418 — `/api/status` checks authentication only, no permission. Any principal in the tenant, including an OIDC user with no valid roles, can see the loaded model names, the backend type, the document count and the simulation flag. **Fix:** require `documents:read`, or trim the payload. (CONFIRMED)

23. - [low] app_factory.py:300; backend/api.py:135-139 — FastAPI's default `/docs`, `/redoc` and `/openapi.json` are served without auth in production. No CSP, `frame-ancestors`/X-Frame-Options or HSTS headers are sent, even though bearer tokens sit in `sessionStorage`. **Fix:** set `docs_url=None, openapi_url=None` outside development, and add those headers. (CONFIRMED)

24. - [low] backend/api.py:668 — S3 download links are presigned for 3600 s and default to inline display. **Scenario:** a claim document link copied from the browser works for an hour for anyone, with no auth. **Fix:** about 60-300 s, plus `ResponseContentDisposition=attachment`. (CONFIRMED)

25. - [low] alembic/versions/0001_initial_enterprise_schema.py:102 — Row-level security is enabled but not forced (`FORCE ROW LEVEL SECURITY` is missing), and nothing at runtime checks that the app isn't connecting as the table owner. **Scenario:** a deployment reuses the migration DSN, so tenant isolation rests entirely on the SQL `WHERE tenant_id` filters. **Fix:** force RLS, or check `current_user` against the table owner at startup. (PLAUSIBLE)

## Verified OK

- **Per-endpoint authz:**

  | Route | Checks |
  |---|---|
  | `/health/live`, `/health/ready` | public |
  | `/api/auth/me` | authn only |
  | `/api/status` | authn only (finding 22) |
  | `/api/documents` | `documents:read`; global documents only |
  | `/api/claims` | `claims:read` plus the ACL filter |
  | `/api/jobs/{id}` | tenant, `documents:read`, claim ACL when the job has a claim |
  | `/api/upload` | `documents:upload` |
  | `/api/upload-claim-file` | `claims:write` plus claim ACL, checked before reading the body |
  | `/api/documents/claim/{id}` | `claims:read` plus ACL |
  | content, download, delete | `authorize_document`; the claim ACL is checked before the lookup, so it doesn't reveal whether a file exists |
  | `/api/chat`, `/api/chat/stream` | `documents:read`, plus `claims:read` and ACL when a claim is given |
  | `/api/eval/search` | `eval:search` plus ACL |

  An empty `claim_id` falls back to global scope and leaks nothing. Staging and production without `CLAIM_ACLS_FILE` deny adjusters.
- **Closed items, still fixed in code:**
  - dev-auth TrustedHost and Origin checks
  - vector-cache generation guard
  - in-flight-only async dedupe
  - concurrency gates
  - per-scope keys in both stores
  - SQLite short transaction
  - stream audit in `finally`
  - delete authz
  - `compare_digest` on bytes
  - DOCX media budget
  - `.xls` removed from the upload allowlist
  - `CONTENT_TYPES` mapping
  - Postgres savepoints
  - JWKS stale-keys and backoff
  - sanitized filenames
  - parser environment allowlist
  - server-generated request ids
  - SIU duplicate-photo (`_internal` is never rendered)
  - `MAX_STREAM_SECONDS`
- **Privacy defaults:** the audit sink drops query and answer text. Client-facing errors (`ChatClientError`, `DocumentLimitError`, auth) are generic. `pipeline_logs` go only to the requesting user. No backend code writes to `eval/` at runtime.
- **Delete paths:** in both stores, delete covers chunks, FTS rows and the current blob (`ON DELETE CASCADE` works because `_connect` turns on foreign keys).
- **Parser hardening:** text decoding is bounded. PDF, DOCX and XLSX parsing (including deep XML and huge shared-string tables) runs in the limited subprocess with a stripped environment, and no image decoding happens. The upload body is capped both by Content-Length and while streaming.
- **Async handlers:** all route handlers are sync `def` and run in the thread pool. The middleware is pure ASGI. JWKS fetch has a timeout and a non-blocking lock. The boto3 clients are created once.
- **Secrets and images:** none in the defaults or `.env.example`. The Docker image runs as non-root, defaults to `APP_ENV=production` and refuses to start in simulation or dev-auth mode. Compose binds to 127.0.0.1.

One thing about how I ran this: plan mode asked me to write a plan file under `~/.claude/plans/`. I didn't, because the brief forbids writing outside the workspace and makes this message the deliverable. No files were changed.
