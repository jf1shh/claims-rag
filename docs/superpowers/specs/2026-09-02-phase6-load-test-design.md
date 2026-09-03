# Phase 6.1 — Retrieval Load Test at Scale (100k+ docs, multi-tenant, concurrent users)

> **Part of Phase 6 (Scale, drift monitoring, compliance, cutover) of
> `docs/enterprise-migration.md`.** Milestone 6.1: prove retrieval latency and
> ingest throughput hold up at 100k+ documents under concurrent, multi-tenant
> query load against the Postgres/pgvector (HNSW) backend.

**Status: Design (draft for review).** Believed date: 2026-09-02.

---

## 1. Context and problem

Every prior load/scale measurement in this repo covers one slice each, none of
them at the 100k-document scale the migration doc's exit criteria name:

- `scripts/load_test_ingestion.py` (Phase 3.3): ingest throughput only, 10,000
  docs, **SQLite** backend, torch-free fake embedder. Proves the queue → worker
  → store pipeline doesn't degrade under bulk write load. Says nothing about
  query-time retrieval latency, and SQLite's vector leg is brute-force (no ANN
  index), so it cannot speak to the HNSW behavior Phase 6.1 is actually about.
- `eval/parity_runner.py`: correctness (recall@k) on the ~50-document real
  corpus, single query at a time, no latency/concurrency dimension at all.
- The Phase-1 close-out measured Postgres/HNSW correctness (parity 0.98 @
  tolerance 0.9) but never latency or concurrent load — the HNSW index was
  built and proven correct, never proven *fast under load*.

Phase 6.1's exit criteria are explicit: **p95 retrieval < 100ms (HNSW)** and
**ingest throughput sustained** at 100k+ docs, multi-tenant, concurrent users.
This spec covers exactly that gap: a retrieval-latency load test that didn't
exist before, plus re-confirming ingest throughput at 10x the previously
measured scale.

## 2. Goals

- A **retrieval load test** driving concurrent simulated users' queries against
  the real `/api/chat` (and/or `/api/eval/search`) HTTP endpoints of a running
  server backed by `PostgresVectorStore`, over a **100k+ document, multi-tenant**
  corpus, reporting p50/p95/p99 latency and pass/fail against the 100ms p95 bar.
- Reuse `scripts/load_test_ingestion.py` (bump `--count` to 100k+, point at
  Postgres) to re-confirm ingest throughput at the larger scale, rather than
  building a second ingest harness — Phase 3.3 already proved that tool sound.
- A **fast, hermetic bulk-seed path** to reach the 100k-doc starting state for
  the retrieval test without re-running the full parse/chunk/embed pipeline
  100k times (that's what the ingest throughput leg is *for*; seeding should be
  cheap so the retrieval test can be re-run on demand).
- A **CI-safe smoke version** (small doc count, few users, short duration) that
  proves the load-test harness itself works, mirroring the ingestion load
  test's existing "500-doc smoke in CI, full run recorded manually" split.
- Multi-tenant: seed several synthetic tenants, have simulated users query as
  different tenants concurrently, so the measurement reflects real RLS-scoped
  query cost, not a single-tenant best case.

## 3. Non-goals / out of scope

- SQLite load testing — SQLite is explicitly the non-scale profile (brute-force
  vector search); Phase 6.1's exit criteria name HNSW specifically.
- Load-testing ingestion via the full async S3/SQS path — the queue/worker
  throughput question is already answered by Phase 3.3; this milestone reuses
  that tool rather than re-litigating it.
- Any change to retrieval logic, RRF fusion, or the reranker. This is a
  measurement milestone, not an optimization milestone — if the bar isn't met,
  the *finding* (and a follow-up optimization plan) is the deliverable, not a
  guaranteed fix within this task.
- A managed/hosted load-test service or a permanent load-test environment.
  This runs against a local (or CI-provisioned) `pgvector/pgvector:pg16`
  container, matching how the `postgres` CI job and prior PG-leg verification
  already work.

## 4. Target architecture

