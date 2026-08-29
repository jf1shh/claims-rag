from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from config import Settings, get_settings


@dataclass
class AppDependencies:
    settings: Settings
    vector_store: Any
    embedding_engine: Any | None = None
    reranking_engine: Any | None = None
    agentic_router: Any | None = None
    queue: Any | None = None
    job_store: Any | None = None
    authenticator: Any | None = None
    claim_access_policy: Any | None = None
    audit_sink: Any | None = None
    rate_limiter: Any | None = None


def _build_blob_store(settings: Settings):
    """Constructs the object-storage adapter for the configured provider.
    ``filesystem`` (the default) keeps the legacy local-directory behavior;
    ``s3`` returns an S3-compatible adapter. Returns None for filesystem so
    the stores fall back to their existing storage_dir writes."""
    if settings.object_storage_provider != "s3":
        return None
    if not settings.object_storage_bucket:
        raise ValueError("OBJECT_STORAGE_BUCKET is required when OBJECT_STORAGE_PROVIDER is s3")
    from backend.blob_store import S3DocumentBlobStore

    return S3DocumentBlobStore(
        bucket=settings.object_storage_bucket,
        tenant_id=settings.tenant_id,
        region=settings.s3_region,
        endpoint_url=settings.s3_endpoint_url,
        sse_kms_key_id=settings.s3_sse_kms_key_id,
    )


def _build_queue(settings: Settings):
    """Constructs the ingestion-queue adapter for the configured provider.
    ``in-process`` (the default) is the dev/test loopback; ``sqs`` returns an
    SQS adapter pointing at a real queue or a compatible endpoint (LocalStack
    via SQS_ENDPOINT_URL). Mirrors _build_blob_store."""
    if settings.queue_provider != "sqs":
        from backend.queue import InProcessQueue

        return InProcessQueue()
    if not settings.sqs_queue_url:
        raise ValueError("SQS_QUEUE_URL is required when QUEUE_PROVIDER is sqs")
    from backend.queue import SQSQueue

    return SQSQueue(
        queue_url=settings.sqs_queue_url,
        region=settings.sqs_region,
        endpoint_url=settings.sqs_endpoint_url,
        dlq_url=settings.sqs_dlq_url,
    )


def _build_job_store(settings: Settings):
    """Durable ingestion-job records (Phase 3.2) live in their own SQLite file,
    independent of the vector-store backend, so GET /api/jobs/{id} stays
    truthful across restarts and across the sync/async modes."""
    from backend.job_store import SqliteJobStore

    return SqliteJobStore(str(settings.jobs_db_path))


def _build_authenticator(settings: Settings):
    """Constructs the authentication chain for the configured providers
    (``development`` | ``oidc`` | ``service-accounts``, comma-separated).
    Mirrors _build_blob_store/_build_queue: config selects the provider, this
    returns the adapter. Development keeps the explicit local identity;
    OIDC verifies Bearer JWTs against the issuer's JWKS; service accounts
    check X-API-Key against a JSON file."""
    from backend.authn import build_authenticator

    return build_authenticator(settings)


def _build_claim_access_policy(settings: Settings):
    """Claim-level ACLs (Phase 4.2): loads claim_id -> [subjects] from
    CLAIM_ACLS_FILE when configured. With no file (dev/test default) the
    policy is open within the tenant, preserving pre-4.2 behavior."""
    from backend.rbac import ClaimAccessPolicy

    if settings.claim_acls_file:
        return ClaimAccessPolicy.from_file(settings.claim_acls_file)
    return ClaimAccessPolicy()


def _build_rate_limiter(settings: Settings):
    """Per-principal request limiter for sensitive endpoints (Phase 4.4).
    In-process sliding-window by default; distributed state (Redis) is
    deployment wiring, mirroring how in-process vs SQS split the queue.
    Mirrors the other builders: config selects the behavior, this returns
    the adapter."""
    from backend.rate_limit import SlidingWindowRateLimiter

    return SlidingWindowRateLimiter(
        max_requests=settings.rate_limit_max_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )


def _build_audit_sink(settings: Settings):
    """Immutable append-only audit sink (Phase 4.3). Writes JSONL events to
    AUDIT_LOG_PATH; every upload/delete/chat/download action records who, what,
    tenant, claim, query, sources returned, and a UTC timestamp. Mirrors the
    other builders: config selects the destination, this returns the adapter."""
    from backend.audit import JsonlAuditSink

    return JsonlAuditSink(settings.audit_log_path)


def build_dependencies(settings: Settings) -> AppDependencies:
    from backend.rag_engine import SQLiteVectorStore

    blob_store = _build_blob_store(settings)
    queue = _build_queue(settings)
    job_store = _build_job_store(settings)

    if settings.vector_store == "postgres":
        if not settings.postgres_dsn:
            raise ValueError("POSTGRES_DSN is required when VECTOR_STORE is postgres")
        from backend.postgres_store import PostgresVectorStore

        vector_store = PostgresVectorStore(
            dsn=settings.postgres_dsn,
            tenant_id=settings.tenant_id,
            storage_dir=str(settings.stored_documents_dir),
            embedding_dimensions=settings.embedding_dimensions,
            blob_store=blob_store,
        )
    else:
        vector_store = SQLiteVectorStore(
            db_path=str(settings.rag_db_path),
            storage_dir=str(settings.stored_documents_dir),
            blob_store=blob_store,
        )

    authenticator = _build_authenticator(settings)
    claim_access_policy = _build_claim_access_policy(settings)
    audit_sink = _build_audit_sink(settings)
    rate_limiter = _build_rate_limiter(settings)

    return AppDependencies(
        settings=settings,
        vector_store=vector_store,
        queue=queue,
        job_store=job_store,
        authenticator=authenticator,
        claim_access_policy=claim_access_policy,
        audit_sink=audit_sink,
        rate_limiter=rate_limiter,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    resolved.validate_for_environment()
    dependencies = build_dependencies(resolved)

    app = FastAPI(title="AutoClaimsRAG API")
    app.state.settings = resolved
    app.state.dependencies = dependencies

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    def ready() -> dict[str, str]:
        try:
            dependencies.vector_store._connect().close()
        except Exception:
            return {"status": "not_ready"}
        return {"status": "ready"}

    return app
