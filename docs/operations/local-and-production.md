> September 6 hardening: see [current security/data-lifecycle requirements](../../SECURITY.md)
> and [verification record](../portfolio-hardening.md). One application now enforces one tenant;
> Postgres migrations must be upgraded to `head`. Runtime sources use immutable version keys.
> The older session notes below describe historical setup and measurements.

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

### Local reranker batching

Local reranking uses bounded, process-local cross-request micro-batching by default.
The coordinator waits up to 5 ms to coalesce requests, then limits each model call
to 16 requests and 256 query/passage pairs. The model inference batch size defaults
to 16, and at most 1024 requests may be pending. These limits protect memory and
bound overload; they do not apply to `RemoteReranker`'s HTTP protocol.

The controls are configured through environment variables:

| Variable | Default | Purpose |
|---|---:|---|
| `RERANK_BATCHING_ENABLED` | `true` | Enable local batching; set `false` to return to the direct semaphore path |
| `RERANK_BATCH_MAX_WAIT_MS` | `5` | Maximum coalescing delay |
| `RERANK_BATCH_MAX_REQUESTS` | `16` | Maximum independent requests per model call |
| `RERANK_BATCH_MAX_PAIRS` | `256` | Maximum flattened query/passage pairs per model call |
| `RERANK_INFERENCE_BATCH_SIZE` | `16` | `CrossEncoder.predict()` inference batch size |
| `RERANK_BATCH_MAX_PENDING` | `1024` | Maximum pending local requests before controlled overload rejection |

`RERANK_BATCH_MAX_PAIRS` must be at least `RERANK_CANDIDATE_POOL`. Batching is an
experimental optimization: use `RERANK_BATCHING_ENABLED=false` as the immediate
rollback if production measurements show a regression. The implementation preserves
result ordering and isolates caller-owned passage dictionaries; see the approved
design and the Phase 6.1 migration record for verification and benchmark status.

### Answer guard, conflict check and prompt defenses

These settings come from the adversarial evaluation (`docs/adversarial-evaluation.md`), which holds the
measurements behind each default.

| Setting | Default | Effect |
|---|---|---|
| `ANSWER_GUARD_MODE` | `withhold` | `withhold` replaces an answer that asserts an unsupported claim outcome or contains an off-allowlist URL or email (status `insufficient_evidence`, sources kept). `flag` keeps the text and records findings. `off` disables the guard |
| `ANSWER_GUARD_ALLOWED_DOMAINS` | empty | Comma-separated domains an answer may link to or cite as a contact; subdomains are included |
| `CONFLICT_CHECK` | `llm` | One short extra model call per answer extracts each source's value. Differing numbers across sources set `conflicting_evidence`. Measured at about +3–4.5 s per answer. `off` skips it |
| `PROMPT_DEFENSE` | `sanitize` | Comma list of `sanitize`, `sandwich`, `datamark`, or `none`. `sandwich` roughly halves held-out injection success but costs about 0.12 factual correctness. `datamark` made this model worse |

Before changing any of them, re-run the adversarial suite and the golden eval, and compare against the
recorded runs in `eval/prompt_defense_ab.json`.

## Docker

```bash
docker compose up --build
```

`Dockerfile` is a single-stage `python:3.12-slim` image. Two things worth knowing:

- The app forces Hugging Face offline mode at import time (`HF_HUB_OFFLINE=1` in `backend/app.py`), so the embedding + reranker models must already be cached before the server can start. The build runs `scripts/precache_models.py` (network required at **build** time only) so the resulting image needs zero network access to boot.
- LM Studio is a native desktop app and can't live in the container. `docker-compose.yml` sets `LLM_BASE_URL=http://host.docker.internal:1234` and adds the `host.docker.internal:host-gateway` mapping Linux needs (Docker Desktop on Mac/Windows already resolves it). With no LM Studio running, the app still serves — `SIMULATION_MODE=true` stands in for a live model.

Runtime state (`rag_store.db`, `stored_documents/`, `jobs.db`, `audit.log.jsonl`) is routed via `RAG_DB_PATH`/`STORED_DOCUMENTS_DIR`/`JOBS_DB_PATH`/`AUDIT_LOG_PATH` into `/app/data`, backed by one named volume — deliberately not a bind mount per file, since Docker creates a directory (not a file) when a bind-mounted host path doesn't already exist.