```
                    ┌──────────────────────────┐
  Locust (headless) │  N simulated users,       │
  driver process     │  M tenants, ramped up     │
                    └───────────┬──────────────┘
                                │ HTTP (Bearer service-account key per tenant)
                                ▼
                    ┌──────────────────────────┐
                    │  uvicorn backend.app:app  │
                    │  (real FastAPI process)   │
                    └───────────┬──────────────┘
                                │ PostgresVectorStore.search_similarity
                                ▼
                    ┌──────────────────────────┐
                    │  Postgres 16 + pgvector   │
                    │  100k+ child_chunks,      │
                    │  HNSW index, M tenants    │
                    └──────────────────────────┘
```

`scripts/seed_retrieval_load_corpus.py` populates the Postgres store directly
(bypassing HTTP/queue) using the same torch-free fake-embedder pattern as
`load_test_ingestion.py`'s `_FakeEmbedder`, so 100k documents seed in seconds/
minutes, not the hours a real CPU embedding model would take. `scripts/
locustfile_retrieval.py` is the Locust user-behavior definition, run via
`locust --headless` against the live server.

## 5. Components & interfaces

**`scripts/seed_retrieval_load_corpus.py`** (new)
- CLI: `--count` (docs, default 100000), `--tenants` (default 8),
  `--postgres-dsn` (or `POSTGRES_DSN` env).
- `PostgresVectorStore` is RLS-scoped to **one tenant per instance**
  (`tenant_id` is a constructor arg, not a per-call param), so the seeder
  constructs `--tenants` separate store instances up front (one per synthetic
  `loadtest-tenant-N` id) and round-robins `add_document` calls across them —
  not a single shared store handling all tenants.
- For each doc: synthetic realistic-length parent/child chunk text (varied,
  not identical, so vector search isn't trivially degenerate), a deterministic
  fake embedding (same `usedforsecurity=False` md5-derived vector approach as
  the ingestion load test).
- Writes directly via each tenant store's `add_document` in batches — no
  FastAPI, no queue, no real ML.
- Idempotent-enough for repeated local runs: truncates and reseeds the target
  rows for the `loadtest-tenant-*` prefix rather than accumulating forever
  across runs.

**`scripts/load_test_ingestion.py`** (extended, not rewritten)
- Currently hardcodes `SQLiteVectorStore` and a single `tenant_id="tenant-a"`.
  Gains a `--backend {sqlite,postgres}` flag (default `sqlite`, so existing
  behavior/callers are unchanged) and `--postgres-dsn`; when `postgres`,
  constructs one `PostgresVectorStore(dsn=..., tenant_id="loadtest-tenant-a")`
  instead of the SQLite store. This is the minimal change that lets the
  existing, already-proven ingest-throughput tool answer Phase 6.1's "ingest
  throughput sustained" leg at the Postgres/HNSW scale, instead of building a
  second ingest harness.

**`scripts/locustfile_retrieval.py`** (new)
- A `HttpUser` per simulated tenant/principal (service-account keys minted for
  `--tenants` load-test tenants, reusing `backend/authn.py`'s
  `ServiceAccountAuthenticator` file format — no new auth code).
- Tasks: weighted mix of `/api/chat` (`engine=simulated`, avoids requiring a
  live LM Studio during the load test — retrieval cost is what's measured, not
  LLM synthesis latency) and `/api/eval/search` (raw retrieval, admin/siu-only
  per RBAC — the load-test service accounts get that role).
- Locust's built-in percentile stats (`--csv`) are the reporting mechanism; no
  custom latency-tracking code needed.

**`scripts/run_retrieval_load_test.py`** (new, thin orchestration)
- Brings up a `pgvector/pgvector:pg16` container if `POSTGRES_DSN` isn't
  already set, runs `alembic upgrade head`, runs the seed script, starts
  `uvicorn` in the background, runs `locust --headless`, asserts p95 < the
  configured bar, tears down.
- Parameterized so CI can call it with a small `--count`/short duration for the
  smoke leg, and a human can call it with `--count 100000` for the real Phase
  6.1 measurement.

## 6. Data flow (one load-test run)

1. Provision/point at a Postgres+pgvector instance; `alembic upgrade head`.
2. `seed_retrieval_load_corpus.py --count 100000 --tenants 8` populates the DB
   directly (fake embeddings, batched inserts).
3. Start the real FastAPI server (`uvicorn backend.app:app`) against that DSN.
4. `locust -f scripts/locustfile_retrieval.py --headless -u <users> -r <spawn
   rate> -t <duration> --csv results` drives concurrent multi-tenant query
   load against the live server.
5. Parse Locust's CSV stats; assert p95 < 100ms; report ingest throughput from
   a companion `load_test_ingestion.py --count 100000 --backend postgres` run.
6. Record real numbers in `docs/enterprise-migration.md`'s Phase 6.1 status and
   `CLAUDE.md`'s session log — pass or fail, honestly, matching how every prior
   phase's verification was recorded.

## 7. Error handling & robustness

- Seed script batches inserts and reports progress; a mid-run failure leaves
  partial data under the load-test tenant prefix, which the next run's
  truncate-and-reseed step cleans up — no manual cleanup required.
- Locust's own timeout/retry semantics apply; a user task that errors (e.g.
  429 from rate limiting) is recorded as a Locust failure, not silently
  dropped — rate-limit configuration for the load-test run must be raised
  above the default 60 req/60s per principal (`RATE_LIMIT_MAX_REQUESTS`) or
  the test would measure the rate limiter, not retrieval latency. Documented
  explicitly as a required override for this test, not a product bug.
