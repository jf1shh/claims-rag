"""Ingestion worker tests (Phase 3 async ingestion, milestone 3.1).

Covers the worker's parse -> chunk -> embed -> transactional upsert pipeline,
the retryable-vs-permanent failure classification (embed failures retry with
backoff and dead-letter once the budget is exhausted -- and never leave a
half-indexed document searchable), and idempotent replay of the same message.
All hermetic: in-process queue + local blob store + SQLite store + a
torch-free deterministic fake embedder.
"""

import hashlib

import pytest

from backend.blob_store import LocalDocumentBlobStore
from backend.ingestion import IngestionStatus
from backend.ingestion_worker import (
    DEAD_LETTERED,
    DELETED,
    DUPLICATE,
    INDEXED,
    MALFORMED,
    REJECTED,
    IngestionWorker,
)
from backend.job_store import SqliteJobStore
from backend.queue import InProcessQueue
from backend.rag_engine import SQLiteVectorStore

LABOR_TEXT = (
    "Regional labor rate schedule 2026. The maximum allowed mechanical labor "
    "rate for Nevada is $110 per hour. Sheet metal repair is capped at $62 "
    "per hour and refinishing at $62 per hour statewide."
)


class _FakeEmbedder:
    """Deterministic, torch-free embedder (mirrors tests/test_store_blob_wiring.py)."""

    def _vec(self, text):
        digest = hashlib.md5((text or "").encode()).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(16)]

    def embed_chunks(self, chunks):
        return [self._vec(c) for c in chunks]

    def embed_query(self, query):
        return self._vec(query)


class _FailingEmbedder:
    """An embedding engine that always fails -- the retryable failure case."""

    def embed_chunks(self, chunks):
        raise RuntimeError("embedding engine down")


class _FlakyEmbedder:
    """Fails the first N embed calls, then succeeds -- the recoverable case."""

    def __init__(self, failures: int = 2):
        self.failures = failures
        self.calls = 0

    def embed_chunks(self, chunks):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("transient embedding engine failure")
        return _FakeEmbedder().embed_chunks(chunks)


def _job_message(filename: str, blob_key: str, claim_id=None, job_id="job_1", etag="abc123"):
    return {
        "job_id": job_id,
        "tenant_id": "tenant-a",
        "filename": filename,
        "claim_id": claim_id,
        "blob_key": blob_key,
        "etag": etag,
    }


@pytest.fixture
def harness(tmp_path):
    """A fully hermetic worker harness: queue + blob store + SQLite store."""
    queue = InProcessQueue()
    blob = LocalDocumentBlobStore(tmp_path / "blobs")
    store = SQLiteVectorStore(
        db_path=str(tmp_path / "test.db"),
        storage_dir=str(tmp_path / "stored_documents"),
        blob_store=blob,
    )
    return queue, blob, store


def _worker(harness, embedder=None, **kwargs):
    queue, blob, store = harness
    defaults = {"backoff_base_seconds": 0.0, "max_retries": 3}
    defaults.update(kwargs)
    return IngestionWorker(
        queue=queue,
        vector_store=store,
        blob_store=blob,
        embedding_engine_factory=lambda: embedder or _FakeEmbedder(),
        **defaults,
    )


def test_given_blob_when_message_processed_then_document_is_indexed_and_acked(harness):
    queue, blob, store = harness
    blob.put("global/labor.txt", LABOR_TEXT.encode(), "text/plain")
    queue.enqueue(_job_message("labor.txt", "global/labor.txt"))

    assert _worker(harness).process_message(queue.dequeue()) == INDEXED

    docs = store.get_all_documents()
    assert len(docs) == 1
    assert docs[0]["filename"] == "labor.txt"
    assert store.get_document_content("labor.txt") == LABOR_TEXT
    # Successfully processed messages are acked and gone.
    assert queue.pending_count == 0
    assert queue.in_flight_count == 0


def test_given_claim_scoped_blob_when_message_processed_then_document_attaches_to_claim(harness):
    queue, blob, store = harness
    blob.put("claim-9/labor.txt", LABOR_TEXT.encode(), "text/plain")
    queue.enqueue(_job_message("labor.txt", "claim-9/labor.txt", claim_id="claim-9"))

    assert _worker(harness).process_message(queue.dequeue()) == INDEXED

    assert len(store.get_claim_documents("claim-9")) == 1
    assert store.get_all_documents() == []


