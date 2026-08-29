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


def build_dependencies(settings: Settings) -> AppDependencies:
    from backend.rag_engine import SQLiteVectorStore

    blob_store = _build_blob_store(settings)

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

    return AppDependencies(
        settings=settings,
        vector_store=vector_store,
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
