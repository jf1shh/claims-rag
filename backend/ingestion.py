from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Callable

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
    """Ingestion job lifecycle: idempotent submission, durable records, and
    (in async mode) queue handoff to the ingestion worker.

    Two modes share one contract (``submit`` returns an ``IngestionJob`` whose
    response shape is compatible with a 202 + job_id):

    * **Async (queue set)** -- submit creates/persists a ``queued`` job and
      enqueues a message; the worker (backend/ingestion_worker.py) consumes it
      and advances the job to parsing/embedding/indexed/failed via ``update``.
    * **Sync (no queue, the Phase-0 behavior)** -- submit runs the indexer
      inline and returns the completed job; ``record_result`` lets existing
      synchronous endpoints record an already-finished job (the API's current
      parse -> add_document pipeline) so their responses also carry a job_id.

    Jobs are deduplicated by ``(tenant_id, idempotency_key)`` -- an idempotency
    key explicitly, or a content checksum otherwise -- so replaying an upload
    returns the same job instead of double-indexing.
    """

    def __init__(
        self,
        indexer: Callable[[str, bytes, str | None], str] | None = None,
        queue: Any | None = None,
        job_store: Any | None = None,
    ):
        self._indexer = indexer or self._default_indexer
        self._queue = queue
        self._job_store = job_store
        self._jobs: dict[tuple[str, str], IngestionJob] = {}
        self._lock = Lock()

    @staticmethod
    def _default_indexer(filename: str, content: bytes, claim_id: str | None) -> str:
        del filename, content, claim_id
        return f"doc_{uuid.uuid4().hex}"

    # ------------------------------------------------------------------ #
    # Submission
    # ------------------------------------------------------------------ #

    def submit(
        self,
        *,
        tenant_id: str,
        filename: str,
        content: bytes,
        claim_id: str | None = None,
        idempotency_key: str | None = None,
        blob_key: str | None = None,
    ) -> IngestionJob:
        now = datetime.now(timezone.utc)
        checksum = hashlib.sha256(content).hexdigest()
        dedupe_key = idempotency_key or f"checksum:{checksum}:{filename}:{claim_id or ''}"
        key = (tenant_id, dedupe_key)

        existing = self._find_existing(key)
        if existing is not None:
            return existing

        job = IngestionJob(
            job_id=f"job_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            document_id=f"pending_{uuid.uuid4().hex}",
            claim_id=claim_id,
            filename=filename,
            checksum=f"sha256:{checksum}",
            # The stored dedupe key IS the computed key (explicit idempotency
            # key when given, content checksum otherwise) -- find_by_dedupe_key
            # looks the record up by exactly this value.
            idempotency_key=dedupe_key,
            status=IngestionStatus.queued,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[key] = job
        self._persist(job)

        if self._queue is not None:
            # Async: hand the work to the worker and return the queued job.
            self._queue.enqueue(
                {
                    "job_id": job.job_id,
                    "tenant_id": tenant_id,
                    "filename": filename,
                    "claim_id": claim_id,
                    "blob_key": blob_key or _blob_key(claim_id, filename),
                    "checksum": job.checksum,
                    "etag": job.checksum,
                }
            )
            return job

        # Sync (legacy): index inline so callers get a completed job back.
        return self._run(job, key, content)

    def record_result(
        self,
        *,
        tenant_id: str,
        filename: str,
        content: bytes,
        claim_id: str | None = None,
        document_id: str | None = None,
        status: IngestionStatus = IngestionStatus.indexed,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> IngestionJob:
        """Records an already-finished operation (used by synchronous upload/
        delete endpoints whose pipeline ran inline). Creates a job in the given
        terminal status (indexed/deleted on success, failed on error) -- so the
        endpoint's response carries a job_id in either case, keeping the sync
        contract compatible with the async 202 + job_id shape."""
        now = datetime.now(timezone.utc)
        checksum = hashlib.sha256(content).hexdigest()
        dedupe_key = f"checksum:{checksum}:{filename}:{claim_id or ''}"
        key = (tenant_id, dedupe_key)

        existing = self._find_existing(key)
        if existing is not None:
            return existing

        failed = error_code is not None or status is IngestionStatus.failed
        terminal = IngestionStatus.failed if failed else status
        job = IngestionJob(
            job_id=f"job_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            document_id=document_id or ("" if failed else f"doc_{uuid.uuid4().hex}"),
            claim_id=claim_id,
            filename=filename,
            checksum=f"sha256:{checksum}",
            idempotency_key=dedupe_key,
            status=terminal,
            progress=0 if failed else 100,
            error_code=error_code,
            error_message=error_message,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[key] = job
        self._persist(job)
        return job

    def submit_delete(
        self,
        *,
        tenant_id: str,
        filename: str,
    ) -> IngestionJob:
        """Async delete: creates a queued delete job and enqueues a delete
        message for the worker. Dedupes only while a delete for this filename
        is still in flight -- a completed delete job must not suppress a later
        delete request (content-independent, so a naive checksum dedupe would
        be wrong here)."""
        dedupe_key = f"delete:{filename}"
        key = (tenant_id, dedupe_key)
        existing = self._find_existing(key)
        if existing is not None and existing.status in (
            IngestionStatus.queued,
            IngestionStatus.parsing,
            IngestionStatus.embedding,
        ):
            return existing

        now = datetime.now(timezone.utc)
        job = IngestionJob(
            job_id=f"job_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            document_id="",
            claim_id=None,
            filename=filename,
            checksum="",
            idempotency_key=dedupe_key,
            status=IngestionStatus.queued,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[key] = job
        self._persist(job)
        if self._queue is None:
            raise ValueError("submit_delete requires an ingestion queue (async mode)")
        self._queue.enqueue(
            {
                "job_id": job.job_id,
                "action": "delete",
                "tenant_id": tenant_id,
                "filename": filename,
                "claim_id": None,
            }
        )
        return job

    # ------------------------------------------------------------------ #
    # Reads / worker updates
    # ------------------------------------------------------------------ #

    def get(self, job_id: str, tenant_id: str) -> IngestionJob:
        if self._job_store is not None:
            return self._job_store.get(job_id, tenant_id)
        for job in self._jobs.values():
            if job.job_id == job_id:
                if job.tenant_id != tenant_id:
                    raise PermissionError("job does not belong to tenant")
                return job
        raise KeyError(job_id)

    def update(self, job_id: str, tenant_id: str, **fields: Any) -> IngestionJob:
        """Advances a job (status/progress/document_id/retry_count/error fields)."""
        if self._job_store is not None:
            return self._job_store.update(job_id, tenant_id, **fields)
        for key, job in list(self._jobs.items()):
            if job.job_id == job_id:
                if job.tenant_id != tenant_id:
                    raise PermissionError("job does not belong to tenant")
                updated = job.model_copy(
                    update={**fields, "updated_at": datetime.now(timezone.utc)}
                )
                self._jobs[key] = updated
                return updated
        raise KeyError(job_id)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _find_existing(self, key: tuple[str, str]) -> IngestionJob | None:
        if self._job_store is not None:
            return self._job_store.find_by_dedupe_key(key[0], key[1])
        with self._lock:
            return self._jobs.get(key)

    def _persist(self, job: IngestionJob) -> None:
        if self._job_store is not None:
            self._job_store.create(job)

    def _run(self, job: IngestionJob, key: tuple[str, str], content: bytes) -> IngestionJob:
        try:
            job = self.update(
                job.job_id, job.tenant_id, status=IngestionStatus.parsing, progress=25
            )
            job = self.update(
                job.job_id, job.tenant_id, status=IngestionStatus.embedding, progress=75
            )
            document_id = self._indexer(job.filename, content, job.claim_id)
            job = self.update(
                job.job_id,
                job.tenant_id,
                document_id=document_id,
                status=IngestionStatus.indexed,
                progress=100,
            )
        except Exception:
            job = self.update(
                job.job_id,
                job.tenant_id,
                status=IngestionStatus.failed,
                error_code="INDEXING_FAILED",
                error_message="document indexing failed",
            )
        return job


def _blob_key(claim_id: str | None, filename: str) -> str:
    """Logical object-store key for a source document -- the same
    {scope}/{filename} shape rag_engine._blob_key produces, kept local so
    ingestion stays importable without the heavy rag_engine dependencies."""
    scope = claim_id if claim_id else "global"
    return f"{scope}/{filename}"
