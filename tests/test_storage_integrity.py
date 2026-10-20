"""Storage-integrity regression tests: vector-cache race, write-transaction
scope, scope-conflict message leakage, and async upload dedupe.

Deliberately torch-free, like test_rag_engine.py: a deterministic fake
embedder stands in for EmbeddingEngine so nothing here downloads a model.
"""
import sqlite3

import numpy as np
import pytest

from backend.ingestion import IngestionService, IngestionStatus
from backend.job_store import SqliteJobStore
from backend.rag_engine import SQLiteVectorStore
from tests.test_rag_engine import FakeEmbeddingEngine, GLASS_TEXT, LABOR_TEXT

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return SQLiteVectorStore(
        db_path=str(tmp_path / "test.db"),
        storage_dir=str(tmp_path / "stored_documents"),
    )


@pytest.fixture
def embedder():
    return FakeEmbeddingEngine()


# ---------------------------------------------------------------------------
# M2 -- vector-cache race between a build and a concurrent invalidation.
# ---------------------------------------------------------------------------

class TestVectorCacheRace:
    @staticmethod
    def _racing_vstack(store, original_vstack):
        def racing(*args, **kwargs):
            # Simulates an add_document/delete_document landing on another
            # thread while this build is still reading -- right after rows
            # are fetched, before the matrix is installed.
            store._invalidate_vector_cache()
            return original_vstack(*args, **kwargs)
        return racing

    def test_build_discards_stale_result_when_invalidated_mid_build(self, store, embedder, monkeypatch):
        store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
        store._vector_cache = None

        monkeypatch.setattr(np, "vstack", self._racing_vstack(store, np.vstack))
        built = store._build_vector_cache()

        # The in-flight search still gets a usable matrix...
        assert built["matrix"].shape[0] > 0
        # ...but the race means it must not be installed as the shared cache.
        assert store._vector_cache is None

    def test_search_similarity_still_returns_results_during_the_race(self, store, embedder, monkeypatch):
        store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
        store._vector_cache = None

        monkeypatch.setattr(np, "vstack", self._racing_vstack(store, np.vstack))
        q = "labor rate"
        results = store.search_similarity(embedder.embed_query(q), q, top_k=4)

        assert results
        assert store._vector_cache is None

    def test_normal_build_with_no_race_is_installed(self, store, embedder):
        store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
        store._vector_cache = None

        built = store._build_vector_cache()

        assert store._vector_cache is built


# ---------------------------------------------------------------------------
# M6 -- embedding must not run inside the write transaction.
# ---------------------------------------------------------------------------

class _ConcurrentWriterEmbedder:
    """Stands in for embed_chunks taking a long time: opens a SECOND
    connection with no busy wait and performs its own write. If the caller
    still held the write transaction open while embedding, this raises
    'database is locked' immediately instead of waiting out a busy timeout.
    """

    def __init__(self, db_path):
        self.db_path = db_path
        self.inner = FakeEmbeddingEngine()

    def embed_chunks(self, chunks):
        if chunks:
            conn = sqlite3.connect(self.db_path, timeout=0)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("ROLLBACK")
            finally:
                conn.close()
        return self.inner.embed_chunks(chunks)


def test_add_document_does_not_hold_the_write_lock_while_embedding(store):
    embedder = _ConcurrentWriterEmbedder(store.db_path)
    store.add_document("labor.txt", "txt", 100, LABOR_TEXT, embedder)
    docs = store.get_all_documents()
    assert [d["filename"] for d in docs] == ["labor.txt"]


# ---------------------------------------------------------------------------
# M5 -- a filename reused across scopes (claim_id, filename identity) must
# coexist, not collide.
# ---------------------------------------------------------------------------

def test_same_filename_in_different_claims_coexists(store, embedder):
    store.add_document("report.pdf", "pdf", 100, LABOR_TEXT, embedder, claim_id="#2026-55912")
    store.add_document("report.pdf", "pdf", 100, GLASS_TEXT, embedder, claim_id="#2026-99999")

    assert store.get_document_content("report.pdf", "#2026-55912") == LABOR_TEXT
    assert store.get_document_content("report.pdf", "#2026-99999") == GLASS_TEXT


# ---------------------------------------------------------------------------
# M3 -- async upload dedupe must not return a finished/failed job.
# ---------------------------------------------------------------------------

