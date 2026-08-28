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


def build_dependencies(settings: Settings) -> AppDependencies:
    from backend.rag_engine import SQLiteVectorStore

    return AppDependencies(
        settings=settings,
        vector_store=SQLiteVectorStore(
            db_path=str(settings.rag_db_path),
            storage_dir=str(settings.stored_documents_dir),
        ),
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
