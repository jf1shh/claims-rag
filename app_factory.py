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
    reranker: Any | None = None
    agentic_router: Any | None = None
    queue: Any | None = None
    job_store: Any | None = None
    authenticator: Any | None = None
    claim_access_policy: Any | None = None
    audit_sink: Any | None = None
    rate_limiter: Any | None = None
    llm_client: Any | None = None


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
    return ClaimAccessPolicy() if settings.app_env in {"development", "test"} else ClaimAccessPolicy({})


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


def _build_llm_client(settings: Settings):
    """Provider-neutral LLM client (Phase 5.1). ``LLM_PROVIDER=none`` disables
    the online path (simulation only); otherwise builds an OpenAI-compatible
    client for the allowlisted LLM_BASE_URL with per-stage models. Mirrors the
    other builders: config selects the provider, this returns the adapter."""
    if settings.llm_provider in (None, "none"):
        return None
    from backend.llm_client import OpenAICompatibleClient

    return OpenAICompatibleClient(
        base_url=settings.llm_base_url,
        default_model=settings.llm_model or "local-model",
        planning_model=settings.planning_model,
        synthesis_model=settings.synthesis_model,
        eval_model=settings.eval_model,
        api_key=settings.llm_api_key,
        timeout=float(settings.llm_synthesis_timeout_seconds),
        plan_timeout=float(settings.llm_plan_timeout_seconds),
        models_ttl_seconds=float(settings.model_cache_ttl_seconds),
    )


def _build_reranker(settings: Settings):
    from backend.reranker import FallbackReranker, LocalReranker, RemoteReranker

    local = LocalReranker(model_name=settings.reranker_model, max_concurrency=settings.rerank_max_concurrency, device=settings.rerank_device)
    if settings.rerank_provider != "remote":
        return local
    remote = RemoteReranker(
        endpoint=settings.rerank_endpoint,
        timeout=float(settings.rerank_timeout_seconds),
        api_key=settings.rerank_api_key,
    )
    return FallbackReranker(primary=remote, fallback=local)


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
    llm_client = _build_llm_client(settings)
    reranker = _build_reranker(settings)

    return AppDependencies(
        settings=settings,
        vector_store=vector_store,
        queue=queue,
        job_store=job_store,
        authenticator=authenticator,
        claim_access_policy=claim_access_policy,
        audit_sink=audit_sink,
        rate_limiter=rate_limiter,
        llm_client=llm_client,
        reranker=reranker,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from threading import Lock
    from fastapi.middleware.cors import CORSMiddleware
    from backend.agentic_router import AgenticRAGRouter
    from backend.blob_store import LocalDocumentBlobStore
    from backend.ingestion import IngestionService
    from backend.ingestion_worker import build_ingestion_worker
    from backend.api import register_routes

    resolved = settings or get_settings()
    resolved.validate_for_environment()
    for path in (resolved.rag_db_path, resolved.jobs_db_path, resolved.audit_log_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    dependencies = build_dependencies(resolved)
    runtime = SimpleNamespace(
        settings=resolved, vector_store=dependencies.vector_store,
        embedding_engine=None, embedding_lock=Lock(), agentic_router=AgenticRAGRouter(),
        _authenticator=dependencies.authenticator, _claim_access_policy=dependencies.claim_access_policy,
        _audit_sink=dependencies.audit_sink, _rate_limiter=dependencies.rate_limiter,
        _llm_client=dependencies.llm_client, _reranker=dependencies.reranker,
        ingestion_job_store=dependencies.job_store, _ingestion_worker=None,
        _async_blob_store=dependencies.vector_store.blob_store or LocalDocumentBlobStore(
            resolved.stored_documents_dir / "ingest_queue"),
    )
    runtime.ingestion_service = IngestionService(
        queue=dependencies.queue if resolved.ingestion_mode == "async" else None,
        job_store=dependencies.job_store,
    )

    @asynccontextmanager
    async def lifespan(app):
        if runtime._ingestion_worker is not None:
            runtime._ingestion_worker.start()
        try:
            yield
        finally:
            if runtime._ingestion_worker is not None:
                runtime._ingestion_worker.stop()
            dependencies.queue.close()

    app = FastAPI(title="AutoClaimsRAG API", lifespan=lifespan)
    app.state.settings = resolved
    app.state.dependencies = dependencies
    app.state.runtime = runtime
    app.add_middleware(CORSMiddleware, allow_origins=list(resolved.cors_origins),
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    from backend.body_limits import BodyLimitMiddleware
    app.add_middleware(BodyLimitMiddleware, settings=resolved)
    register_routes(app, runtime)
    if resolved.ingestion_mode == "async":
        runtime._ingestion_worker = build_ingestion_worker(
            resolved, dependencies.vector_store, runtime._async_blob_store,
            lambda: runtime._get_embedding_engine(), job_store=dependencies.job_store,
            queue=dependencies.queue,
        )
    return app
