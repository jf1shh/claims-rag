from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Callable

from pydantic import BaseModel, ConfigDict


class IngestionStatus(str, Enum):
    queued = "queued"
    parsing = "parsing"
    embedding = "embedding"
    indexed = "indexed"
    failed = "failed"
    deleted = "deleted"


class IngestionJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    tenant_id: str
    document_id: str
    claim_id: str | None = None
    filename: str
    checksum: str
    idempotency_key: str | None = None
    status: IngestionStatus
    progress: int = 0
    retry_count: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class IngestionService:
    def __init__(self, indexer: Callable[[str, bytes, str | None], str] | None = None):
        self._indexer = indexer or self._default_indexer
        self._jobs: dict[tuple[str, str], IngestionJob] = {}
        self._lock = Lock()

    @staticmethod
    def _default_indexer(filename: str, content: bytes, claim_id: str | None) -> str:
        del filename, content, claim_id
        return f"doc_{uuid.uuid4().hex}"

    def submit(
        self,
        *,
        tenant_id: str,
        filename: str,
        content: bytes,
        claim_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> IngestionJob:
        now = datetime.now(timezone.utc)
        checksum = hashlib.sha256(content).hexdigest()
        dedupe_key = idempotency_key or f"checksum:{checksum}:{filename}:{claim_id or ''}"
        key = (tenant_id, dedupe_key)
        with self._lock:
            existing = self._jobs.get(key)
            if existing:
                return existing
            job = IngestionJob(
                job_id=f"job_{uuid.uuid4().hex}",
                tenant_id=tenant_id,
                document_id=f"pending_{uuid.uuid4().hex}",
                claim_id=claim_id,
                filename=filename,
                checksum=f"sha256:{checksum}",
                idempotency_key=idempotency_key,
                status=IngestionStatus.queued,
                created_at=now,
                updated_at=now,
            )
            self._jobs[key] = job
        return self._run(job, key, content)

    def _run(self, job: IngestionJob, key: tuple[str, str], content: bytes) -> IngestionJob:
        try:
            job = job.model_copy(update={"status": IngestionStatus.parsing, "progress": 25, "updated_at": datetime.now(timezone.utc)})
            self._jobs[key] = job
            job = job.model_copy(update={"status": IngestionStatus.embedding, "progress": 75, "updated_at": datetime.now(timezone.utc)})
            self._jobs[key] = job
            document_id = self._indexer(job.filename, content, job.claim_id)
            job = job.model_copy(update={"document_id": document_id, "status": IngestionStatus.indexed, "progress": 100, "updated_at": datetime.now(timezone.utc)})
        except Exception:
            job = job.model_copy(update={"status": IngestionStatus.failed, "error_code": "INDEXING_FAILED", "error_message": "document indexing failed", "updated_at": datetime.now(timezone.utc)})
        self._jobs[key] = job
        return job

    def get(self, job_id: str, tenant_id: str) -> IngestionJob:
        for job in self._jobs.values():
            if job.job_id == job_id:
                if job.tenant_id != tenant_id:
                    raise PermissionError("job does not belong to tenant")
                return job
        raise KeyError(job_id)
