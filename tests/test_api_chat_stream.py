import json
import pytest
from fastapi.testclient import TestClient
from backend.app import runtime as app_module
from backend.app import app
from backend.audit import JsonlAuditSink


pytestmark = pytest.mark.usefixtures("isolated_api_runtime")


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
    assert "answer" not in chat[0]
    assert "query" not in chat[0]
    assert chat[0]["sources"] == ["labor.txt"]


def test_chat_stream_401_without_credential(streaming_harness, monkeypatch):
    monkeypatch.setattr(app_module, "_authenticator",
                        __import__("backend.authn", fromlist=["ChainAuthenticator"]).ChainAuthenticator(
                            [__import__("backend.authn", fromlist=["DevelopmentAuthenticator"]).DevelopmentAuthenticator("production")]))
    resp = TestClient(app).post("/api/chat/stream", json={"query": "q", "engine": "lm-studio"})
    assert resp.status_code == 401


def test_chat_stream_403_when_claim_access_denied(streaming_harness, monkeypatch):
    # Mirrors test_api_rbac.py's equivalent /api/chat case: an adjuster
    # querying a claim they aren't assigned to must 403 on the streaming
    # endpoint too.
    from backend.authn import ChainAuthenticator, DevelopmentAuthenticator
    from backend.rbac import ClaimAccessPolicy
    from backend.tenant_context import PrincipalContext

    client, _sink = streaming_harness

    class _AdjusterIdentity(DevelopmentAuthenticator):
        def __init__(self):
            super().__init__("development")

        def authenticate(self, request):
            return PrincipalContext(
                subject="adjuster-nobody", tenant_id="tenant-a", roles=frozenset({"adjuster"})
            )

    monkeypatch.setattr(app_module, "_authenticator", ChainAuthenticator([_AdjusterIdentity()]))
    monkeypatch.setattr(app_module, "_claim_access_policy",
                        ClaimAccessPolicy({"#2026-99382": ["someone-else"]}))

    resp = client.post(
        "/api/chat/stream",
        json={"query": "q", "engine": "lm-studio", "claim_id": "#2026-99382"},
    )
    assert resp.status_code == 403


def test_chat_stream_429_when_rate_limited(streaming_harness, monkeypatch):
    # Mirrors test_api_limits.py's equivalent /api/chat case.
    from backend.rate_limit import SlidingWindowRateLimiter

    client, _sink = streaming_harness
    monkeypatch.setattr(
        app_module, "_rate_limiter", SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)
    )
    # Consume the single allowance on a cheap endpoint that also calls
    # _rate_limit_429, then confirm the stream endpoint is denied too.
    assert client.post("/api/delete", json={"filename": "ghost.txt"}).status_code == 404
    resp = client.post("/api/chat/stream", json={"query": "q", "engine": "lm-studio"})
    assert resp.status_code == 429


def test_chat_stream_pipeline_failure_before_synthesis_still_emits_final_and_audits(
    streaming_harness, monkeypatch
):
    # Whole-branch-review finding: a mid-pipeline failure (retrieval, before
    # synthesis even starts) must not propagate an uncaught exception out of
    # the SSE generator -- the client still gets a `final` event + [DONE],
    # and the audit sink still records the request (previously it left zero
    # audit trail for a request that reached retrieval).
    client, sink = streaming_harness

    class ExplodingVectorStore:
        def search_similarity(self, *a, **kw):
            raise RuntimeError("db connection lost")

        def get_claim_chunks(self, claim_id):
            raise RuntimeError("db connection lost")

    class StubEmbedder:
        def embed_query(self, q):
            return [0.0] * 8

    class StubLLMClient:
        def models(self):
            return ["m"]

        def model_for_stage(self, stage):
            return "m"

        def complete(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
            return ('{"needs_global_policies": true, "needs_claim_dossier": false, '
                     '"sub_queries": ["q"]}')

        def complete_stream(self, *a, **kw):
            raise AssertionError("must not be called -- the pipeline failed before synthesis")
            yield  # pragma: no cover -- makes this a generator function

    monkeypatch.setattr(app_module, "vector_store", ExplodingVectorStore())
    monkeypatch.setattr(app_module, "_get_embedding_engine", lambda: StubEmbedder())
    monkeypatch.setattr(app_module, "_llm_client", StubLLMClient())
    monkeypatch.setattr(app_module, "_reranker", None)

    with client.stream("POST", "/api/chat/stream", json={"query": "q", "engine": "lm-studio"}) as r:
        assert r.status_code == 200
        body = r.read().decode()
    assert "data: [DONE]" in body
    assert '"status": "error"' in body
    assert "db connection lost" not in body  # never leak the raw exception text

    events = [json.loads(l) for l in sink.path.read_text().splitlines() if l.strip()]
    chat = [e for e in events if e["event"] == "chat"]
    assert len(chat) == 1
    assert chat[0]["status"] == "error"
