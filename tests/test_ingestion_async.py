"""Async ingestion lifecycle tests (Phase 3.2).

The IngestionService (queue + durable job store) and the IngestionWorker must
agree on one job record: submit creates it queued and enqueues; the worker
advances it parsing -> embedding -> indexed (or failed), bumps retry_count on
each rejected attempt, and never leaves a job claiming success when nothing
was indexed.
"""

import hashlib

from backend.ingestion import IngestionService, IngestionStatus
from backend.ingestion_worker import DEAD_LETTERED, INDEXED, REJECTED, IngestionWorker
from backend.job_store import SqliteJobStore
from backend.queue import InProcessQueue

LABOR_TEXT = (
    "Regional labor rate schedule 2026. The maximum allowed mechanical labor "
    "rate for Nevada is $110 per hour."
)


class _FakeEmbedder:
    def _vec(self, text):
        digest = hashlib.md5((text or "").encode()).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(16)]

    def embed_chunks(self, chunks):
        return [self._vec(c) for c in chunks]


class _FailingEmbedder:
    def embed_chunks(self, chunks):
        raise RuntimeError("embedding engine down")


def _make_worker(queue, store, blob, job_store, embedder=None, **kwargs):
    defaults = {"backoff_base_seconds": 0.0, "max_retries": 3}
    defaults.update(kwargs)
    return IngestionWorker(
        queue=queue,
        vector_store=store,
        blob_store=blob,
        embedding_engine_factory=lambda: embedder or _FakeEmbedder(),
        job_store=job_store,
        **defaults,
    )


def test_given_queue_when_submitted_then_job_is_queued_and_message_enqueued(tmp_path):
    queue = InProcessQueue()
    service = IngestionService(queue=queue, job_store=SqliteJobStore(str(tmp_path / "jobs.db")))
    job = service.submit(tenant_id="tenant-a", filename="labor.txt", content=LABOR_TEXT.encode())

    assert job.status is IngestionStatus.queued
    assert job.progress == 0
    message = queue.dequeue()
    assert message is not None
    assert message.payload["job_id"] == job.job_id
    assert message.payload["blob_key"] == "global/labor.txt"
    assert message.payload["etag"] == job.checksum


def test_given_worker_when_processes_message_then_job_reaches_indexed(tmp_path):
    from backend.blob_store import LocalDocumentBlobStore
    from backend.rag_engine import SQLiteVectorStore

    queue = InProcessQueue()
    blob = LocalDocumentBlobStore(tmp_path / "blobs")
    store = SQLiteVectorStore(db_path=str(tmp_path / "test.db"), storage_dir=str(tmp_path / "docs"), blob_store=blob)
    job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
    service = IngestionService(queue=queue, job_store=job_store)
    blob.put("global/labor.txt", LABOR_TEXT.encode(), "text/plain")

    job = service.submit(tenant_id="tenant-a", filename="labor.txt", content=LABOR_TEXT.encode())
    worker = _make_worker(queue, store, blob, job_store)

    assert worker.process_message(queue.dequeue()) == INDEXED
    recorded = job_store.get(job.job_id, "tenant-a")
    assert recorded.status is IngestionStatus.indexed
    assert recorded.progress == 100
    assert recorded.document_id.startswith("doc_") or recorded.document_id.isdigit()
    assert store.get_document_content("labor.txt") == LABOR_TEXT


def test_given_embedding_failure_then_job_retries_and_finally_fails(tmp_path):
    from backend.blob_store import LocalDocumentBlobStore
    from backend.rag_engine import SQLiteVectorStore

    queue = InProcessQueue()
    blob = LocalDocumentBlobStore(tmp_path / "blobs")
    store = SQLiteVectorStore(db_path=str(tmp_path / "test.db"), storage_dir=str(tmp_path / "docs"), blob_store=blob)
    job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
    service = IngestionService(queue=queue, job_store=job_store)
    blob.put("global/labor.txt", LABOR_TEXT.encode(), "text/plain")
    worker = _make_worker(queue, store, blob, job_store, embedder=_FailingEmbedder())

    job = service.submit(tenant_id="tenant-a", filename="labor.txt", content=LABOR_TEXT.encode())

    assert worker.process_message(queue.dequeue()) == REJECTED
    retried = job_store.get(job.job_id, "tenant-a")
    assert retried.status is IngestionStatus.queued  # ready for the retry
    assert retried.retry_count == 1

    # Remaining attempts: rc=1 and rc=2 reject, rc=3 (== max_retries) dead-letters.
    outcomes = []
    while True:
        message = queue.dequeue()
        if message is None:
            break
        outcomes.append(worker.process_message(message))
    assert outcomes == [REJECTED, REJECTED, DEAD_LETTERED]

    failed = job_store.get(job.job_id, "tenant-a")
    assert failed.status is IngestionStatus.failed
    assert failed.error_code == "RETRIES_EXHAUSTED"
    assert store.get_all_documents() == []  # no half-state


def test_given_same_content_submitted_twice_then_same_job_returned(tmp_path):
    queue = InProcessQueue()
    job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
    service = IngestionService(queue=queue, job_store=job_store)

    first = service.submit(tenant_id="tenant-a", filename="labor.txt", content=LABOR_TEXT.encode())
    second = service.submit(tenant_id="tenant-a", filename="labor.txt", content=LABOR_TEXT.encode())
    assert first.job_id == second.job_id
    # Only one message was enqueued (the dedupe happened before enqueue).
    assert queue.dequeue() is not None
    assert queue.dequeue() is None


def test_given_worker_message_without_precreated_job_then_record_is_created(tmp_path):
    """S3-event-driven messages arrive with a job_id but no job record yet; the
    worker must create it from the payload so GET /api/jobs/{id} works for the
    event-driven flow too."""
    from backend.blob_store import LocalDocumentBlobStore
    from backend.rag_engine import SQLiteVectorStore

    queue = InProcessQueue()
    blob = LocalDocumentBlobStore(tmp_path / "blobs")
    store = SQLiteVectorStore(db_path=str(tmp_path / "test.db"), storage_dir=str(tmp_path / "docs"), blob_store=blob)
    job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
    worker = _make_worker(queue, store, blob, job_store)

    blob.put("global/labor.txt", LABOR_TEXT.encode(), "text/plain")
    queue.enqueue(
        {
            "job_id": "job_from_event",
            "tenant_id": "tenant-a",
            "filename": "labor.txt",
            "claim_id": None,
            "blob_key": "global/labor.txt",
            "etag": "sha256:abc",
        }
    )

    assert worker.process_message(queue.dequeue()) == INDEXED
    recorded = job_store.get("job_from_event", "tenant-a")
    assert recorded.status is IngestionStatus.indexed
    assert recorded.checksum == "sha256:abc"


def test_given_legacy_service_without_queue_then_submit_still_indexes_inline():
    """The Phase-0 synchronous contract (no queue) must keep working."""
    service = IngestionService()
    job = service.submit(tenant_id="tenant-a", filename="labor.txt", content=b"x")
    assert job.status is IngestionStatus.indexed
    assert job.progress == 100


def test_given_record_result_then_job_reflects_success(tmp_path):
    job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
    service = IngestionService(job_store=job_store)
    job = service.record_result(
        tenant_id="tenant-a", filename="labor.txt", content=b"x", document_id="doc_7"
    )
    assert job.status is IngestionStatus.indexed
    assert job.progress == 100
    assert job_store.get(job.job_id, "tenant-a").document_id == "doc_7"
