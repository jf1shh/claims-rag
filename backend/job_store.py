from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.ingestion import IngestionJob, IngestionStatus

# Statuses a claim treats as already-finished success: re-claiming (and
# therefore re-processing) one of these is always wrong, no matter how stale
# the previous claim is.
_TERMINAL_SUCCESS_STATUSES = (IngestionStatus.indexed.value, IngestionStatus.deleted.value)

# Terminal statuses for claim purge / retention sweep (M12c-E): a job in one
# of these will never change state again, so it is safe to count as
# "inactive" for purge conflict detection and safe to expire once stale.
_TERMINAL_STATUSES = (
    IngestionStatus.indexed.value,
    IngestionStatus.failed.value,
    IngestionStatus.deleted.value,
)

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
        # timeout=30 matches busy_timeout below: a committed upload must not
        # 500 just because another writer briefly held the lock.
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
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
            columns = {row[1] for row in conn.execute("PRAGMA table_info(ingestion_jobs)")}
            for column in ("claimed_at", "claim_token"):
                if column not in columns:
                    conn.execute(f"ALTER TABLE ingestion_jobs ADD COLUMN {column} TEXT")
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
        """Returns the newest job for this dedupe key. No UNIQUE index backs
        (tenant_id, idempotency_key) -- re-uploading the same content after
        the first job finished creates a second row with the same key -- so
        this must always pick the most recent one, not whichever SQLite
        happens to return first."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM ingestion_jobs WHERE tenant_id = ? AND idempotency_key = ? "
                "ORDER BY created_at DESC LIMIT 1",
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

    def claim(
        self, job_id: str, tenant_id: str, claim_token: str, lease_seconds: float, now: datetime | None = None
    ) -> str:
        """Atomically claims a job for processing: a message delivered twice
        (two queue redeliveries, or two worker replicas) must index it at
        most once. Returns ``"claimed"`` when this call now holds the claim,
        ``"duplicate"`` when the job already reached a terminal-success
        status (the index is already correct; the caller should ack without
        reprocessing), or ``"held"`` when a live claim (``claimed_at`` within
        ``lease_seconds``) belongs to a *different* ``claim_token``. A caller
        presenting the same ``claim_token`` it already holds always renews
        (refreshes ``claimed_at``) even before the lease expires -- that is
        how one worker's own sequential retries of the same message stay
        claimed throughout, while a different worker (different token) is
        blocked until the lease lapses. A claim never changes ``status`` --
        that stays the worker's own progress tracking -- it only records who
        currently owns the job and since when."""
        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(seconds=lease_seconds)).isoformat()
        conn = self._connect()
        try:
            cursor = conn.execute(
                f"""
                UPDATE ingestion_jobs SET claimed_at = ?, claim_token = ?, updated_at = ?
                WHERE job_id = ? AND tenant_id = ?
                  AND status NOT IN ({",".join("?" * len(_TERMINAL_SUCCESS_STATUSES))})
                  AND (claimed_at IS NULL OR claimed_at = '' OR claimed_at < ? OR claim_token = ?)
                """,
                (now.isoformat(), claim_token, now.isoformat(), job_id, tenant_id,
                 *_TERMINAL_SUCCESS_STATUSES, cutoff, claim_token),
            )
            if cursor.rowcount > 0:
                conn.commit()
                return "claimed"
            conn.rollback()
            row = conn.execute(
                "SELECT status FROM ingestion_jobs WHERE job_id = ? AND tenant_id = ?", (job_id, tenant_id)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(job_id)
        if row["status"] in _TERMINAL_SUCCESS_STATUSES:
            return "duplicate"
        return "held"

    # -- claim purge / job retention (M12c-E) ------------------------------ #

    def active_jobs_for_claim(self, tenant_id: str, claim_id: str) -> int:
        """Counts jobs for this (tenant, claim) that are not yet terminal --
        a claim purge must refuse while any of these are in flight, or the
        purge could race an in-progress ingestion/delete."""
        conn = self._connect()
        try:
            row = conn.execute(
                f"""
                SELECT COUNT(*) FROM ingestion_jobs
                WHERE tenant_id = ? AND claim_id IS ?
                  AND status NOT IN ({",".join("?" * len(_TERMINAL_STATUSES))})
                """,
                (tenant_id, claim_id, *_TERMINAL_STATUSES),
            ).fetchone()
        finally:
            conn.close()
        return row[0]

    def count_for_claim(self, tenant_id: str, claim_id: str) -> int:
        """Total job rows for this (tenant, claim), regardless of status --
        a read-only count of what ``delete_for_claim`` would remove."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM ingestion_jobs WHERE tenant_id = ? AND claim_id IS ?",
                (tenant_id, claim_id),
            ).fetchone()
        finally:
            conn.close()
        return row[0]

    def delete_for_claim(self, tenant_id: str, claim_id: str) -> int:
        """Deletes every job row for this (tenant, claim). Returns the count
        deleted. Called only after ``active_jobs_for_claim`` confirms nothing
        is in flight."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM ingestion_jobs WHERE tenant_id = ? AND claim_id IS ?",
                (tenant_id, claim_id),
            )
            conn.commit()
        finally:
            conn.close()
        return cursor.rowcount

    def scrub_document(self, tenant_id: str, claim_id: str | None, filename: str) -> int:
        """Scrubs identifying fields from every job row matching this
        (tenant, claim, filename) scope -- called after a document delete so
        the job store can no longer confirm a given file was ever uploaded.
        Keeps status/timestamps so job history is still legible. Returns the
        count of rows scrubbed."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                UPDATE ingestion_jobs
                SET filename = '[deleted]', checksum = NULL, idempotency_key = NULL, error_message = NULL
                WHERE tenant_id = ? AND claim_id IS ? AND filename = ?
                """,
                (tenant_id, claim_id, filename),
            )
            conn.commit()
        finally:
            conn.close()
        return cursor.rowcount

    def sweep_terminal(self, tenant_id: str, older_than: datetime) -> int:
        """Deletes terminal job rows for this tenant whose ``updated_at`` is
        older than ``older_than`` (JOB_RETENTION_DAYS). Returns the count
        deleted."""
        cutoff = older_than.isoformat()
        conn = self._connect()
        try:
            cursor = conn.execute(
                f"""
                DELETE FROM ingestion_jobs
                WHERE tenant_id = ? AND updated_at < ?
                  AND status IN ({",".join("?" * len(_TERMINAL_STATUSES))})
                """,
                (tenant_id, cutoff, *_TERMINAL_STATUSES),
            )
            conn.commit()
        finally:
            conn.close()
        return cursor.rowcount


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