def test_given_embedding_failure_when_message_processed_then_retried_with_no_half_state(harness):
    queue, blob, store = harness
    blob.put("global/labor.txt", LABOR_TEXT.encode(), "text/plain")
    queue.enqueue(_job_message("labor.txt", "global/labor.txt"))
    worker = _worker(harness, embedder=_FailingEmbedder())

    first = queue.dequeue()
    assert worker.process_message(first) == REJECTED
    # The transaction rolled back: nothing is indexed, nothing is searchable.
    assert store.get_all_documents() == []
    assert store.get_claim_documents("claim-9") == []

    # The message is back in the queue for another attempt (receive_count bumped).
    retried = queue.dequeue()
    assert retried is not None
    assert retried.receive_count == first.receive_count + 1

    # Each failure rejects with backoff until the retry budget is exhausted
    # (max_retries=3 -> three attempts total, the first above), then the message
    # dead-letters. Every attempt rolls back -- no half-state at any point.
    outcomes = [worker.process_message(retried)]
    while True:
        message = queue.dequeue()
        if message is None:
            break
        outcomes.append(worker.process_message(message))
    assert outcomes == [REJECTED, REJECTED, DEAD_LETTERED]
    assert queue.dead_letter_count == 1
    assert store.get_all_documents() == []


def test_given_corrupt_file_when_message_processed_then_dead_lettered_without_retry(harness):
    queue, blob, store = harness
    blob.put("global/broken.pdf", b"%PDF-1.4 this is not a real pdf at all", "application/pdf")
    queue.enqueue(_job_message("broken.pdf", "global/broken.pdf"))

    assert _worker(harness).process_message(queue.dequeue()) == DEAD_LETTERED
    assert queue.dead_letter_count == 1
    assert queue.pending_count == 0
    assert store.get_all_documents() == []


def test_given_empty_document_when_message_processed_then_dead_lettered(harness):
    queue, blob, store = harness
    blob.put("global/blank.txt", b"   \n\t ", "text/plain")
    queue.enqueue(_job_message("blank.txt", "global/blank.txt"))

    assert _worker(harness).process_message(queue.dequeue()) == DEAD_LETTERED
    assert store.get_all_documents() == []


def test_given_unsupported_file_type_when_message_processed_then_dead_lettered(harness):
    queue, blob, store = harness
    blob.put("global/virus.exe", b"MZ\x90\x00", "application/octet-stream")
    queue.enqueue(_job_message("virus.exe", "global/virus.exe"))

    assert _worker(harness).process_message(queue.dequeue()) == DEAD_LETTERED
    assert store.get_all_documents() == []


def test_given_missing_blob_when_message_processed_then_dead_lettered(harness):
    queue, _, store = harness
    queue.enqueue(_job_message("ghost.txt", "global/ghost.txt"))

    assert _worker(harness).process_message(queue.dequeue()) == DEAD_LETTERED
    assert store.get_all_documents() == []


def test_given_malformed_message_when_processed_then_dead_lettered(harness):
    queue, _, store = harness
    queue.enqueue({"job_id": "job_1", "tenant_id": "tenant-a"})  # no filename/blob_key

    assert _worker(harness).process_message(queue.dequeue()) == MALFORMED
    assert queue.dead_letter_count == 1
    assert store.get_all_documents() == []


def test_given_scope_conflict_when_message_processed_then_dead_lettered_and_other_scope_intact(harness):
    queue, blob, store = harness
    # Claim-9 already owns report.txt; a global event for the same filename must
    # not clobber it (per-scope overwrite guard surfaced as a permanent failure).
    store.add_document("report.txt", "txt", 100, "claim nine report", _FakeEmbedder(), claim_id="claim-9")
    blob.put("global/report.txt", b"global report", "text/plain")
    queue.enqueue(_job_message("report.txt", "global/report.txt"))

    assert _worker(harness).process_message(queue.dequeue()) == DEAD_LETTERED
    claim_docs = store.get_claim_documents("claim-9")
    assert len(claim_docs) == 1
    assert claim_docs[0]["filename"] == "report.txt"
    assert store.get_all_documents() == []