class _RecordingQueue:
    def __init__(self):
        self.messages = []

    def enqueue(self, message):
        self.messages.append(message)


def _service(job_store=None):
    return IngestionService(queue=_RecordingQueue(), job_store=job_store)


class TestSubmitDedupeInMemory:
    def test_reupload_after_completed_job_creates_a_new_job(self):
        service = _service()
        content = b"same bytes"
        first = service.submit(tenant_id="t1", filename="a.pdf", content=content)
        service.update(first.job_id, "t1", status=IngestionStatus.indexed, progress=100)

        second = service.submit(tenant_id="t1", filename="a.pdf", content=content)

        assert second.job_id != first.job_id
        assert second.status is IngestionStatus.queued

    def test_reupload_after_failed_job_creates_a_new_job(self):
        service = _service()
        content = b"same bytes"
        first = service.submit(tenant_id="t1", filename="a.pdf", content=content)
        service.update(first.job_id, "t1", status=IngestionStatus.failed, error_code="INDEXING_FAILED")

        second = service.submit(tenant_id="t1", filename="a.pdf", content=content)

        assert second.job_id != first.job_id
        assert second.status is IngestionStatus.queued

    def test_duplicate_while_first_job_still_queued_returns_the_first(self):
        service = _service()
        content = b"same bytes"
        first = service.submit(tenant_id="t1", filename="a.pdf", content=content)

        second = service.submit(tenant_id="t1", filename="a.pdf", content=content)

        assert second.job_id == first.job_id

    def test_explicit_idempotency_key_returns_same_job_even_after_completion(self):
        service = _service()
        content = b"same bytes"
        first = service.submit(
            tenant_id="t1", filename="a.pdf", content=content, idempotency_key="k1"
        )
        service.update(first.job_id, "t1", status=IngestionStatus.indexed, progress=100)

        second = service.submit(
            tenant_id="t1", filename="a.pdf", content=content, idempotency_key="k1"
        )

        assert second.job_id == first.job_id


class TestSubmitDedupeSqliteBacked:
    def test_reupload_after_completed_job_creates_a_new_job(self, tmp_path):
        job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
        service = _service(job_store=job_store)
        content = b"same bytes"
        first = service.submit(tenant_id="t1", filename="a.pdf", content=content)
        service.update(first.job_id, "t1", status=IngestionStatus.indexed, progress=100)

        second = service.submit(tenant_id="t1", filename="a.pdf", content=content)

        assert second.job_id != first.job_id
        assert second.status is IngestionStatus.queued

    def test_reupload_after_failed_job_creates_a_new_job(self, tmp_path):
        job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
        service = _service(job_store=job_store)
        content = b"same bytes"
        first = service.submit(tenant_id="t1", filename="a.pdf", content=content)
        service.update(first.job_id, "t1", status=IngestionStatus.failed, error_code="INDEXING_FAILED")

        second = service.submit(tenant_id="t1", filename="a.pdf", content=content)

        assert second.job_id != first.job_id
        assert second.status is IngestionStatus.queued

    def test_duplicate_while_first_job_still_queued_returns_the_first(self, tmp_path):
        job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
        service = _service(job_store=job_store)
        content = b"same bytes"
        first = service.submit(tenant_id="t1", filename="a.pdf", content=content)

        second = service.submit(tenant_id="t1", filename="a.pdf", content=content)

        assert second.job_id == first.job_id

    def test_explicit_idempotency_key_returns_same_job_even_after_completion(self, tmp_path):
        job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
        service = _service(job_store=job_store)
        content = b"same bytes"
        first = service.submit(
            tenant_id="t1", filename="a.pdf", content=content, idempotency_key="k1"
        )
        service.update(first.job_id, "t1", status=IngestionStatus.indexed, progress=100)

        second = service.submit(
            tenant_id="t1", filename="a.pdf", content=content, idempotency_key="k1"
        )

        assert second.job_id == first.job_id

    def test_find_by_dedupe_key_returns_the_newest_job(self, tmp_path):
        job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
        service = _service(job_store=job_store)
        content = b"same bytes"
        first = service.submit(tenant_id="t1", filename="a.pdf", content=content)
        service.update(first.job_id, "t1", status=IngestionStatus.indexed, progress=100)
        second = service.submit(tenant_id="t1", filename="a.pdf", content=content)

        found = job_store.find_by_dedupe_key("t1", first.idempotency_key)

        assert found.job_id == second.job_id
