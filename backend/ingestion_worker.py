from __future__ import annotations

import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from backend.ingestion import IngestionJob, IngestionStatus
from backend.queue import Queue, QueueMessage
from backend.rag_engine import DocumentParser, VectorStore

logger = logging.getLogger(__name__)

ALLOWED_FILE_TYPES = {"pdf", "docx", "xlsx", "xls", "txt"}

# Outcomes of processing a single message.
INDEXED = "indexed"  # parsed, embedded, and transactionally upserted; acked
REJECTED = "rejected"  # retryable failure; message made visible again with backoff
DEAD_LETTERED = "dead_lettered"  # permanent failure or retries exhausted
MALFORMED = "malformed"  # message missing required fields; dropped to DLQ

_MAX_BACKOFF_SECONDS = 60.0


class IngestionWorker:
    """Consumes ingestion messages and indexes their documents.

    Pipeline per message: fetch source bytes from the blob store -> parse to
    text -> chunk -> batch-embed -> ``vector_store.add_document`` (a single
    transaction, so a mid-embedding failure rolls back and can never leave a
    partially indexed document searchable -- the "no half-state" guarantee).
    The embed/upsert step is the incremental one: only the one document's rows
    change, never a full-corpus rebuild (the Postgres backend's HNSW index
    makes the next search incremental too).

    When a ``job_store`` is attached, the worker keeps the job record truthful
    as it goes (queued -> parsing -> embedding -> indexed/failed, retry_count
    bumped on each rejected attempt) -- this is what GET /api/jobs/{id} reads.
    A missing record is created from the message (the S3-event-driven flow has
    no pre-created job); job-store failures are logged and never abort message
    processing, because the document index is the source of truth, not the
    job record.

    Failures are classified so retries are meaningful:

    * Permanent (dead-letter immediately, no retry): missing blob, unsupported
      file type, corrupt/unparseable file, empty document, or a ``ValueError``
      from the store (safe-filename rejection, per-scope overwrite conflict,
      embedding-dimension mismatch).
    * Retryable (reject with exponential backoff until ``max_retries``, then
      dead-letter): any other failure inside the store, e.g. the embedding
      engine failing or a transient database error.

    The embedding engine is injected as a *factory* so importing this module
    never pulls in torch/sentence-transformers (lazy-ML-imports constraint).
    """

    def __init__(
        self,
        queue: Queue,
        vector_store: VectorStore,
        blob_store: Any,
        embedding_engine_factory: Callable[[], Any],
        *,
        max_retries: int = 5,
        backoff_base_seconds: float = 2.0,
        poll_interval_seconds: float = 1.0,
        job_store: Any | None = None,
    ):
        if blob_store is None:
            raise ValueError("IngestionWorker requires a blob store (filesystem or S3 adapter)")
        if max_retries < 1:
            raise ValueError("max_retries must be at least 1")
        self.queue = queue
        self.vector_store = vector_store
        self.blob_store = blob_store
        self._embedding_engine_factory = embedding_engine_factory
        self.job_store = job_store
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    # Processing
    # ------------------------------------------------------------------ #

    def process_message(self, message: QueueMessage) -> str:
        """Processes one message; returns its outcome string."""
        payload = message.payload
        blob_key = payload.get("blob_key")
        filename = payload.get("filename")
        claim_id = payload.get("claim_id")
        if not blob_key or not filename:
            self.queue.dead_letter(message)
            return MALFORMED

        job = self._ensure_job(message)
        content = self._fetch_or_dead_letter(message, blob_key)
        if content is None:
            self._mark_failed(job, "BLOB_NOT_FOUND", "source document not found in object storage")
            return DEAD_LETTERED

        outcome = self._index(message, filename, claim_id, content, job)
        if outcome == INDEXED:
            self.queue.ack(message)
        return outcome

    def _ensure_job(self, message: QueueMessage) -> IngestionJob | None:
        """Loads the job record for a message, creating it from the payload if
        it does not exist yet (S3-event-driven messages arrive without one)."""
        if self.job_store is None:
            return None
        job_id = message.payload.get("job_id")
        tenant_id = message.payload.get("tenant_id")
        if not job_id or not tenant_id:
            return None
        try:
            return self.job_store.get(job_id, tenant_id)
        except KeyError:
            pass
        now = datetime.now(timezone.utc)
        job = IngestionJob(
            job_id=job_id,
            tenant_id=tenant_id,
            document_id=f"pending_{uuid.uuid4().hex}",
            claim_id=message.payload.get("claim_id"),
            filename=message.payload["filename"],
            checksum=message.payload.get("etag") or "",
            status=IngestionStatus.queued,
            created_at=now,
            updated_at=now,
        )
        try:
            return self.job_store.create(job)
        except Exception:
            logger.warning("could not create job record for %s; continuing without job tracking", job_id, exc_info=True)
            return None

    def _fetch_or_dead_letter(self, message: QueueMessage, blob_key: str) -> bytes | None:
        """Fetches source bytes; a missing object is permanent (its event is stale)."""
        try:
            return self.blob_store.get(blob_key)
        except Exception as exc:  # object gone / provider error on fetch
            logger.warning("blob fetch failed for %s: %s", blob_key, exc)
            self.queue.dead_letter(message)
            return None

    def _index(
        self,
        message: QueueMessage,
        filename: str,
        claim_id: str | None,
        content: bytes,
        job: IngestionJob | None,
    ) -> str:
        file_ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if file_ext not in ALLOWED_FILE_TYPES:
            self._mark_failed(job, "UNSUPPORTED_FILE_TYPE", "unsupported file type")
            self.queue.dead_letter(message)
            logger.warning("unsupported file type %r for %s; dead-lettered", file_ext, filename)
            return DEAD_LETTERED

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            self._set_job(job, status=IngestionStatus.parsing, progress=25)

            try:
                text = DocumentParser.parse(tmp_path, file_ext)
            except Exception:
                # Corrupt/truncated/password-protected files fail deep inside the
                # parser. That is a client error (the bytes are unreadable), so
                # retrying will never succeed -- dead-letter immediately.
                self._mark_failed(job, "CORRUPT_FILE", "could not read the file -- it may be corrupt, truncated, or password-protected")
                self.queue.dead_letter(message)
                logger.warning("unparseable file %s; dead-lettered", filename)
                return DEAD_LETTERED

            if not text.strip():
                self._mark_failed(job, "EMPTY_DOCUMENT", "document is empty or unreadable")
                self.queue.dead_letter(message)
                logger.warning("empty document %s; dead-lettered", filename)
                return DEAD_LETTERED

            self._set_job(job, status=IngestionStatus.embedding, progress=75)

            try:
                doc_id, _ = self.vector_store.add_document(
                    filename=filename,
                    file_type=file_ext,
                    file_size=len(content),
                    text=text,
                    embedding_engine=self._embedding_engine_factory(),
                    claim_id=claim_id,
                    file_path=tmp_path,
                )
            except ValueError as exc:
                # Client-controllable rejections (safe_filename, per-scope
                # overwrite conflict, embedding-dimension mismatch) are
                # permanent -- retrying cannot change the outcome.
                self._mark_failed(job, "REJECTED_BY_STORE", str(exc))
                self.queue.dead_letter(message)
                logger.warning("permanent store rejection for %s: %s", filename, exc)
                return DEAD_LETTERED
            except Exception as exc:
                # Embedding-engine failure or a transient store error: the
                # transaction was rolled back (no half-state), so retrying the
                # same message is safe. Reject with exponential backoff until
                # the retry budget is exhausted, then dead-letter.
                return self._retry_or_dead_letter(message, filename, exc, job)

            self._set_job(job, status=IngestionStatus.indexed, progress=100, document_id=str(doc_id))
            logger.info("indexed %s (claim=%s)", filename, claim_id or "global")
            return INDEXED
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _retry_or_dead_letter(
        self, message: QueueMessage, filename: str, exc: Exception, job: IngestionJob | None
    ) -> str:
        if message.receive_count >= self.max_retries:
            self._mark_failed(job, "RETRIES_EXHAUSTED", "document indexing failed after repeated attempts")
            logger.warning("retries exhausted for %s; dead-lettered (%s)", filename, exc)
            self.queue.dead_letter(message)
            return DEAD_LETTERED
        self._set_job(
            job,
            status=IngestionStatus.queued,
            progress=0,
            retry_count=(job.retry_count + 1) if job is not None else 0,
        )
        backoff = min(self.backoff_base_seconds * (2 ** max(0, message.receive_count - 1)), _MAX_BACKOFF_SECONDS)
        logger.warning("retryable failure for %s (attempt %d): %s", filename, message.receive_count, exc)
        self.queue.reject(message, backoff_seconds=backoff)
        return REJECTED

    # ------------------------------------------------------------------ #
    # Job-record bookkeeping (observability, never load-bearing)
    # ------------------------------------------------------------------ #

    def _set_job(self, job: IngestionJob | None, **fields: Any) -> None:
        if job is None or self.job_store is None:
            return
        try:
            self.job_store.update(job.job_id, job.tenant_id, **fields)
        except Exception:
            logger.warning("job update failed for %s; continuing", job.job_id, exc_info=True)

    def _mark_failed(self, job: IngestionJob | None, error_code: str, error_message: str) -> None:
        self._set_job(
            job,
            status=IngestionStatus.failed,
            progress=0,
            error_code=error_code,
            error_message=error_message,
        )

    # ------------------------------------------------------------------ #
    # Run loops
    # ------------------------------------------------------------------ #

    def run_once(self) -> int:
        """Processes every currently-visible message; returns how many were handled."""
        handled = 0
        while True:
            message = self.queue.dequeue()
            if message is None:
                return handled
            self.process_message(message)
            handled += 1

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        """Polls the queue until the stop event is set (blocking)."""
        stop = stop_event or self._stop_event
        while not stop.is_set():
            self.run_once()
            stop.wait(self.poll_interval_seconds)

    def start(self) -> None:
        """Starts a background polling thread (useful for local dev)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.run_forever, kwargs={"stop_event": self._stop_event}, daemon=True, name="ingestion-worker"
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stops the background thread and waits for it to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None


def build_ingestion_worker(
    settings,
    vector_store: VectorStore,
    blob_store: Any,
    embedding_engine_factory: Callable[[], Any],
    job_store: Any | None = None,
) -> IngestionWorker:
    """Constructs a worker from Settings, mirroring the other factory helpers."""
    from backend.queue import InProcessQueue, SQSQueue

    if settings.queue_provider == "sqs":
        if not settings.sqs_queue_url:
            raise ValueError("SQS_QUEUE_URL is required when QUEUE_PROVIDER is sqs")
        queue: Queue = SQSQueue(
            queue_url=settings.sqs_queue_url,
            region=settings.sqs_region,
            endpoint_url=settings.sqs_endpoint_url,
            dlq_url=settings.sqs_dlq_url,
        )
    else:
        queue = InProcessQueue()
    return IngestionWorker(
        queue=queue,
        vector_store=vector_store,
        blob_store=blob_store,
        embedding_engine_factory=embedding_engine_factory,
        max_retries=settings.worker_max_retries,
        backoff_base_seconds=settings.worker_backoff_base_seconds,
        poll_interval_seconds=settings.worker_poll_interval_seconds,
        job_store=job_store,
    )
