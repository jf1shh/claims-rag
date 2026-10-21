"""M12c-C: overall deadline for non-streaming /api/chat (finding #9)."""

import json

import pytest
from fastapi.testclient import TestClient

from backend.app import runtime as app_module
from backend.app import app
from backend.audit import JsonlAuditSink
from backend.llm_client import LLMDeadlineExceeded

pytestmark = pytest.mark.usefixtures("isolated_api_runtime")


@pytest.fixture
def chat_harness(tmp_path, monkeypatch):
    from config import Settings

    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_module, "_audit_sink", sink)
    monkeypatch.setattr(app_module, "settings", Settings.from_env({"CHAT_DEADLINE_SECONDS": "1"}))
    client = TestClient(app)
    return client, sink


class _TimeoutRouter:
    """Stands in for a router whose LLM call blew the overall chat deadline."""

    def run_query(self, **kwargs):
        raise LLMDeadlineExceeded("LLM call deadline already exceeded")


class _OKRouter:
    def run_query(self, **kwargs):
        return {
            "answer": "The max labor rate is $110/hr.",
            "sources": [{"filename": "labor.txt", "content": "x", "score": 1.0}],
            "claim_dossier": None,
            "engine": "simulated (agentic)",
            "pipeline_logs": [],
        }


def test_chat_deadline_exceeded_returns_504_with_generic_body(chat_harness, monkeypatch):
    client, sink = chat_harness
    monkeypatch.setattr(app_module, "agentic_router", _TimeoutRouter())

    response = client.post("/api/chat", json={"query": "What is the cap?", "engine": "lm-studio"})

    assert response.status_code == 504
    body = response.json()
    assert body == {"detail": "The answer took too long; try again."}

    events = [json.loads(line) for line in sink.path.read_text().splitlines() if line.strip()]
    chat_events = [e for e in events if e["event"] == "chat"]
    assert len(chat_events) == 1
    assert chat_events[0]["outcome"] == "timeout"
    assert "answer" not in chat_events[0]
    assert "query" not in chat_events[0]


def test_chat_deadline_releases_slot_so_next_request_succeeds(chat_harness, monkeypatch):
    client, _sink = chat_harness
    monkeypatch.setattr(app_module, "agentic_router", _TimeoutRouter())

    timed_out = client.post("/api/chat", json={"query": "What is the cap?", "engine": "lm-studio"})
    assert timed_out.status_code == 504

    monkeypatch.setattr(app_module, "agentic_router", _OKRouter())
    ok = client.post("/api/chat", json={"query": "What is the cap?", "engine": "lm-studio"})
    assert ok.status_code == 200
    assert ok.json()["answer"] == "The max labor rate is $110/hr."
