import time
from fastapi.testclient import TestClient
import backend.app as app_module
from backend.app import app


def test_time_to_first_token_is_reported_and_precedes_final(monkeypatch):
    class Router:
        def __init__(self): self.order = []
        def run_query_stream(self, **kw):
            start = time.monotonic()
            self.order.append("chunk")
            yield {"type": "chunk", "text": "a"}
            time.sleep(0.05)
            self.order.append("final")
            yield {"type": "final", "answer": "a", "sources": [], "engine": "ok",
                   "pipeline_logs": [], "ttf_ms": round((time.monotonic() - start) * 1000, 1)}
    monkeypatch.setattr(app_module, "agentic_router", Router())
    monkeypatch.setattr(app_module, "_reranker", None)
    monkeypatch.setattr(app_module, "settings", __import__("config").Settings.from_env({}))

    with TestClient(app).stream("POST", "/api/chat/stream", json={"query": "q", "engine": "lm-studio"}) as r:
        body = r.read().decode()
    assert '"ttf_ms"' in body
    # first token is streamed before the final event (the body order encodes it)
    assert body.index("data: {\"text\": \"a\"}") < body.index('"ttf_ms"')


def test_ttf_under_scripted_latency_has_sane_percentile(monkeypatch):
    # Statistical guard only: with per-token 5ms scripted latency, p95 ttf for
    # the FIRST token must be far below the full-answer time.
    #
    # Hermetic per the controller's pre-flight ruling: the brief's version of
    # this test took `monkeypatch` but never called `monkeypatch.setattr`,
    # which would have sent all 20 requests through the real agentic_router /
    # _get_embedding_engine() / real vector store -- i.e. the real ML stack on
    # every request. Stub the router the same way test 1 (and Task 5's
    # `streaming_harness` fixture) do, so no real LLM or vector-store work
    # ever happens here.
    import statistics

    class Router:
        def run_query_stream(self, **kw):
            yield {"type": "chunk", "text": "a"}
            time.sleep(0.005)
            yield {"type": "final", "answer": "a", "sources": [], "engine": "ok",
                   "pipeline_logs": [], "ttf_ms": 5.0}

    monkeypatch.setattr(app_module, "agentic_router", Router())
    monkeypatch.setattr(app_module, "_reranker", None)
    monkeypatch.setattr(app_module, "settings", __import__("config").Settings.from_env({}))

    def _measure():
        t0 = time.monotonic()
        with TestClient(app).stream("POST", "/api/chat/stream", json={"query": "q", "engine": "lm-studio"}) as r:
            r.read()
        return (time.monotonic() - t0)
    samples = [_measure() for _ in range(20)]
    # 20 quick responses total under 10s (nobody waits for a full model).
    assert statistics.median(samples) < 2.0