def test_given_same_message_replayed_then_one_consistent_document_version(harness):
    queue, blob, store = harness
    blob.put("global/labor.txt", LABOR_TEXT.encode(), "text/plain")
    worker = _worker(harness)

    for _ in range(2):
        queue.enqueue(_job_message("labor.txt", "global/labor.txt"))
        assert worker.process_message(queue.dequeue()) == INDEXED

    docs = store.get_all_documents()
    assert len(docs) == 1  # overwrite-in-place, not a duplicate
    assert store.get_document_content("labor.txt") == LABOR_TEXT


def test_given_three_messages_when_run_once_then_all_processed(harness):
    queue, blob, store = harness
    for i in range(3):
        blob.put(f"global/doc{i}.txt", f"document number {i}".encode(), "text/plain")
        queue.enqueue(_job_message(f"doc{i}.txt", f"global/doc{i}.txt"))

    assert _worker(harness).run_once() == 3
    assert len(store.get_all_documents()) == 3
    assert queue.pending_count == 0


def test_given_no_blob_store_then_worker_is_rejected(harness):
    queue, _, store = harness
    with pytest.raises(ValueError):
        IngestionWorker(
            queue=queue,
            vector_store=store,
            blob_store=None,
            embedding_engine_factory=_FakeEmbedder,
        )


# ---------------------------------------------------------------------------
# Phase 3.2/3.3 -- delete messages, etag idempotency, retry drills, metrics
# ---------------------------------------------------------------------------

def _delete_message(filename, job_id="job_del1"):
    return {"job_id": job_id, "action": "delete", "tenant_id": "tenant-a", "filename": filename, "claim_id": None}


def test_given_delete_message_when_processed_then_document_removed_and_job_deleted(harness, tmp_path):
    queue, blob, store = harness
    job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
    worker = _worker(harness, job_store=job_store)
    store.add_document("labor.txt", "txt", 100, LABOR_TEXT, _FakeEmbedder())

    queue.enqueue(_delete_message("labor.txt"))
    assert worker.process_message(queue.dequeue()) == DELETED

    assert store.get_all_documents() == []
    job = job_store.get("job_del1", "tenant-a")
    assert job.status is IngestionStatus.deleted
    assert job.progress == 100
    assert worker.stats()["deleted"] == 1
    assert worker.stats()["processed"] == 1


def test_given_delete_message_for_missing_document_then_dead_lettered(harness, tmp_path):
    queue, blob, store = harness
    job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
    worker = _worker(harness, job_store=job_store)

    queue.enqueue(_delete_message("ghost.txt"))
    assert worker.process_message(queue.dequeue()) == DEAD_LETTERED

    job = job_store.get("job_del1", "tenant-a")
    assert job.status is IngestionStatus.failed
    assert job.error_code == "DOCUMENT_NOT_FOUND"
    assert worker.stats()["dead_lettered"] == 1


def test_given_delete_message_with_unsafe_filename_then_dead_lettered(harness, tmp_path):
    queue, blob, store = harness
    job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
    worker = _worker(harness, job_store=job_store)

    queue.enqueue(_delete_message("."))
    assert worker.process_message(queue.dequeue()) == DEAD_LETTERED
    job = job_store.get("job_del1", "tenant-a")
    assert job.error_code == "REJECTED_BY_STORE"


def test_given_duplicate_s3_event_then_acked_without_reindexing(harness, tmp_path):
    queue, blob, store = harness
    job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
    worker = _worker(harness, job_store=job_store)
    blob.put("global/labor.txt", LABOR_TEXT.encode(), "text/plain")

    queue.enqueue(_job_message("labor.txt", "global/labor.txt", job_id="job_a", etag="e1"))
    assert worker.process_message(queue.dequeue()) == INDEXED

    # Same object version re-delivered (same etag, new event job_id).
    queue.enqueue(_job_message("labor.txt", "global/labor.txt", job_id="job_b", etag="e1"))
    assert worker.process_message(queue.dequeue()) == DUPLICATE
    assert len(store.get_all_documents()) == 1  # not re-indexed
    with pytest.raises(KeyError):
        job_store.get("job_b", "tenant-a")  # no duplicate job record either
    assert worker.stats()["duplicates"] == 1


