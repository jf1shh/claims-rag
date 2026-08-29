"""API-level tests for Phase 3.2 async ingestion.

/ API/upload + /api/upload-claim-file must return 202 + job_id in async mode
(cheap validation 400s preserved), GET /api/jobs/{job_id} must reflect the
worker's real progress, and sync mode (the default) must keep its legacy
behavior while gaining job_id/status for the shared response contract.
"""

import hashlib

import pytest
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.app import app
from backend.blob_store import LocalDocumentBlobStore
from backend.ingestion import IngestionService
from backend.ingestion_worker import DEAD_LETTERED, INDEXED, IngestionWorker
from backend.job_store import SqliteJobStore
from backend.queue import InProcessQueue
from backend.rag_engine import SQLiteVectorStore
from config import Settings

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

    def embed_query(self, query):
        return self._vec(query)


def _worker(queue, store, blob, job_store):
    return IngestionWorker(
        queue=queue,
        vector_store=store,
        blob_store=blob,
        embedding_engine_factory=_FakeEmbedder,
        job_store=job_store,
        max_retries=3,
        backoff_base_seconds=0.0,
    )


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Rebuilds the app's ingestion globals against temp paths (the module was
    imported at collection with repo-root defaults; tests must stay hermetic).
    Returns a builder: harness(mode="sync"|"async") -> (client, store, blob,
    job_store, queue)."""

    def _build(mode="sync"):
        store = SQLiteVectorStore(db_path=str(tmp_path / "test.db"), storage_dir=str(tmp_path / "docs"))
        blob = LocalDocumentBlobStore(tmp_path / "ingest_queue")
        job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
        queue = InProcessQueue()
        service = IngestionService(queue=queue, job_store=job_store)

        monkeypatch.setattr(app_module, "vector_store", store)
        monkeypatch.setattr(app_module, "_async_blob_store", blob)
        monkeypatch.setattr(app_module, "ingestion_service", service)
        monkeypatch.setattr(app_module, "ingestion_job_store", job_store)
        monkeypatch.setattr(app_module, "_get_embedding_engine", lambda: _FakeEmbedder())
        if mode == "async":
            monkeypatch.setattr(
                app_module, "settings", Settings(ingestion_mode="async", tenant_id="tenant-a")
            )
        return TestClient(app), store, blob, job_store, queue

    return _build


def test_given_async_mode_when_upload_then_202_with_job_id_and_worker_indexes(harness):
    client, store, blob, job_store, queue = harness("async")

    response = client.post(
        "/api/upload",
        files={"file": ("labor.txt", LABOR_TEXT.encode(), "text/plain")},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["job_id"].startswith("job_")
    assert body["status"] == "queued"
    assert body["filename"] == "labor.txt"

    # The job is queryable immediately and shows queued state.
    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["status"] == "queued"

    # The worker then indexes it; the same job record reflects the real state.
    assert _worker(queue, store, blob, job_store).process_message(queue.dequeue()) == INDEXED
    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["status"] == "indexed"
    assert store.get_document_content("labor.txt") == LABOR_TEXT


def test_given_async_mode_when_claim_upload_then_job_is_claim_scoped(harness):
    client, store, blob, job_store, queue = harness("async")

    response = client.post(
        "/api/upload-claim-file",
        data={"claim_id": "claim-9"},
        files={"file": ("police_report.txt", b"police report contents", "text/plain")},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["claim_id"] == "claim-9"

    assert _worker(queue, store, blob, job_store).process_message(queue.dequeue()) == INDEXED
    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["status"] == "indexed"
    assert job["claim_id"] == "claim-9"
    assert len(store.get_claim_documents("claim-9")) == 1


def test_given_async_mode_when_corrupt_file_then_job_fails(harness):
    client, store, blob, job_store, queue = harness("async")

    response = client.post(
        "/api/upload",
        files={"file": ("broken.pdf", b"%PDF-1.4 this is not a real pdf", "application/pdf")},
    )
    assert response.status_code == 202
    body = response.json()

    assert _worker(queue, store, blob, job_store).process_message(queue.dequeue()) == DEAD_LETTERED
    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["status"] == "failed"
    assert job["error_code"] == "CORRUPT_FILE"
    assert store.get_all_documents() == []


def test_given_async_mode_when_unsupported_extension_then_400(harness):
    client, _, _, _, _ = harness("async")
    response = client.post(
        "/api/upload", files={"file": ("virus.exe", b"MZ\x90\x00", "application/octet-stream")}
    )
    assert response.status_code == 400


def test_given_async_mode_when_empty_file_then_400(harness):
    client, _, _, _, _ = harness("async")
    response = client.post("/api/upload", files={"file": ("blank.txt", b"", "text/plain")})
    assert response.status_code == 400


def test_given_sync_mode_when_upload_then_200_with_job_id_and_indexed(harness):
    client, store, blob, job_store, _ = harness("sync")
    response = client.post(
        "/api/upload", files={"file": ("labor.txt", LABOR_TEXT.encode(), "text/plain")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"].startswith("job_")
    assert body["status"] == "indexed"
    assert body["chunks_count"] >= 1
    # The legacy timing fields are still present.
    assert body["steps"]["parsing_ms"] >= 0
    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["status"] == "indexed"
    assert store.get_document_content("labor.txt") == LABOR_TEXT


def test_given_sync_mode_when_corrupt_file_then_400_preserved(harness):
    client, _, _, _, _ = harness("sync")
    response = client.post(
        "/api/upload",
        files={"file": ("broken.pdf", b"%PDF-1.4 this is not a real pdf", "application/pdf")},
    )
    assert response.status_code == 400


def test_given_missing_job_then_404(harness):
    client, _, _, _, _ = harness("async")
    assert client.get("/api/jobs/job_nope").status_code == 404
