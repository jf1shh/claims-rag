# Phase 6.1 — Retrieval Load Test Implementation Plan

**Spec:** `docs/superpowers/specs/2026-09-02-phase6-load-test-design.md`

**Goal:** Prove (or honestly disprove) that retrieval stays under p95 < 100ms at
100k+ documents, multi-tenant, under concurrent query load against the
Postgres/pgvector HNSW backend, and that ingest throughput holds at the same
scale — reusing the Phase 3.3 ingestion load-test tool rather than duplicating
it.

**Tech stack:** Python 3.12, `locust` (new dev dependency), the existing
`PostgresVectorStore`/`ServiceAccountAuthenticator`. No new production code
paths — this is a test-tooling milestone.

## Tasks

1. **`--backend postgres` on `scripts/load_test_ingestion.py`** — minimal
   extension, default stays `sqlite` (no behavior change for existing
   callers/CI smoke). Test: a PG-gated unit case constructing the script's
   `main()` with `--backend postgres --count <small>` against the test DSN
   already used by `tests/test_postgres_store.py`.
2. **`scripts/seed_retrieval_load_corpus.py`** — bulk-seeds N docs across M
   tenant-scoped `PostgresVectorStore` instances, fake embedder, batched
   writes, truncate-and-reseed idempotency for the `loadtest-tenant-*` prefix.
   Test: PG-gated, small `--count`/`--tenants`, asserts exact document counts
   per tenant and non-identical embeddings.
3. **`scripts/locustfile_retrieval.py`** — Locust `HttpUser` classes issuing
   `/api/chat` (simulated engine) and `/api/eval/search` requests as
   load-test service-account principals. Not unit-tested directly (Locust's
   own test surface); validated via the smoke run in Task 4.
4. **`scripts/run_retrieval_load_test.py`** — orchestration: DSN check/PG
   container bring-up, migrate, seed, start server, run Locust headless,
   parse+assert p95, teardown. Parameterized `--count`/`--users`/`--duration`.
   Test: the CI smoke leg (`--count 500 --users 5 --duration 15s`) wired into
   the `postgres` CI job, asserting exit 0 and a parseable p95 line — not
   gating on the 100ms bar at trivial scale (see spec Section 10).
5. **Run the real 6.1 measurement** — `--count 100000` locally, record actual
   numbers (pass or fail against the 100ms bar) in
   `docs/enterprise-migration.md` and `CLAUDE.md`, honestly.
6. **Docs**: migration doc Phase 6.1 status paragraph, CLAUDE.md session log,
   `pyproject.toml`/`requirements.txt` gains `locust` (dev/test section,
   pinned).

## Verification

- `pytest tests/ -q` full suite green (existing + 2 new PG-gated cases).
- `ruff check .` clean, `scripts/run_foundation_gates.py --mode gate` 0
  blocking.
- The real 100k-doc run's actual p50/p95/p99 and ingest docs/sec, recorded
  honestly regardless of outcome.
- Standing workflow: commit + PR + CI on the self-hosted runner + merge.
