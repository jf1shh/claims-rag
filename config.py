from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parent


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected a boolean value, got {value!r}")


def _int(value: str | None, default: int, name: str) -> int:
    if value is None:
        return default
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _float(value: str | None, default: float, name: str) -> float:
    if value is None:
        return default
    try:
        result = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: tuple[str, ...] = ("http://localhost:8000", "http://127.0.0.1:8000")
    rag_db_path: Path = REPO_ROOT / "rag_store.db"
    stored_documents_dir: Path = REPO_ROOT / "stored_documents"
    jobs_db_path: Path = REPO_ROOT / "jobs.db"
    ingestion_mode: str = "sync"  # "sync" | "async" (Phase 3 async ingestion)
    vector_store: str = "sqlite"  # "sqlite" | "postgres" (Phase 1 data plane)
    postgres_dsn: str | None = None
    tenant_id: str = "local-development"
    object_storage_provider: str = "filesystem"
    object_storage_bucket: str | None = None
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None
    s3_sse_kms_key_id: str | None = None
    queue_provider: str = "in-process"  # "in-process" | "sqs" (Phase 3 async ingestion)
    sqs_queue_url: str | None = None
    sqs_dlq_url: str | None = None
    sqs_region: str = "us-east-1"
    sqs_endpoint_url: str | None = None
    worker_max_retries: int = 5
    worker_backoff_base_seconds: float = 2.0
    worker_poll_interval_seconds: float = 1.0
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dimensions: int = 384
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    llm_provider: str = "lm-studio"
    llm_base_url: str = "http://127.0.0.1:1234"
    llm_model: str | None = None
    max_upload_bytes: int = 25 * 1024 * 1024
    max_document_chars: int = 2_000_000
    max_query_chars: int = 10_000
    max_top_k: int = 50
    request_timeout_seconds: int = 120
    simulation_mode: bool = True

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        origins = tuple(
            origin.strip()
            for origin in env.get("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
            if origin.strip()
        )
        return cls(
            app_env=env.get("APP_ENV", "development").strip().lower(),
            log_level=env.get("LOG_LEVEL", "INFO").strip().upper(),
            cors_origins=origins,
            rag_db_path=Path(env.get("RAG_DB_PATH", str(REPO_ROOT / "rag_store.db"))).expanduser(),
            stored_documents_dir=Path(env.get("STORED_DOCUMENTS_DIR", str(REPO_ROOT / "stored_documents"))).expanduser(),
            jobs_db_path=Path(env.get("JOBS_DB_PATH", str(REPO_ROOT / "jobs.db"))).expanduser(),
            ingestion_mode=env.get("INGESTION_MODE", "sync").strip().lower(),
            vector_store=env.get("VECTOR_STORE", "sqlite").strip().lower(),
            postgres_dsn=env.get("POSTGRES_DSN") or None,
            tenant_id=env.get("TENANT_ID", "local-development").strip(),
            object_storage_provider=env.get("OBJECT_STORAGE_PROVIDER", "filesystem").strip().lower(),
            object_storage_bucket=env.get("OBJECT_STORAGE_BUCKET") or None,
            s3_region=env.get("S3_REGION", "us-east-1").strip(),
            s3_endpoint_url=env.get("S3_ENDPOINT_URL") or None,
            s3_sse_kms_key_id=env.get("S3_SSE_KMS_KEY_ID") or None,
            queue_provider=env.get("QUEUE_PROVIDER", "in-process").strip().lower(),
            sqs_queue_url=env.get("SQS_QUEUE_URL") or None,
            sqs_dlq_url=env.get("SQS_DLQ_URL") or None,
            sqs_region=env.get("SQS_REGION", "us-east-1").strip(),
            sqs_endpoint_url=env.get("SQS_ENDPOINT_URL") or None,
            worker_max_retries=_int(env.get("WORKER_MAX_RETRIES"), 5, "WORKER_MAX_RETRIES"),
            worker_backoff_base_seconds=_float(env.get("WORKER_BACKOFF_BASE_SECONDS"), 2.0, "WORKER_BACKOFF_BASE_SECONDS"),
            worker_poll_interval_seconds=_float(env.get("WORKER_POLL_INTERVAL_SECONDS"), 1.0, "WORKER_POLL_INTERVAL_SECONDS"),
            embedding_model=env.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
            embedding_dimensions=_int(env.get("EMBEDDING_DIMENSIONS"), 384, "EMBEDDING_DIMENSIONS"),
            reranker_model=env.get("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
            llm_provider=env.get("LLM_PROVIDER", "lm-studio").strip().lower(),
            llm_base_url=env.get("LLM_BASE_URL", "http://127.0.0.1:1234").rstrip("/"),
            llm_model=env.get("LLM_MODEL") or None,
            max_upload_bytes=_int(env.get("MAX_UPLOAD_BYTES"), 25 * 1024 * 1024, "MAX_UPLOAD_BYTES"),
            max_document_chars=_int(env.get("MAX_DOCUMENT_CHARS"), 2_000_000, "MAX_DOCUMENT_CHARS"),
            max_query_chars=_int(env.get("MAX_QUERY_CHARS"), 10_000, "MAX_QUERY_CHARS"),
            max_top_k=_int(env.get("MAX_TOP_K"), 50, "MAX_TOP_K"),
            request_timeout_seconds=_int(env.get("REQUEST_TIMEOUT_SECONDS"), 120, "REQUEST_TIMEOUT_SECONDS"),
            simulation_mode=_bool(env.get("SIMULATION_MODE"), True),
        )

    def validate_for_environment(self) -> None:
        if self.app_env not in {"development", "test", "staging", "production"}:
            raise ValueError("APP_ENV must be development, test, staging, or production")
        if not self.cors_origins:
            raise ValueError("CORS_ORIGINS must contain at least one origin")
        for origin in self.cors_origins:
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"CORS_ORIGINS contains an invalid URL: {origin!r}")
        if self.vector_store not in {"sqlite", "postgres"}:
            raise ValueError("VECTOR_STORE must be sqlite or postgres")
        if not self.tenant_id.strip():
            raise ValueError("TENANT_ID must not be empty")
        if self.object_storage_provider not in {"filesystem", "s3"}:
            raise ValueError("OBJECT_STORAGE_PROVIDER must be filesystem or s3")
        if self.queue_provider not in {"in-process", "sqs"}:
            raise ValueError("QUEUE_PROVIDER must be in-process or sqs")
        if self.queue_provider == "sqs" and not self.sqs_queue_url:
            raise ValueError("SQS_QUEUE_URL is required when QUEUE_PROVIDER is sqs")
        if self.ingestion_mode not in {"sync", "async"}:
            raise ValueError("INGESTION_MODE must be sync or async")
        if self.llm_provider not in {"lm-studio", "openai-compatible", "none"}:
            raise ValueError("LLM_PROVIDER must be lm-studio, openai-compatible, or none")
        if self.app_env == "production":
            if self.simulation_mode:
                raise ValueError("SIMULATION_MODE must be false in production")
            if self.vector_store == "postgres" and not self.postgres_dsn:
                raise ValueError("POSTGRES_DSN is required when VECTOR_STORE is postgres")
            if self.llm_provider == "openai-compatible" and not self.llm_base_url:
                raise ValueError("LLM_BASE_URL is required for openai-compatible production")
            if self.object_storage_provider == "s3" and not self.object_storage_bucket:
                raise ValueError("OBJECT_STORAGE_BUCKET is required for S3 production")
            if any(origin.startswith("http://localhost") or origin.startswith("http://127.0.0.1") for origin in self.cors_origins):
                raise ValueError("production CORS_ORIGINS cannot use localhost origins")


def get_settings() -> Settings:
    settings = Settings.from_env()
    settings.validate_for_environment()
    return settings
