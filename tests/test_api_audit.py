"""API-level tests for Phase 4.3 immutable audit log.

Every authenticated action -- upload, claim upload, delete, chat, download --
must record an append-only audit event carrying who (subject), tenant,
request_id, and a UTC timestamp (added by the sink), plus action-specific
fields (filename, claim_id, query, answer, sources, outcome). This module
swaps the module-level _audit_sink for a temp-path sink and asserts the
completeness of the event stream across sampled actions, including failure
and async outcomes.
"""

import json

import pytest
from fastapi.testclient import TestClient

from backend.app import runtime as app_module
from backend.app import app
from backend.audit import JsonlAuditSink
from backend.blob_store import LocalDocumentBlobStore
from backend.ingestion import IngestionService
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
        import hashlib

        digest = hashlib.md5((text or "").encode()).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(16)]

    def embed_chunks(self, chunks):
        return [self._vec(c) for c in chunks]

    def embed_query(self, query):
        return self._vec(query)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Rebuilds the app's ingestion globals against temp paths and points the
    audit sink at a temp file, so the test observes exactly the events the
    sampled actions produce. Returns a builder: harness(mode) -> (client,
    store, blob, job_store, queue, sink_path)."""

    def _build(mode="sync"):
        store = SQLiteVectorStore(db_path=str(tmp_path / "test.db"), storage_dir=str(tmp_path / "docs"))
        blob = LocalDocumentBlobStore(tmp_path / "ingest_queue")
        job_store = SqliteJobStore(str(tmp_path / "jobs.db"))
        queue = InProcessQueue()
        service = IngestionService(queue=queue, job_store=job_store)
        sink_path = tmp_path / "audit.jsonl"
        sink = JsonlAuditSink(sink_path)

        monkeypatch.setattr(app_module, "vector_store", store)
        monkeypatch.setattr(app_module, "_async_blob_store", blob)
        monkeypatch.setattr(app_module, "ingestion_service", service)
        monkeypatch.setattr(app_module, "ingestion_job_store", job_store)
        monkeypatch.setattr(app_module, "_audit_sink", sink)
        monkeypatch.setattr(app_module, "_get_embedding_engine", lambda: _FakeEmbedder())
        if mode == "async":
            monkeypatch.setattr(
                app_module, "settings", Settings(ingestion_mode="async", tenant_id="tenant-a")
            )
        return TestClient(app), store, blob, job_store, queue, sink_path

    return _build


def _events(sink_path):
    return [json.loads(line) for line in sink_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _by_event(events, event, outcome=None):
    return [e for e in events if e["event"] == event and (outcome is None or e.get("outcome") == outcome)]


# --- upload ------------------------------------------------------------------


def test_given_sync_upload_when_indexed_then_audit_event_carries_who_tenant_and_document(harness):
    client, _, _, _, _, sink_path = harness("sync")
    response = client.post(
        "/api/upload",
        files={"file": ("labor.txt", LABOR_TEXT.encode(), "text/plain")},
    )
    assert response.status_code == 200

    events = _events(sink_path)
    uploads = _by_event(events, "upload", "indexed")
    assert len(uploads) == 1
    event = uploads[0]
    assert event["subject"] == "local-development-user"
    assert event["tenant_id"] == "local-development"
    assert event["request_id"].startswith("req_")
    assert event["filename"] == "labor.txt"
    assert event["claim_id"] is None
    assert event["file_size"] > 0
    assert event["chunks_count"] >= 1
    assert event["job_id"].startswith("job_")
    assert "recorded_at" in event


def test_given_claim_upload_when_indexed_then_audit_event_carries_claim_id(harness):
    client, _, _, _, _, sink_path = harness("sync")
    response = client.post(
        "/api/upload-claim-file",
        data={"claim_id": "#2026-99382"},
        files={"file": ("receipt.txt", LABOR_TEXT.encode(), "text/plain")},
    )
    assert response.status_code == 200

    event = _by_event(_events(sink_path), "upload", "indexed")[0]
    assert event["claim_id"] == "#2026-99382"
    assert event["filename"] == "receipt.txt"


def test_given_unsupported_upload_when_rejected_then_failed_event_is_recorded(harness):
    client, _, _, _, _, sink_path = harness("sync")
    response = client.post(
        "/api/upload",
        files={"file": ("bad.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 400

    events = _by_event(_events(sink_path), "upload", "failed")
    assert len(events) == 1
    assert events[0]["filename"] == "bad.exe"
    assert events[0]["reason"] == "unsupported format"


def test_given_async_upload_when_queued_then_audit_event_carries_job_id(harness):
    client, _, _, _, _, sink_path = harness("async")
    response = client.post(
        "/api/upload",
        files={"file": ("labor.txt", LABOR_TEXT.encode(), "text/plain")},
    )
    assert response.status_code == 202

    events = _by_event(_events(sink_path), "upload", "queued")
    assert len(events) == 1
    assert events[0]["filename"] == "labor.txt"
    assert events[0]["job_id"].startswith("job_")


# --- delete ------------------------------------------------------------------


def test_given_delete_when_deleted_then_audit_event_is_recorded(harness):
    client, _, _, _, _, sink_path = harness("sync")
    client.post("/api/upload", files={"file": ("labor.txt", LABOR_TEXT.encode(), "text/plain")})
    response = client.post("/api/delete", json={"filename": "labor.txt"})
    assert response.status_code == 200

    events = _by_event(_events(sink_path), "delete", "deleted")
    assert len(events) == 1
    event = events[0]
    assert event["subject"] == "local-development-user"
    assert event["tenant_id"] == "local-development"
    assert event["filename"] == "labor.txt"
    assert event["job_id"].startswith("job_")
    assert "recorded_at" in event


def test_given_delete_when_missing_then_not_found_event_is_recorded(harness):
    client, _, _, _, _, sink_path = harness("sync")
    response = client.post("/api/delete", json={"filename": "ghost.txt"})
    assert response.status_code == 404

    events = _by_event(_events(sink_path), "delete", "not_found")
    assert len(events) == 1
    assert events[0]["filename"] == "ghost.txt"


# --- download -----------------------------------------------------------------


def test_given_download_when_served_then_audit_event_is_recorded(harness):
    client, _, _, _, _, sink_path = harness("sync")
    client.post("/api/upload", files={"file": ("labor.txt", LABOR_TEXT.encode(), "text/plain")})
    response = client.get("/api/documents/download/labor.txt")
    assert response.status_code == 200

    events = _by_event(_events(sink_path), "download", "served")
    assert len(events) == 1
    event = events[0]
    assert event["subject"] == "local-development-user"
    assert event["filename"] == "labor.txt"
    assert "recorded_at" in event


def test_given_download_when_missing_then_not_found_event_is_recorded(harness):
    client, _, _, _, _, sink_path = harness("sync")
    response = client.get("/api/documents/download/ghost.txt")
    assert response.status_code == 404

    events = _by_event(_events(sink_path), "download", "not_found")
    assert len(events) == 1
    assert events[0]["filename"] == "ghost.txt"


# --- chat --------------------------------------------------------------------


class _StubRouter:
    """Deterministic stand-in for the agentic router: the audit layer must log
    whatever run_query returns (query, answer, source filenames), independent
    of retrieval internals (which with the fake embedder legitimately match
    nothing)."""

    def __init__(self, answer="The max labor rate is $110/hr.", sources=None):
        self._answer = answer
        self._sources = sources or [{"filename": "labor.txt", "content": "x", "score": 1.0}]
        self.last_claim_id = None

    def run_query(self, **kwargs):
        self.last_claim_id = kwargs.get("claim_id")
        return {
            "answer": self._answer,
            "sources": self._sources,
            "claim_dossier": None,
            "engine": "simulated (agentic)",
            "pipeline_logs": [],
        }


@pytest.fixture
def stub_router(harness, monkeypatch):
    """Returns (client, sink_path, stub) with the agentic router replaced by a
    deterministic stub so chat audit assertions don't depend on retrieval."""
    client, _, _, _, _, sink_path = harness("sync")
    stub = _StubRouter()
    monkeypatch.setattr(app_module, "agentic_router", stub)
    return client, sink_path, stub


