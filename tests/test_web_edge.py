"""API-level tests for the 2026-10-20 web-edge security findings:

M1 (cross-site writes against the development admin), M4 (concurrency caps on
chat/chat-stream/upload), L7 (stream audit only on `final`), L8 (delete ignores
claim scope), L15 (sanitized filename discarded), L17 (client request id becomes
the audit id). Fixtures mirror the existing API test modules (tests/test_api_jobs.py,
tests/test_api_limits.py, tests/conftest.py's isolated_api_runtime).
"""

import json
import threading
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.app import runtime as app_module
from backend.app import app
from backend.audit import JsonlAuditSink
from backend.authn import ChainAuthenticator, DevelopmentAuthenticator
from backend.tenant_context import PrincipalContext

pytestmark = pytest.mark.usefixtures("isolated_api_runtime")


@pytest.fixture(autouse=True)
def _generous_rate_limit(monkeypatch):
    # This module fires many requests at the same default dev principal across
    # many tests; the sliding-window rate limiter is real (not stubbed by
    # isolated_api_runtime) and otherwise starts tripping 429s well before the
    # end of the file, unrelated to anything these tests are actually checking.
    from backend.rate_limit import SlidingWindowRateLimiter
    monkeypatch.setattr(app_module, "_rate_limiter", SlidingWindowRateLimiter(max_requests=10_000, window_seconds=60.0))


class _StubChatRouter:
    """Deterministic stand-in for the agentic router (mirrors tests/test_api_audit.py)."""

    def run_query(self, **kwargs):
        return {"answer": "ok", "sources": [], "engine": "simulated", "pipeline_logs": []}


# --- M1: cross-site writes against the development admin --------------------