This image is a development/portfolio-demo profile, same as local SQLite mode (see Production boundary below) — it isn't a production deployment artifact on its own.

## CI

The repository is public, so `.github/workflows/tests.yml` runs both jobs on GitHub-hosted `ubuntu-latest` runners. Each job's `runs-on` is an expression that sends runs to the maintainer's self-hosted runner only while the repository is **private** (owner-only pushes and PRs). A public repository's runs — including external pull requests — never reach the maintainer's machine.

- **What runs**:
  - `pytest-linux` runs `ruff check .`, the foundation gates, the full `pytest tests/` suite, and the retrieval-parity self-check.
  - `postgres` starts `pgvector/pgvector:pg16` in a workflow step and runs the full suite with `POSTGRES_DSN` set (so PG-gated cases execute), then `parity_runner --backend-b postgres --tolerance 0.9`.
  - There is no Windows job; the two Windows-only traversal cases skip on Linux.
- **No model downloads**: the workflow sets `HF_HUB_OFFLINE=1` and hosted runners start with an empty model cache, so any test that constructs the real embedding/reranker model fails there. API tests must stub `_get_embedding_engine` as well as the router (see the Critical Constraints in `CLAUDE.md`). Reproduce locally with an empty `HF_HOME` and `HF_HUB_OFFLINE=1`.
- **Docker Hub pulls**: hosted runners share Docker Hub's anonymous pull limit, which can fail the `postgres` job before any test runs (`toomanyrequests`). Set the repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (a read-only Docker Hub access token) under Settings → Secrets and variables → Actions. The start step logs in only when both are set; otherwise (forks, or before they exist) it pulls anonymously. The image is started as a step rather than a `services:` container because a service container's `credentials:` rejects empty secrets instead of skipping login.
- **Status**: a run stuck *queued* on a hosted runner is GitHub capacity, not this repo. A red badge reflects the latest *completed* run on `main`.

### Self-hosted runner (private-repository fallback only)

Kept for the case where the repository is made private again (GitHub-hosted minutes are capped on private repositories). It is registered under `~/actions-runner-claims-rag` (runner `cachyos-x8664-claimsrag`, label `claimsrag`) as a systemd **user** service (`actions-runner-claims-rag.service`); without `loginctl enable-linger` it only runs while the owner is logged in. On that host the jobs skip `actions/setup-python` (no prebuilt release for CachyOS rolling) and build a venv from the system `python3.12`. While the repo stays public nothing routes to it, and it can be disabled with `systemctl --user disable --now actions-runner-claims-rag.service`.

If it is needed again, check it before assuming a workflow bug — a private-repo run stuck queued usually means the service is down:

```bash
systemctl --user status actions-runner-claims-rag.service
gh api repos/jf1shh/claims-rag/actions/runners --jq '.runners[] | {name, status}'
```

The runner does not follow the git repo: on a new host it must be re-registered (`~/actions-runner-claims-rag/config.sh` with a token from `gh api -X POST repos/jf1shh/claims-rag/actions/runners/registration-token`), with Docker and system Python 3.12 available.

## Health

- `GET /health/live` only checks that the process is responding.
- `GET /health/ready` checks configured local dependencies and returns a safe not-ready response when they are unavailable.
- `X-Request-ID` is accepted or generated and returned on responses.

## Runtime data

Keep `rag_store.db`, `stored_documents/`, `.env`, model caches, logs, and audit output outside version control. Configure explicit paths for service deployments. Back up the database and source documents together until object storage is enabled.

## Production boundary

The current SQLite/filesystem profile is a development and evaluation profile. A production deployment must complete the enterprise migration phases for Postgres/pgvector with tenant isolation, durable encrypted object storage, async ingestion, verified authentication, authorization, audit logging, rate limits, observability, backups, and retention/deletion controls.

The assistant remains a research aid. Coverage, fraud, payment, denial, referral, and reserve decisions remain human-owned or belong to an explicitly integrated external system.
