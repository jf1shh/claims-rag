# Local and Production Operations

## Local development

```bash
uv venv .venv --python python3.12
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python generate_auto_pdfs.py
.venv/bin/python ingest_all.py
.venv/bin/python -m uvicorn backend.app:app --reload --port 8000
```

Local mode uses SQLite, the configured filesystem directory, local embeddings/reranking, and optional LM Studio. The repository’s sample data is synthetic.

## Local CI (self-hosted runner)

CI for this repo runs on a **self-hosted GitHub Actions runner** (`runs-on: [self-hosted, linux, autoclaimsrag]`), not a GitHub-hosted VM — the repo is private, GitHub-hosted minutes are capped, and self-hosted matches the project's "runs entirely on my own machine, no cloud" design constraint. See `.github/workflows/tests.yml` and CLAUDE.md (Current State) for the full history and rationale.

- **What runs**: `tests.yml` has two jobs, both on the self-hosted runner — `pytest-linux` (ruff + full `pytest tests/` + retrieval-parity self-check) and `postgres` (PG-gated tests + `parity_runner --backend-b postgres --tolerance 0.9` against a `pgvector/pgvector:pg16` service container, which requires Docker on the runner host). There is **no Windows leg** (no self-hosted Windows box; GitHub-hosted runners are blocked account-wide by a billing failure) — the two Windows-only traversal test cases always skip.
- **How the runner is installed**: registered against this repo under `~/actions-runner` and running as a systemd service (`actions.runner.jf1shh-auto-claims-rag.*.service`) on the host. Both jobs build a per-run venv from the host's system `python3.12` (no `actions/setup-python` — no prebuilt release exists for this host's distro, CachyOS rolling).

### Checking the runner is healthy

A CI run that sits queued/stuck (not failed) usually means the runner's systemd service is down — check before assuming a workflow bug:

```bash
systemctl status 'actions.runner.jf1shh-auto-claims-rag.*'
gh api repos/jf1shh/auto-claims-rag/actions/runners --jq '.runners[] | {name, status}'
```

The runner must show `status: online` (or a queued run never starts). Restart it with `systemctl restart` on the runner host.

### If the repo moves to a different machine

The runner does **not** follow the git repo — it must be re-registered on the new host (`~/actions-runner/config.sh` against a fresh registration token from `gh api -X POST repos/jf1shh/auto-claims-rag/actions/runners/registration-token`), and Docker + system Python 3.12 must be available there.

## Health

- `GET /health/live` only checks that the process is responding.
- `GET /health/ready` checks configured local dependencies and returns a safe not-ready response when they are unavailable.
- `X-Request-ID` is accepted or generated and returned on responses.

## Runtime data

Keep `rag_store.db`, `stored_documents/`, `.env`, model caches, logs, and audit output outside version control. Configure explicit paths for service deployments. Back up the database and source documents together until object storage is enabled.

## Production boundary

The current SQLite/filesystem profile is a development and evaluation profile. A production deployment must complete the enterprise migration phases for Postgres/pgvector with tenant isolation, durable encrypted object storage, async ingestion, verified authentication, authorization, audit logging, rate limits, observability, backups, and retention/deletion controls.

The assistant remains a research aid. Coverage, fraud, payment, denial, referral, and reserve decisions remain human-owned or belong to an explicitly integrated external system.
