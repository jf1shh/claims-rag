import json
import pytest
from fastapi.testclient import TestClient
import backend.app as app_module
from backend.app import app
from backend.audit import JsonlAuditSink


@pytest.fixture
def streaming_harness(tmp_path, monkeypatch):
    from config import Settings
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_module, "_audit_sink", sink)
    monkeypatch.setattr(app_module, "settings", Settings.from_env({"CONTEXT_MAX_CLAIM_CHUNKS": "8"}))
    client = TestClient(app)
    return client, sink


def test_chat_stream_emits_sse_and_audits_assembled_answer(streaming_harness, monkeypatch):
    client, sink = streaming_harness
    class Router:
        def run_query_stream(self, **kw):
            yield {"type": "chunk", "text": "Nevada "}
            yield {"type": "chunk", "text": "cap is $110"}
            yield {"type": "final", "answer": "Nevada cap is $110",
                   "sources": [{"filename": "labor.txt", "file_type": "txt", "content": "x", "score": 0.9}],
                   "engine": "lm-studio (agentic)", "pipeline_logs": [], "ttf_ms": 12.3}
    monkeypatch.setattr(app_module, "agentic_router", Router())
    monkeypatch.setattr(app_module, "_reranker", None)

    with client.stream("POST", "/api/chat/stream", json={"query": "cap?", "engine": "lm-studio"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = r.read().decode()
    assert 'data: {"text": "Nevada "}' in body
    assert 'data: {"text": "cap is $110"}' in body
    assert '"answer": "Nevada cap is $110"' in body
    assert "data: [DONE]" in body

    events = [json.loads(l) for l in (sink.path).read_text().splitlines() if l.strip()]
    chat = [e for e in events if e["event"] == "chat"]
    assert len(chat) == 1
    assert chat[0]["answer"] == "Nevada cap is $110"
    assert chat[0]["sources"] == ["labor.txt"]


def test_chat_stream_401_without_credential(streaming_harness, monkeypatch):
    monkeypatch.setattr(app_module, "_authenticator",
                        __import__("backend.authn", fromlist=["ChainAuthenticator"]).ChainAuthenticator(
                            [__import__("backend.authn", fromlist=["DevelopmentAuthenticator"]).DevelopmentAuthenticator("production")]))
    resp = TestClient(app).post("/api/chat/stream", json={"query": "q", "engine": "lm-studio"})
    assert resp.status_code == 401
