from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from backend.ingestion import IngestionJob, IngestionStatus

# Fields the worker/API may update on an existing job. Everything else is
# immutable once created (job_id, tenant_id, filename, scope, checksum).
_UPDATABLE_FIELDS = {
    "document_id",
    "status",
    "progress",
    "retry_count",
    "error_code",
    "error_message",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteJobStore:
    """Durable ingestion-job records in their own SQLite file.

    Jobs are queue metadata, not retrieval data -- they describe uploads the
    worker is (or was) processing -- so they live in a separate database from
    the vector store and work identically whether VECTOR_STORE is sqlite or
    postgres. Records survive API/worker restarts, which the in-memory
    Phase-0 IngestionService could not, so GET /api/jobs/{id} stays truthful
    across process boundaries (SQS + separate worker). Every read is
    tenant-guarded: get() raises PermissionError when the caller's tenant
    does not own the job.
    """

    def __init__(self, db_path: str):
        if not db_path:
            raise ValueError("db_path must not be empty")
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                    job_id         TEXT PRIMARY KEY,
                    tenant_id      TEXT NOT NULL,
                    document_id    TEXT,
                    claim_id       TEXT,
                    filename       TEXT NOT NULL,
                    checksum       TEXT,
                    idempotency_key TEXT,
                    status         TEXT NOT NULL,
                    progress       INTEGER NOT NULL DEFAULT 0,
                    retry_count    INTEGER NOT NULL DEFAULT 0,
                    error_code     TEXT,
                    error_message  TEXT,
                    created_at     TEXT NOT NULL,
                    updated_at     TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_ingestion_jobs_dedupe ON ingestion_jobs (tenant_id, idempotency_key)"
            )
            conn.commit()
        finally:
            conn.close()

    # -- reads ------------------------------------------------------------ #

    def get(self, job_id: str, tenant_id: str) -> IngestionJob:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM ingestion_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(job_id)
        if row["tenant_id"] != tenant_id:
            raise PermissionError("job does not belong to tenant")
        return _row_to_job(row)

    def find_by_dedupe_key(self, tenant_id: str, dedupe_key: str) -> IngestionJob | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM ingestion_jobs WHERE tenant_id = ? AND idempotency_key = ?",
                (tenant_id, dedupe_key),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_job(row) if row else None

    # -- writes ----------------------------------------------------------- #

    def create(self, job: IngestionJob) -> IngestionJob:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO ingestion_jobs (
                    job_id, tenant_id, document_id, claim_id, filename, checksum,
                    idempotency_key, status, progress, retry_count, error_code,
                    error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.tenant_id,
                    job.document_id,
                    job.claim_id,
                    job.filename,
                    job.checksum,
                    job.idempotency_key,
                    job.status.value,
                    job.progress,
                    job.retry_count,
                    job.error_code,
                    job.error_message,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        # INSERT OR IGNORE: an idempotent replay returns the existing row.
        return self.get(job.job_id, job.tenant_id)

    def update(self, job_id: str, tenant_id: str, **fields: Any) -> IngestionJob:
        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"cannot update non-updatable job fields: {sorted(unknown)}")
        sets = ", ".join(f"{name} = ?" for name in fields)
        values = list(fields.values())
        conn = self._connect()
        try:
            cursor = conn.execute(
                f"UPDATE ingestion_jobs SET {sets}, updated_at = ? WHERE job_id = ? AND tenant_id = ?",
                (*values, _now(), job_id, tenant_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(job_id)
            conn.commit()
        finally:
            conn.close()
        return self.get(job_id, tenant_id)


def _row_to_job(row: sqlite3.Row) -> IngestionJob:
    return IngestionJob(
        job_id=row["job_id"],
        tenant_id=row["tenant_id"],
        document_id=row["document_id"],
        claim_id=row["claim_id"],
        filename=row["filename"],
        checksum=row["checksum"],
        idempotency_key=row["idempotency_key"],
        status=IngestionStatus(row["status"]),
        progress=row["progress"],
        retry_count=row["retry_count"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
