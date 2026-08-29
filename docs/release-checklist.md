# AutoClaimsRAG Foundation Release Checklist

## Required automated verification

Run from the repository root using the supported local environment:

```bash
.venv/bin/python -m pytest -q
ruff check .
.venv/bin/python scripts/run_foundation_gates.py --mode gate
.venv/bin/python eval/parity_runner.py
.venv/bin/python -m compileall -q backend app_factory.py config.py
```

Record the actual output and exit code for every command. Do not state that CI is green until the remote workflow has completed successfully.

## Required review checks

- Approved design and implementation plan are linked.
- No real claims files, secrets, databases, model caches, or logs are committed.
- P0 isolation, input validation, evidence grounding, and migration checks pass.
- Every material answer claim can be connected to evidence IDs.
- Responses default to `not_a_decision` and identify human action requirements.
- Configuration has safe development and explicit production behavior.
- `/health/live` works when model services are unavailable.
- `/health/ready` reports dependency failure without leaking internals.
- Documentation and `.env.example` match the implemented commands/settings.
- Deferred findings have an owner, reason, and review date.

## Production-readiness boundary

This foundation is not a production authorization for autonomous claims adjudication. Phases 1–2 of the enterprise migration plan (Postgres + pgvector data plane with tenant RLS, and S3-compatible durable object storage) are implemented and verified as of 2026-08-29, but before a company loads real claim data it must still complete the remaining phases: authentication, authorization, audit, asynchronous ingestion, operational, and compliance (see `docs/enterprise-migration.md`). The default development profile remains SQLite + local filesystem.
