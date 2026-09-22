# Portfolio hardening — September 6, 2026

This remediation addresses the implementation findings from the maintainer's repository review. AutoClaimsRAG remains a synthetic claims research portfolio project. Passing these checks does not certify claims correctness or a production deployment.

## Implemented changes

| Area | Result |
| --- | --- |
| Authorization | One configured tenant per application; mismatched principals are denied. Claim ACLs cover actual document content/downloads and jobs. An explicit empty ACL denies adjusters. Signed access tokens require expiration and identity claims. |
| Application lifecycle | The documented ASGI entry point uses the same factory as tests, including Postgres selection, app-owned services, and a shared ingestion producer/worker queue. Lazy embedding and reranker initialization and rate-limit updates are synchronized. |
| Source consistency | New sources have immutable storage keys and SHA-256 versions. Source staging precedes the index/reference transaction; failed replacement preserves the previous index and source. Citation content/download requests can reject stale versions with HTTP 409. |
| Resource limits | Request bytes, queries, expanded archives, and extracted text are bounded. Complex document parsers run in a subprocess with a wall timeout and a POSIX memory limit. Plain text uses bounded decoding without subprocess startup overhead. |
| Background ingestion | Each upload stages distinct bytes; workers verify the checksum, apply parsing limits, retry transient storage failures, and remove staging after successful indexing. |
| Audit/privacy | Cooperating processes append under a file lock, fsync writes, and recover a torn final record. API audit records omit full questions and answers; operational metadata can still be sensitive. Errors and status responses omit internal paths/provider details. Browser API responses are not cached. |
| Evidence/functionality | Simulation is enforced server-side in JSON and streaming paths. Context is checked again after trimming. Responses include a structured evidence projection, source/chunk/version identifiers where available, and the exact supplied excerpts. Live retrieval honors configured rerank settings. |
| Browser | API-key and bearer authentication use the correct headers; credentials are validated and stored for the browser session. Legacy persistent tokens are cleared. External fonts are removed. Dialog focus handling and labels improve keyboard access; ranking scores are no longer presented as confidence percentages. |
| Packaging/CI | Docker uses explicit runtime copies, excludes environment/secret files, and Compose binds to loopback. Demo seeding includes its PDF dependency and uses temporary generated inputs. PRs and public runs use hosted CI; only private pushes may use the local runner. |
| Evaluation honesty | Metric summaries include scored/failed counts and identify reference-coverage correctness. Parity embeddings are deterministic. Historical performance and answer-quality numbers are labeled separately from current verification. |

## Verification performed

- Full suite with a disposable pgvector/Postgres 16 database: **421 passed, 1 skipped**, with one upstream Starlette/AnyIO deprecation warning.
- `ruff check .` and `git diff --check`: clean.
- Foundation gate: **27 advisory findings, 0 blocking**. Existing ragas/diskcache vulnerability exceptions remain; this is not a clean vulnerability scan.
- Deterministic Postgres/SQLite retrieval parity: mean recall@4 **0.9808** against a 0.90 floor; exact match **0.9231**.
- Alembic migration: upgrade to `0002`, downgrade to `0001_initial`, and upgrade again against a disposable database.
- Docker image rebuilt; a fresh volume seeded successfully with `--network none`, then the container served health and indexed-document endpoints. Image inspection confirmed environment files and Git metadata were absent.
- Chromium browser check: API-key login, session-only credential storage, signout, no external requests, and no JavaScript errors. This is a functional check, not a complete accessibility audit.
- Regression coverage includes actual factory isolation and Postgres selection, claim/tenant denial on real files, async worker lifecycle, failed blob replacement, stale citations, missing token expiration, parsing limits/timeouts, post-trimming refusal, thread/process audit concurrency, torn audit recovery, and safe orphan collection.

### Fresh-runner CI follow-up

