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

## Health

- `GET /health/live` only checks that the process is responding.
- `GET /health/ready` checks configured local dependencies and returns a safe not-ready response when they are unavailable.
- `X-Request-ID` is accepted or generated and returned on responses.

## Runtime data

Keep `rag_store.db`, `stored_documents/`, `.env`, model caches, logs, and audit output outside version control. Configure explicit paths for service deployments. Back up the database and source documents together until object storage is enabled.

## Production boundary

The current SQLite/filesystem profile is a development and evaluation profile. A production deployment must complete the enterprise migration phases for Postgres/pgvector with tenant isolation, durable encrypted object storage, async ingestion, verified authentication, authorization, audit logging, rate limits, observability, backups, and retention/deletion controls.

The assistant remains a research aid. Coverage, fraud, payment, denial, referral, and reserve decisions remain human-owned or belong to an explicitly integrated external system.
