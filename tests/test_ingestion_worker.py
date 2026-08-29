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
from backend.ingestion_worker import (
    DEAD_LETTERED,
    INDEXED,
    MALFORMED,
    REJECTED,
    IngestionWorker,
)
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


def _job_message(filename: str, blob_key: str, claim_id=None, job_id="job_1"):
    return {
        "job_id": job_id,
        "tenant_id": "tenant-a",
        "filename": filename,
        "claim_id": claim_id,
        "blob_key": blob_key,
        "etag": "abc123",
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