The first hosted PR run exposed 11 API contract tests that still acquired the real embedding model before calling a stubbed router. A populated local model cache concealed this test-isolation defect. Reproduced with an empty `HF_HOME` and offline model settings: **11 failed, 29 passed** in the affected modules. An explicit opt-in fixture now supplies deterministic embeddings, temporary SQLite/document/audit storage, and a guard against model construction; authorization and routing remain real. The complete no-Postgres suite then passed with the empty cache: **405 passed, 17 skipped**. CI explicitly disables model downloads. The separate Postgres/Locust smoke server also required cached models. It now opts into an explicit synthetic-model entry point that preserves HTTP, authentication, and Postgres retrieval. Synthetic mode requires `--no-assert-p95` and prints that its timings are not production latency evidence; the default benchmark still uses real models. Full local Postgres verification with the empty cache then passed: **422 passed, 1 skipped**, including the HTTP/Locust smoke and the new synthetic-mode guard. Ruff remained clean.

## Upgrade and operation

Stop writers and back up both database and document storage together. For Postgres, set `POSTGRES_DSN` and run `alembic upgrade head` before starting the updated application. SQLite adds the two source-reference columns automatically. Existing documents remain readable through the legacy filename fallback and are explicitly unversioned until reingested. New/replaced documents gain source hashes and immutable keys.

Do not downgrade after writing versioned documents without restoring a matching database/source backup: dropping the reference columns discards the mapping to immutable source keys. The migration roundtrip test verifies schema mechanics on a disposable database, not a safe data downgrade.

Credentials must carry the application's configured `TENANT_ID`. Configure real claim grants for adjusters; staging/production without grants fails closed. Simulation must be explicitly enabled to use demo answers. A `.env` file is not implicitly loaded by Python: use the documented `uvicorn --env-file .env` command or export settings through the deployment environment. Development identity is only suitable for the loopback synthetic demo.

Replacements and failed publication can leave unreferenced immutable blobs. With API and workers stopped, `python scripts/collect_source_garbage.py` reports their count; inspect the environment/storage target, then use `--apply` to remove them. The collector preserves currently referenced sources and does not purge pending job staging. Define an operator retention policy for failed jobs, staging, backups, old legacy blobs, and audit metadata. Deleted/reclaimed versions are not a permanent evidence archive.

## Remaining evidence and deployment work

**Partly closed 2026-09-22 — the golden suite was rerun live.** The generator/judge was loaded (`qwen3-coder-30b-a3b-instruct`, local, 32k context) and the full 19-query suite ran against the live router with `SIMULATION_MODE=false`: 1965.1 s, all 19 queries scored on every metric, zero scoring failures. Context precision naive 0.727 → hybrid 0.832; context recall naive 0.895 → hybrid 0.965; live faithfulness 0.892; factual correctness 0.797 (mode=recall). Recorded in `eval/results.json`.

Read those against the 2026-09-03 run with care. Faithfulness (0.812 → 0.892) and correctness (0.749 → 0.797) improved, but context precision fell in **both** arms by near-identical amounts (−0.053 hybrid, −0.052 naive). The 2026-09-13 reranker batching change affects only the hybrid arm and therefore cannot account for a matched drop in the naive baseline, which points at LLM-judge variance or corpus drift rather than a regression. **This is one run of an LLM-judged suite and is not sufficient to claim either.** A confirming run is still owed.

**Still open:** adversarial and human-reviewed cases were *not* run. The suite covers one refusal probe; it does not systematically cover prompt injection through document content, or queries where retrieved sources genuinely conflict (a policy endorsement against a state statute) — a common shape in real claims work and an unmeasured behavior here. Citation identifiers and prompt delimiters improve traceability; they do not validate each assertion, prove relevance, or prevent every prompt injection. No arbitrary retrieval-score cutoff was introduced without evaluation.

Rerun performance measurements against the corrected Postgres application path before reusing historical latency claims. Real IdP integration, distributed quotas, infrastructure isolation, storage encryption/key management, audit access/retention, and backup recovery require deployment-specific validation. The local audit log is concurrency-safe for cooperating writers, not tamper-proof/WORM storage. Remote provider configurations can transmit data outside the machine. Keep synthetic data in the portfolio demo until those controls and model-quality evidence are established.