def test_given_same_object_with_new_etag_then_reindexed(harness, tmp_path):
    queue, blob, store = harness
    job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
    worker = _worker(harness, job_store=job_store)
    blob.put("global/labor.txt", LABOR_TEXT.encode(), "text/plain")

    queue.enqueue(_job_message("labor.txt", "global/labor.txt", job_id="job_a", etag="e1"))
    assert worker.process_message(queue.dequeue()) == INDEXED
    # Same object, new version: re-index (overwrite-in-place).
    queue.enqueue(_job_message("labor.txt", "global/labor.txt", job_id="job_c", etag="e2"))
    assert worker.process_message(queue.dequeue()) == INDEXED
    assert len(store.get_all_documents()) == 1
    job = job_store.get("job_c", "tenant-a")
    assert job.status is IngestionStatus.indexed


def test_given_transient_embed_failure_then_retry_recovers_and_job_indexed(harness, tmp_path):
    queue, blob, store = harness
    job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
    worker = _worker(harness, embedder=_FlakyEmbedder(failures=2), job_store=job_store)
    blob.put("global/labor.txt", LABOR_TEXT.encode(), "text/plain")
    queue.enqueue(_job_message("labor.txt", "global/labor.txt", job_id="job_1", etag="e1"))

    assert worker.process_message(queue.dequeue()) == REJECTED
    assert worker.process_message(queue.dequeue()) == REJECTED
    assert worker.process_message(queue.dequeue()) == INDEXED

    job = job_store.get("job_1", "tenant-a")
    assert job.status is IngestionStatus.indexed
    assert job.retry_count == 2
    assert store.get_document_content("labor.txt") == LABOR_TEXT
    assert worker.stats()["rejected"] == 2
    assert worker.stats()["indexed"] == 1


def test_given_mixed_outcomes_then_stats_reflect_each(harness, tmp_path):
    queue, blob, store = harness
    job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
    worker = _worker(harness, job_store=job_store)
    blob.put("global/good.txt", b"good document text", "text/plain")
    blob.put("global/bad.pdf", b"%PDF-1.4 garbage", "application/pdf")
    queue.enqueue(_job_message("good.txt", "global/good.txt", job_id="job_g"))
    queue.enqueue(_job_message("bad.pdf", "global/bad.pdf", job_id="job_b"))
    queue.enqueue(_delete_message("ghost.txt", job_id="job_d"))

    worker.run_once()
    stats = worker.stats()
    assert stats["processed"] == 3
    assert stats["indexed"] == 1
    assert stats["dead_lettered"] == 2  # corrupt pdf + missing-doc delete
    assert stats["malformed"] == 0
    assert stats["rejected"] == 0


def test_given_sqs_queue_when_retries_exhausted_then_message_lands_in_dlq(tmp_path):
    """The SQS leg of the DLQ failure drill: a message that exhausts its retry
    budget via visibility-timeout rejects must end up in the dead-letter queue,
    not silently dropped."""
    import boto3
    from moto import mock_aws

    from backend.queue import SQSQueue

    with mock_aws():
        client = boto3.client("sqs", region_name="us-east-1")
        source_url = client.create_queue(QueueName="ingest")["QueueUrl"]
        dlq_url = client.create_queue(QueueName="ingest-dlq")["QueueUrl"]
        queue = SQSQueue(queue_url=source_url, region="us-east-1", dlq_url=dlq_url)
        blob = LocalDocumentBlobStore(tmp_path / "blobs")
        store = SQLiteVectorStore(db_path=str(tmp_path / "test.db"), storage_dir=str(tmp_path / "docs"), blob_store=blob)
        worker = IngestionWorker(
            queue=queue,
            vector_store=store,
            blob_store=blob,
            embedding_engine_factory=_FailingEmbedder,
            max_retries=3,
            backoff_base_seconds=0.0,
        )
        blob.put("global/labor.txt", LABOR_TEXT.encode(), "text/plain")
        queue.enqueue(_job_message("labor.txt", "global/labor.txt"))

        outcomes = []
        for _ in range(5):
            message = queue.dequeue()
            if message is None:
                break
            outcomes.append(worker.process_message(message))
        assert outcomes == [REJECTED, REJECTED, DEAD_LETTERED]

        # Source queue is empty; the message is in the DLQ, not dropped.
        assert queue.dequeue() is None
        dlq_message = client.receive_message(QueueUrl=dlq_url).get("Messages", [None])[0]
        assert dlq_message is not None
        assert '"job_id": "job_1"' in dlq_message["Body"]
        assert store.get_all_documents() == []  # no half-state on the SQS path either
