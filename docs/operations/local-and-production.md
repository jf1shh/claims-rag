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

### Optional: GPU reranking (AMD/ROCm or NVIDIA/CUDA)

`RERANK_DEVICE` (default `auto`) decides where the cross-encoder runs: `auto` uses a
GPU when torch can see one and falls back to CPU otherwise, so this is safe to leave
alone on a CPU-only box or in CI. `cpu` pins the old behaviour; `cuda` asks for a GPU
explicitly (and still falls back rather than failing startup). ROCm builds of torch
address AMD cards as `cuda` too — there is no separate `rocm` device name.

The default `requirements.txt` install pulls whichever torch variant
`sentence-transformers` resolves to, which on a fresh box is the CUDA build. On an AMD
card that build reports no GPU, so replace it with the ROCm one (matching the
system-installed ROCm version — check with `cat /opt/rocm/.info/version`):

```bash
.venv/bin/pip install --index-url https://download.pytorch.org/whl/rocm7.2 "torch==2.13.0+rocm7.2"
.venv/bin/python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

If the machine has both a discrete card and an integrated one, torch enumerates both;
`HIP_VISIBLE_DEVICES=0` pins it to the discrete card. This is a local-workstation
choice only — the Docker image deliberately installs the CPU-only wheel (see below),
and CI runs CPU.

## Docker

```bash
docker compose up --build
```

`Dockerfile` is a single-stage `python:3.12-slim` image. Two things worth knowing:

- The app forces Hugging Face offline mode at import time (`HF_HUB_OFFLINE=1` in `backend/app.py`), so the embedding + reranker models must already be cached before the server can start. The build runs `scripts/precache_models.py` (network required at **build** time only) so the resulting image needs zero network access to boot.
- LM Studio is a native desktop app and can't live in the container. `docker-compose.yml` sets `LLM_BASE_URL=http://host.docker.internal:1234` and adds the `host.docker.internal:host-gateway` mapping Linux needs (Docker Desktop on Mac/Windows already resolves it). With no LM Studio running, the app still serves — `SIMULATION_MODE=true` stands in for a live model.

Runtime state (`rag_store.db`, `stored_documents/`, `jobs.db`, `audit.log.jsonl`) is routed via `RAG_DB_PATH`/`STORED_DOCUMENTS_DIR`/`JOBS_DB_PATH`/`AUDIT_LOG_PATH` into `/app/data`, backed by one named volume — deliberately not a bind mount per file, since Docker creates a directory (not a file) when a bind-mounted host path doesn't already exist.

This image is a development/portfolio-demo profile, same as local SQLite mode (see Production boundary below) — it isn't a production deployment artifact on its own.

## Local CI (self-hosted runner)

CI for this repo runs on a **self-hosted GitHub Actions runner** (`runs-on: [self-hosted, linux, autoclaimsrag]`), not a GitHub-hosted VM — the repo is private, GitHub-hosted minutes are capped, and self-hosted matches the project's "runs entirely on my own machine, no cloud" design constraint. See `.github/workflows/tests.yml` and CLAUDE.md (Current State) for the full history and rationale.

> **Before making this repository public — required checklist** (do this *together with* flipping visibility, not before or instead):
>
> 1. In `.github/workflows/tests.yml`, change `runs-on: [self-hosted, linux, autoclaimsrag]` to `runs-on: ubuntu-latest` in both jobs (`pytest-linux` and `postgres`) — each `runs-on` line has a `>>> BEFORE MAKING THIS REPO PUBLIC` comment marking exactly where.
> 2. Push that change and confirm both jobs go green on GitHub-hosted runners before merging anything else.
> 3. Optionally stop/disable the self-hosted runner service (`systemctl stop actions.runner.jf1shh-auto-claims-rag.*`) once nothing routes to it — it's no longer needed.
>
> **Why this is the whole fix, not a tradeoff**: `tests.yml` triggers on plain `pull_request`, which a self-hosted runner must never execute untrusted code for once the repo is public — an external PR's code would run on Jared's own machine. Self-hosted was adopted *only* because GitHub-hosted minutes are capped on private repos; that cap doesn't exist for public repos (GitHub Actions is free/unlimited on standard hosted runners there). So switching to `ubuntu-latest` at the moment of going public simultaneously removes the security risk and the reason self-hosted was needed in the first place — there's no cost/benefit tradeoff to weigh, just do it. (A Windows leg could also be re-added at this point if the account billing issue — see below — is separately resolved; that's independent and optional.)

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