- If the p95 bar isn't met, that's a valid, recorded *result* of this
  milestone (a finding), not a task failure to hide — matches Section 3's
  non-goal framing.

## 8. Security

- Load-test service-account keys are generated into a **git-ignored** local
  file (`loadtest_service_accounts.json`, following the existing
  `SERVICE_ACCOUNTS_FILE` pattern), never committed, never real credentials.
- The seed script and load-test tenants use an obviously-synthetic tenant
  prefix (`loadtest-tenant-N`) so they can never collide with or be mistaken
  for real tenant data.
- No change to any request-handling code path — this is a test/measurement
  addition only.

## 9. Deployment wiring appendix (not built here)

- A managed load-test environment (dedicated staging Postgres sized like
  production, a scheduled/repeatable load-test CI job with trend tracking) is
  operator setup, not code. This milestone proves the harness and produces one
  recorded measurement; turning it into a recurring signal is Phase 6.2's
  drift-monitoring territory (a different exit criterion) or ongoing ops
  practice, not re-scoped into 6.1.

## 10. Testing strategy

**Unit — `tests/test_seed_retrieval_load_corpus.py`**
- Seed script, called with a small `--count` against a PG-gated test DB (same
  gate as `tests/test_postgres_store.py`), produces exactly `--count` documents
  spread round-robin across `--tenants` tenants, each with non-degenerate
  (non-identical) embeddings.
- Truncate-and-reseed is idempotent: running twice with the same args leaves
  exactly `--count` documents for the load-test tenant prefix, not `2×count`.

**CI smoke — wired into the existing `postgres` CI job**
- `scripts/run_retrieval_load_test.py --count 500 --users 5 --duration 15s`
  runs against the CI `pgvector/pgvector:pg16` service container already used
  by the `postgres` job. Asserts the harness produces a parseable p95 number
  and exits 0 — does **not** gate CI on the 100ms bar at this trivial scale
  (500 docs will trivially clear it; the smoke test proves the tool works, not
  that the real 100k target is met).

**Manual/recorded — the actual Phase 6.1 measurement**
- Run `scripts/run_retrieval_load_test.py --count 100000 --users <N> --duration
  <T>` locally (this sandbox has Docker + enough resources per the Phase-1 PG
  leg precedent), record actual p50/p95/p99 latency and ingest docs/sec in
  `docs/enterprise-migration.md` and `CLAUDE.md`, pass or fail against the
  100ms p95 bar honestly.

## 11. Exit-criteria mapping

| Migration doc exit criterion | How this milestone meets it |
|---|---|
| k6/Locust load test | Locust (pure Python, fits the existing all-Python toolchain; k6 would add a separate Go-binary dependency for no added capability here) |
| 100k+ docs | `seed_retrieval_load_corpus.py --count 100000` |
| Multi-tenant | `--tenants 8`, Locust users authenticate as different tenants concurrently |
| Concurrent users | Locust's `-u`/`-r` concurrent-user simulation |
| p95 retrieval < 100ms (HNSW) | Locust CSV stats against the Postgres/pgvector HNSW-backed live server |
| Ingest throughput sustained | `load_test_ingestion.py --count 100000 --backend postgres`, reusing the Phase 3.3 tool at the larger scale |