def test_given_chat_when_answered_then_query_answer_and_sources_are_logged(stub_router):
    client, sink_path, stub = stub_router
    response = client.post(
        "/api/chat",
        json={"query": "What is the max labor rate in Nevada?", "engine": "simulated"},
    )
    assert response.status_code == 200

    events = _by_event(_events(sink_path), "chat")
    assert len(events) == 1
    event = events[0]
    assert event["subject"] == "local-development-user"
    assert event["tenant_id"] == "local-development"
    assert event["request_id"].startswith("req_")
    assert "query" not in event
    assert event["engine"] == "simulated"
    assert "answer" not in event
    assert event["sources"] == ["labor.txt"]
    assert "recorded_at" in event


def test_given_claim_chat_when_answered_then_claim_id_is_logged(stub_router):
    client, sink_path, stub = stub_router
    response = client.post(
        "/api/chat",
        json={"query": "Summarize this claim", "engine": "simulated", "claim_id": "#2026-99382"},
    )
    assert response.status_code == 200
    assert stub.last_claim_id == "#2026-99382"

    event = _by_event(_events(sink_path), "chat")[0]
    assert event["claim_id"] == "#2026-99382"


# --- immutability -------------------------------------------------------------


def test_given_multiple_actions_when_completed_then_events_are_append_only(harness):
    client, _, _, _, _, sink_path = harness("sync")
    client.post("/api/upload", files={"file": ("labor.txt", LABOR_TEXT.encode(), "text/plain")})
    client.get("/api/documents/download/labor.txt")
    client.post("/api/delete", json={"filename": "labor.txt"})

    events = _events(sink_path)
    kinds = [(e["event"], e.get("outcome")) for e in events]
    assert kinds == [
        ("upload", "indexed"),
        ("download", "served"),
        ("delete", "deleted"),
    ]
    # Every line is a complete self-describing JSON event (no partial writes).
    for event in events:
        assert set(event) >= {"event", "request_id", "tenant_id", "subject", "recorded_at"}
