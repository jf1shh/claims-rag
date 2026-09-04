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
    audit_log_path: Path = REPO_ROOT / "audit.log.jsonl"  # Phase 4.3 immutable audit log
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
    rerank_provider: str = "local"
    rerank_endpoint: str | None = None
    # Was 50; lowered per the Phase 6.1 load-test finding (docs/enterprise-migration.md):
    # the CPU cross-encoder rerank step, not corpus scale or the vector index, is what
    # exceeds the 100ms p95 bar. pool=8 was tried first and cuts sequential latency
    # the most (~365ms -> ~80ms), but the full eval suite showed it costs more than
    # the quick golden-query check suggested: two queries' live /api/chat correctness
    # dropped hard (0.75->0.0, 1.0->0.25), one of them chen-custom-equipment-cap --
    # the exact query Phase 9's guaranteed-dossier-inclusion fix was built around.
    # pool=15 was chosen instead: verified to preserve full golden-query recall@4
    # (identical to pool=50, no regression) at a smaller but still real sequential
    # latency win (~365ms -> ~155-167ms). This closes part of the *sequential*
    # latency gap without an eval-suite-verified quality regression; it does NOT
    # close the *concurrent*-load p95 (~3800ms regardless of pool size, model size,
    # or torch thread count -- all tested) -- that bottleneck is specific to the
    # rerank call path under concurrency and remains open, tracked as a separate
    # follow-up (see docs/enterprise-migration.md Phase 6.1).
    rerank_candidate_pool: int = 15
    rerank_timeout_seconds: int = 10
    rerank_api_key: str | None = None
    # Phase 6.1 follow-up (docs/enterprise-migration.md): py-spy profiling under
    # concurrent load, then controlled A/B tests, showed the ~3800ms concurrent p95
    # is neither a torch intra-op-thread problem (capping threads/call 8->1 changed
    # nothing) nor a single-process GIL problem (4 separate worker processes, each
    # single-threaded, changed nothing either) -- latency scales with the number of
    # *simultaneous* CPU cross-encoder forward passes, consistent with a shared
    # resource (memory bandwidth/cache) that no thread/process knob adds more of.
    # A bounded semaphore in front of the local reranker call trades unlimited
    # concurrency for queuing, which measurably lowers p95 (see Debugging History).
    rerank_max_concurrency: int = 2
    # Phase 6.1 close-out: the same profiling that ruled out thread/process knobs
    # showed 91% of sampled frames were genuine BERT forward-pass compute, i.e.
    # the bottleneck is raw math the CPU cannot do fast enough -- so run it on a
    # GPU when one exists. "auto" uses a visible GPU and silently falls back to
    # CPU (CI, and any box without one); "cpu" pins the old behaviour; "cuda" is
    # also the device name ROCm builds of torch use for AMD cards.
    rerank_device: str = "auto"
    llm_provider: str = "lm-studio"
    llm_base_url: str = "http://127.0.0.1:1234"
    llm_model: str | None = None
    planning_model: str | None = None       # Phase 5.1 per-stage routing
    synthesis_model: str | None = None
    eval_model: str | None = None
    llm_api_key: str | None = None           # injected from env/secrets; never client-supplied
    llm_synthesis_timeout_seconds: int = 120
    llm_plan_timeout_seconds: int = 5
    model_cache_ttl_seconds: int = 10
    max_upload_bytes: int = 25 * 1024 * 1024
    max_document_chars: int = 2_000_000
    rate_limit_max_requests: int = 60  # Phase 4.4 per-principal rate limiting
    rate_limit_window_seconds: float = 60.0
    max_query_chars: int = 10_000
    max_top_k: int = 50
    request_timeout_seconds: int = 120
    simulation_mode: bool = True
    auth_providers: tuple[str, ...] = ("development",)  # "development" | "oidc" | "service-accounts" (Phase 4.1)
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_jwks_url: str | None = None
    oidc_roles_claim: str = "roles"
    oidc_tenant_claim: str = "tenant_id"
    oidc_cache_ttl_seconds: int = 300
    service_accounts_file: Path | None = None
    claim_acls_file: Path | None = None  # Phase 4.2 RBAC: claim_id -> [subjects]
    context_max_claim_chunks: int = 8      # Phase 5.3 dossier cap
    context_max_global_matches: int = 4    # global/claim match cap (was hardcoded :4)
    context_max_prompt_chars: int = 60_000 # total user-prompt budget guardrail

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
            audit_log_path=Path(env.get("AUDIT_LOG_PATH", str(REPO_ROOT / "audit.log.jsonl"))).expanduser(),
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
            rerank_provider=env.get("RERANK_PROVIDER", "local").strip().lower(),
            rerank_endpoint=env.get("RERANK_ENDPOINT") or None,
            rerank_candidate_pool=_int(env.get("RERANK_CANDIDATE_POOL"), 15, "RERANK_CANDIDATE_POOL"),
            rerank_timeout_seconds=_int(env.get("RERANK_TIMEOUT_SECONDS"), 10, "RERANK_TIMEOUT_SECONDS"),
            rerank_api_key=env.get("RERANK_API_KEY") or None,
            rerank_max_concurrency=_int(env.get("RERANK_MAX_CONCURRENCY"), 2, "RERANK_MAX_CONCURRENCY"),
            rerank_device=(env.get("RERANK_DEVICE") or "auto").strip().lower(),
            llm_provider=env.get("LLM_PROVIDER", "lm-studio").strip().lower(),
            llm_base_url=env.get("LLM_BASE_URL", "http://127.0.0.1:1234").rstrip("/"),
            llm_model=env.get("LLM_MODEL") or None,
            planning_model=env.get("PLANNING_MODEL") or None,
            synthesis_model=env.get("SYNTHESIS_MODEL") or None,
            eval_model=env.get("EVAL_MODEL") or None,
            llm_api_key=env.get("LLM_API_KEY") or None,
            llm_synthesis_timeout_seconds=_int(env.get("LLM_SYNTHESIS_TIMEOUT_SECONDS"), 120, "LLM_SYNTHESIS_TIMEOUT_SECONDS"),
            llm_plan_timeout_seconds=_int(env.get("LLM_PLAN_TIMEOUT_SECONDS"), 5, "LLM_PLAN_TIMEOUT_SECONDS"),
            model_cache_ttl_seconds=_int(env.get("MODEL_CACHE_TTL_SECONDS"), 10, "MODEL_CACHE_TTL_SECONDS"),
            max_upload_bytes=_int(env.get("MAX_UPLOAD_BYTES"), 25 * 1024 * 1024, "MAX_UPLOAD_BYTES"),
            max_document_chars=_int(env.get("MAX_DOCUMENT_CHARS"), 2_000_000, "MAX_DOCUMENT_CHARS"),
            rate_limit_max_requests=_int(env.get("RATE_LIMIT_MAX_REQUESTS"), 60, "RATE_LIMIT_MAX_REQUESTS"),
            rate_limit_window_seconds=_float(env.get("RATE_LIMIT_WINDOW_SECONDS"), 60.0, "RATE_LIMIT_WINDOW_SECONDS"),
            max_query_chars=_int(env.get("MAX_QUERY_CHARS"), 10_000, "MAX_QUERY_CHARS"),
            max_top_k=_int(env.get("MAX_TOP_K"), 50, "MAX_TOP_K"),
            request_timeout_seconds=_int(env.get("REQUEST_TIMEOUT_SECONDS"), 120, "REQUEST_TIMEOUT_SECONDS"),
            simulation_mode=_bool(env.get("SIMULATION_MODE"), True),
            auth_providers=tuple(
                provider.strip().lower()
                for provider in env.get("AUTH_PROVIDERS", "development").split(",")
                if provider.strip()
            ),
            oidc_issuer=env.get("OIDC_ISSUER") or None,
            oidc_client_id=env.get("OIDC_CLIENT_ID") or None,
            oidc_jwks_url=env.get("OIDC_JWKS_URL") or None,
            oidc_roles_claim=env.get("OIDC_ROLES_CLAIM", "roles").strip(),
            oidc_tenant_claim=env.get("OIDC_TENANT_CLAIM", "tenant_id").strip(),
            oidc_cache_ttl_seconds=_int(env.get("OIDC_CACHE_TTL_SECONDS"), 300, "OIDC_CACHE_TTL_SECONDS"),
            service_accounts_file=Path(env["SERVICE_ACCOUNTS_FILE"]).expanduser() if env.get("SERVICE_ACCOUNTS_FILE") else None,
            claim_acls_file=Path(env["CLAIM_ACLS_FILE"]).expanduser() if env.get("CLAIM_ACLS_FILE") else None,
            context_max_claim_chunks=_int(env.get("CONTEXT_MAX_CLAIM_CHUNKS"), 8, "CONTEXT_MAX_CLAIM_CHUNKS"),
            context_max_global_matches=_int(env.get("CONTEXT_MAX_GLOBAL_MATCHES"), 4, "CONTEXT_MAX_GLOBAL_MATCHES"),
            context_max_prompt_chars=_int(env.get("CONTEXT_MAX_PROMPT_CHARS"), 60_000, "CONTEXT_MAX_PROMPT_CHARS"),
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
        if not self.auth_providers:
            raise ValueError("AUTH_PROVIDERS must contain at least one provider")
        unknown_providers = set(self.auth_providers) - {"development", "oidc", "service-accounts"}
        if unknown_providers:
            raise ValueError(f"AUTH_PROVIDERS contains unknown providers: {sorted(unknown_providers)}")
        if "oidc" in self.auth_providers and (not self.oidc_issuer or not self.oidc_client_id):
            raise ValueError("OIDC_ISSUER and OIDC_CLIENT_ID are required when AUTH_PROVIDERS includes oidc")
        if "service-accounts" in self.auth_providers and not self.service_accounts_file:
            raise ValueError("SERVICE_ACCOUNTS_FILE is required when AUTH_PROVIDERS includes service-accounts")
        if self.rerank_provider not in {"local", "remote"}:
            raise ValueError("RERANK_PROVIDER must be local or remote")
        if self.rerank_device not in {"auto", "cpu", "cuda"}:
            raise ValueError("RERANK_DEVICE must be auto, cpu, or cuda")
        if self.rerank_provider == "remote":
            if not self.rerank_endpoint:
                raise ValueError("RERANK_ENDPOINT is required when RERANK_PROVIDER is remote")
            parsed = urlparse(self.rerank_endpoint)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("RERANK_ENDPOINT must be an http(s) URL")
        if self.llm_provider not in {"lm-studio", "openai-compatible", "none"}:
            raise ValueError("LLM_PROVIDER must be lm-studio, openai-compatible, or none")
        if self.app_env == "production":
            if self.simulation_mode:
                raise ValueError("SIMULATION_MODE must be false in production")
            if "development" in self.auth_providers:
                raise ValueError("AUTH_PROVIDERS must not include development in production")
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