def test_given_cross_site_origin_when_posting_upload_then_403_and_nothing_stored(monkeypatch):
    client = TestClient(app)
    response = client.post(
        "/api/upload",
        files={"file": ("policy.txt", b"policy text", "text/plain")},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-origin request refused."
    assert app_module.vector_store.get_all_documents() == []


def test_given_sec_fetch_site_cross_site_when_posting_then_403():
    client = TestClient(app)
    response = client.post(
        "/api/delete",
        json={"filename": "ghost.txt"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-origin request refused."


def test_given_same_origin_header_when_posting_then_request_passes():
    client = TestClient(app)
    # TestClient's default base URL is http://testserver -- the Host header the
    # server sees is "testserver", so this Origin matches "{scheme}://{host}".
    response = client.post(
        "/api/delete",
        json={"filename": "ghost.txt"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 404  # reached the handler; just no such doc


def test_given_listed_cors_origin_when_posting_then_request_passes():
    client = TestClient(app)
    response = client.post(
        "/api/delete",
        json={"filename": "ghost.txt"},
        headers={"Origin": "http://localhost:8000"},
    )
    assert response.status_code == 404


def test_given_foreign_origin_when_get_then_request_still_passes():
    client = TestClient(app)
    response = client.get("/api/auth/me", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200


def test_given_no_origin_or_sec_fetch_site_header_when_posting_then_request_passes():
    # curl / service clients / the existing TestClient suite send neither header.
    client = TestClient(app)
    assert client.post("/api/delete", json={"filename": "ghost.txt"}).status_code == 404


def test_given_disallowed_host_in_development_then_trusted_host_rejects():
    client = TestClient(app)
    response = client.get("/health/live", headers={"Host": "evil.example"})
    assert response.status_code == 400


def test_given_allowed_localhost_host_then_request_passes():
    client = TestClient(app)
    response = client.get("/health/live", headers={"Host": "localhost"})
    assert response.status_code == 200


# --- M4: concurrency caps on chat / chat-stream / upload ----------------------


def test_given_per_principal_limit_of_one_when_second_concurrent_chat_then_429(monkeypatch):
    monkeypatch.setattr(
        app_module, "settings",
        replace(app_module.settings, max_concurrent_per_principal=1, max_concurrent_chat=8),
    )
    ready = threading.Event()
    release = threading.Event()

    class BlockingRouter:
        def run_query(self, **kwargs):
            ready.set()
            assert release.wait(timeout=5)
            return {"answer": "ok", "sources": [], "engine": "simulated", "pipeline_logs": []}

    monkeypatch.setattr(app_module, "agentic_router", BlockingRouter())
    client = TestClient(app)
    payload = {"query": "q", "engine": "simulated"}

    results = {}
    thread = threading.Thread(target=lambda: results.setdefault("first", client.post("/api/chat", json=payload)))
    thread.start()
    try:
        assert ready.wait(timeout=5)
        second = client.post("/api/chat", json=payload)
        assert second.status_code == 429
        assert second.json()["detail"] == "Too many requests in progress."
    finally:
        release.set()
        thread.join(timeout=5)
    assert results["first"].status_code == 200

    # The slot is released after success: a further request now succeeds.
    monkeypatch.setattr(app_module, "agentic_router", _StubChatRouter())
    assert client.post("/api/chat", json=payload).status_code == 200


def test_given_global_chat_limit_of_one_when_two_principals_concurrent_then_503(monkeypatch):
    monkeypatch.setattr(
        app_module, "settings",
        replace(app_module.settings, max_concurrent_per_principal=5, max_concurrent_chat=1),
    )
    ready = threading.Event()
    release = threading.Event()

    class BlockingRouter:
        def run_query(self, **kwargs):
            ready.set()
            assert release.wait(timeout=5)
            return {"answer": "ok", "sources": [], "engine": "simulated", "pipeline_logs": []}

    class _Fixed(DevelopmentAuthenticator):
        def __init__(self, subject):
            super().__init__("development")
            self._subject = subject

        def authenticate(self, request):
            return PrincipalContext(subject=self._subject, tenant_id=app_module.settings.tenant_id, roles=frozenset({"admin"}))

    monkeypatch.setattr(app_module, "agentic_router", BlockingRouter())
    monkeypatch.setattr(app_module, "_authenticator", ChainAuthenticator([_Fixed("user-a")]))
    client = TestClient(app)
    payload = {"query": "q", "engine": "simulated"}

    thread = threading.Thread(target=lambda: client.post("/api/chat", json=payload))
    thread.start()
    try:
        assert ready.wait(timeout=5)
        monkeypatch.setattr(app_module, "_authenticator", ChainAuthenticator([_Fixed("user-b")]))
        second = client.post("/api/chat", json=payload)
        assert second.status_code == 503
        assert second.headers.get("Retry-After") == "5"
    finally:
        release.set()
        thread.join(timeout=5)


def test_given_blocking_slot_when_handler_raises_then_slot_is_released(monkeypatch):
    monkeypatch.setattr(
        app_module, "settings",
        replace(app_module.settings, max_concurrent_per_principal=1, max_concurrent_chat=8),
    )

    class ExplodingRouter:
        def run_query(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "agentic_router", ExplodingRouter())
    # raise_server_exceptions=False: the generic Exception handler's 500 is what
    # this test is checking, not the propagate-for-debugging behavior Starlette's
    # ServerErrorMiddleware otherwise re-raises into TestClient.
    client = TestClient(app, raise_server_exceptions=False)
    payload = {"query": "q", "engine": "simulated"}
    first = client.post("/api/chat", json=payload)
    assert first.status_code == 500
    # If the slot were not released in `finally`, this would 429 instead.
    second = client.post("/api/chat", json=payload)
    assert second.status_code != 429
    assert second.status_code == 500


def test_given_upload_global_limit_of_one_when_second_concurrent_upload_then_503(monkeypatch, tmp_path):
    monkeypatch.setattr(
        app_module, "settings",
        replace(app_module.settings, max_concurrent_uploads=1, max_concurrent_per_principal=5,
                tenant_id=app_module.settings.tenant_id),
    )
    ready = threading.Event()
    release = threading.Event()
    real_add_document = app_module.vector_store.add_document

    def blocking_add_document(*args, **kwargs):
        ready.set()
        assert release.wait(timeout=5)
        return real_add_document(*args, **kwargs)

    monkeypatch.setattr(app_module.vector_store, "add_document", blocking_add_document)
    client = TestClient(app)

    thread = threading.Thread(
        target=lambda: client.post("/api/upload", files={"file": ("a.txt", b"hello world", "text/plain")})
    )
    thread.start()
    try:
        assert ready.wait(timeout=5)
        second = client.post("/api/upload", files={"file": ("b.txt", b"hello again", "text/plain")})
        assert second.status_code == 503
        assert second.headers.get("Retry-After") == "5"
    finally:
        release.set()
        thread.join(timeout=5)


# --- L7: stream audit only on `final` -----------------------------------------


def test_given_stream_closed_early_then_one_aborted_audit_record_and_slot_released(monkeypatch, tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_module, "_audit_sink", sink)
    monkeypatch.setattr(
        app_module, "settings",
        replace(app_module.settings, max_concurrent_per_principal=1, max_concurrent_chat=1),
    )

    class ManyChunksRouter:
        def run_query_stream(self, **kwargs):
            for i in range(10_000):
                yield {"type": "chunk", "text": f"chunk-{i} "}

    monkeypatch.setattr(app_module, "agentic_router", ManyChunksRouter())
    client = TestClient(app)

    with client.stream("POST", "/api/chat/stream", json={"query": "q", "engine": "simulated"}) as r:
        assert r.status_code == 200
        lines = r.iter_lines()
        next(lines)  # read exactly one chunk, then close the stream early

    events = [json.loads(line) for line in sink.path.read_text().splitlines() if line.strip()]
    chat_events = [e for e in events if e["event"] == "chat"]
    assert len(chat_events) == 1
    assert chat_events[0]["outcome"] == "aborted"
    assert "answer" not in chat_events[0]

    # The concurrency slot (cap 1, shared across chat + stream) was released:
    # a further streaming request now succeeds instead of 429/503.
    monkeypatch.setattr(app_module, "agentic_router", _StubStreamRouter())
    with client.stream("POST", "/api/chat/stream", json={"query": "q2", "engine": "simulated"}) as r2:
        assert r2.status_code == 200
        r2.read()


def test_given_stream_that_yields_forever_when_wall_clock_cap_hits_then_aborted_and_slot_released(
    monkeypatch, tmp_path
):
    # CR-SEC-FIX-C.1: TestClient's in-process ASGI transport buffers the whole
    # response before returning, so it can't reproduce a stalled real-socket
    # reader directly -- but an endlessly yielding stream exercises the same
    # underlying failure (an unbounded generator holding the chat slot and the
    # upstream LLM response forever); only the wall-clock cap, not anything
    # the client does, can end it.
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_module, "_audit_sink", sink)
    monkeypatch.setattr(
        app_module, "settings",
        replace(app_module.settings, max_concurrent_per_principal=1, max_concurrent_chat=1,
                max_stream_seconds=1),
    )

    class ForeverRouter:
        def run_query_stream(self, **kwargs):
            while True:
                yield {"type": "chunk", "text": "x"}

    monkeypatch.setattr(app_module, "agentic_router", ForeverRouter())
    client = TestClient(app)

    # TestClient's in-process transport already ran the whole ASGI call (and
    # hit the 1s cap) by the time this returns -- there is nothing left to
    # read.
    with client.stream("POST", "/api/chat/stream", json={"query": "q", "engine": "simulated"}) as r:
        assert r.status_code == 200

    events = [json.loads(line) for line in sink.path.read_text().splitlines() if line.strip()]
    chat_events = [e for e in events if e["event"] == "chat"]
    assert len(chat_events) == 1
    assert chat_events[0]["outcome"] == "aborted"

    # Cap 1: the slot from the forever-stream must be free again.
    monkeypatch.setattr(app_module, "agentic_router", _StubStreamRouter())
    with client.stream("POST", "/api/chat/stream", json={"query": "q2", "engine": "simulated"}) as r2:
        assert r2.status_code == 200
        r2.read()


def test_given_stream_that_completes_before_the_wall_clock_cap_then_slot_released_once(
    monkeypatch, tmp_path
):
    # Normal-completion counterpart to the forever-stream test above: the cap
    # must not interfere with (or double-release) an ordinary, fast stream.
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_module, "_audit_sink", sink)
    monkeypatch.setattr(
        app_module, "settings",
        replace(app_module.settings, max_concurrent_per_principal=1, max_concurrent_chat=1),
    )
    monkeypatch.setattr(app_module, "agentic_router", _StubStreamRouter())
    client = TestClient(app)

    with client.stream("POST", "/api/chat/stream", json={"query": "q", "engine": "simulated"}) as r:
        assert r.status_code == 200
        r.read()

    events = [json.loads(line) for line in sink.path.read_text().splitlines() if line.strip()]
    chat_events = [e for e in events if e["event"] == "chat"]
    assert len(chat_events) == 1
    assert chat_events[0]["outcome"] == "completed"

    # Cap 1: a second request succeeds, proving the slot was released exactly once.
    with client.stream("POST", "/api/chat/stream", json={"query": "q2", "engine": "simulated"}) as r2:
        assert r2.status_code == 200
        r2.read()


def test_given_stream_body_never_started_then_slot_released_and_aborted_audit(monkeypatch, tmp_path):
    # A disconnect before the first next() means the generator never starts, so its
    # own finally never runs; the weakref.finalize fallback must still release.
    import gc

    import backend.api as api_module
    from fastapi.responses import JSONResponse

    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_module, "_audit_sink", sink)
    monkeypatch.setattr(
        app_module, "settings",
        replace(app_module.settings, max_concurrent_per_principal=1, max_concurrent_chat=1),
    )
    monkeypatch.setattr(app_module, "agentic_router", _StubStreamRouter())
    # Drop the body iterator without ever iterating it.
    monkeypatch.setattr(api_module, "_TimeBoxedStream", lambda stream, max_seconds, **kwargs: JSONResponse({}))
    client = TestClient(app)

    assert client.post("/api/chat/stream", json={"query": "q", "engine": "simulated"}).status_code == 200
    gc.collect()
    events = [json.loads(line) for line in sink.path.read_text().splitlines() if line.strip()]
    chat_events = [e for e in events if e["event"] == "chat"]
    assert len(chat_events) == 1
    assert chat_events[0]["outcome"] == "aborted"
    # Cap 1: a second request would be 429 if the first slot had leaked.
    assert client.post("/api/chat/stream", json={"query": "q2", "engine": "simulated"}).status_code == 200


class _StubStreamRouter:
    def run_query_stream(self, **kwargs):
        yield {"type": "final", "answer": "ok", "sources": [], "engine": "simulated",
               "pipeline_logs": [], "ttf_ms": 1.0}


def test_given_stream_completes_then_one_completed_audit_record(monkeypatch, tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_module, "_audit_sink", sink)
    monkeypatch.setattr(app_module, "agentic_router", _StubStreamRouter())
    client = TestClient(app)
    with client.stream("POST", "/api/chat/stream", json={"query": "q", "engine": "simulated"}) as r:
        assert r.status_code == 200
        r.read()
    events = [json.loads(line) for line in sink.path.read_text().splitlines() if line.strip()]
    chat_events = [e for e in events if e["event"] == "chat"]
    assert len(chat_events) == 1
    assert chat_events[0]["outcome"] == "completed"


# --- L8: delete ignores claim scope -------------------------------------------


def test_given_delete_of_nonexistent_claim_scoped_filename_then_404():
    response = TestClient(app).post("/api/delete", json={"filename": "no-such-claim-doc.txt"})
    assert response.status_code == 404


def test_given_delete_of_claim_scoped_document_then_authorize_document_is_consulted(monkeypatch):
    # Regression guard for the fix: delete now routes through authorize_document
    # (the same helper content/download use) instead of calling the store
    # directly. A claim-scoped document the principal can see still deletes.
    client = TestClient(app)
    upload = client.post(
        "/api/upload-claim-file",
        data={"claim_id": "#2026-1"},
        files={"file": ("report.txt", b"claim report", "text/plain")},
    )
    assert upload.status_code == 200
    response = client.post("/api/delete", json={"filename": "report.txt"})
    assert response.status_code == 200
    assert app_module.vector_store.get_document_metadata("report.txt") is None


# NOTE: every role granted the `documents:delete` permission (supervisor, admin)
# is also in rbac._ALL_CLAIMS_ROLES, which bypasses claim-level ACLs entirely
# (backend/rbac.py ClaimAccessPolicy.can_access_claim). So a principal that can
# reach this branch of authorize_document with "documents:delete" but a denied
# claim ACL cannot currently be constructed through the real role matrix -- the
# 404/403 coverage above and the RBAC suite's existing adjuster-vs-documents:delete
# 403 case are the full reachable surface for this fix.


# --- L15: sanitized filename discarded -----------------------------------------


def test_given_delete_with_path_like_filename_then_sanitized_name_is_recorded(monkeypatch, tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_module, "_audit_sink", sink)
    client = TestClient(app)
    client.post("/api/upload", files={"file": ("x.pdf", b"%PDF-1.4 x", "application/pdf")})
    # x.pdf is a bogus PDF and will fail parsing, but safe_filename("sub/x.pdf")
    # resolves to "x.pdf" before any parsing happens -- delete targets that.
    response = client.post("/api/delete", json={"filename": "sub/x.pdf"})
    events = [json.loads(line) for line in sink.path.read_text().splitlines() if line.strip()]
    delete_events = [e for e in events if e["event"] == "delete"]
    assert len(delete_events) == 1
    assert delete_events[0]["filename"] == "x.pdf"
    assert response.status_code in (200, 404)


def test_given_upload_then_response_and_audit_use_sanitized_filename(monkeypatch, tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_module, "_audit_sink", sink)
    client = TestClient(app)
    response = client.post("/api/upload", files={"file": ("labor.txt", b"Nevada labor cap is $110.", "text/plain")})
    assert response.status_code == 200
    assert response.json()["filename"] == "labor.txt"
    events = [json.loads(line) for line in sink.path.read_text().splitlines() if line.strip()]
    upload_events = [e for e in events if e["event"] == "upload"]
    assert upload_events[0]["filename"] == "labor.txt"


# --- L17: client-chosen request ID becomes the audit ID -----------------------


def test_given_client_request_id_header_then_server_id_is_returned_and_echoed_separately(monkeypatch, tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_module, "_audit_sink", sink)
    client = TestClient(app)
    response = client.post(
        "/api/delete",
        json={"filename": "ghost.txt"},
        headers={"X-Request-ID": "client-chosen-id"},
    )
    assert response.status_code == 404
    returned_id = response.headers["X-Request-ID"]
    assert returned_id != "client-chosen-id"
    assert returned_id.startswith("req_")

    events = [json.loads(line) for line in sink.path.read_text().splitlines() if line.strip()]
    delete_events = [e for e in events if e["event"] == "delete"]
    assert len(delete_events) == 1
    assert delete_events[0]["request_id"] == returned_id
    assert delete_events[0]["client_request_id"] == "client-chosen-id"


def test_given_no_client_request_id_header_then_client_request_id_is_none(monkeypatch, tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_module, "_audit_sink", sink)
    client = TestClient(app)
    client.post("/api/delete", json={"filename": "ghost.txt"})
    events = [json.loads(line) for line in sink.path.read_text().splitlines() if line.strip()]
    assert events[0]["client_request_id"] is None


def test_given_invalid_client_request_id_header_then_client_request_id_is_none(monkeypatch, tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    monkeypatch.setattr(app_module, "_audit_sink", sink)
    client = TestClient(app)
    client.post("/api/delete", json={"filename": "ghost.txt"}, headers={"X-Request-ID": "x" * 200})
    events = [json.loads(line) for line in sink.path.read_text().splitlines() if line.strip()]
    assert events[0]["client_request_id"] is None
