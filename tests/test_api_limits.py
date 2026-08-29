"""Tests for Phase 4.4 resource limits: bounded input caps and rate limiting.

Milestone 4.4 exit criterion is the abuse drill: a huge top_k and a giant
upload must answer 413 (never consume unbounded resources), and a burst on a
sensitive endpoint must answer 429. These are exercised through the real
TestClient, swapping module settings/limiter for small, deterministic limits.
"""

import pytest
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.app import app, SearchRequest
from backend.rate_limit import SlidingWindowRateLimiter
from config import Settings


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _StubRouter:
    """Deterministic stand-in so chat rate-limit assertions don't depend on
    retrieval/LLM internals (mirrors tests/test_api_audit.py)."""

    def run_query(self, **kwargs):
        return {"answer": "ok", "sources": [], "claim_dossier": None, "engine": "simulated", "pipeline_logs": []}


# --- bounded input caps (unit) -------------------------------------------------


def test_given_query_over_maximum_length_when_validated_then_request_is_rejected():
    settings = Settings.from_env({"MAX_QUERY_CHARS": "10"})
    with pytest.raises(ValueError):
        SearchRequest(query="x" * 11, mode="naive", top_k=4).validate_limits(settings)


def test_given_top_k_over_configured_limit_when_validated_then_request_is_rejected():
    settings = Settings.from_env({"MAX_TOP_K": "4"})
    with pytest.raises(ValueError):
        SearchRequest(query="valid", mode="naive", top_k=5).validate_limits(settings)


# --- abuse drill: oversized inputs answer 413 ----------------------------------


def test_given_giant_upload_in_sync_mode_then_413(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "settings", Settings.from_env({"MAX_UPLOAD_BYTES": "1024"}))
    client = TestClient(app)
    big = b"x" * 4096  # 4 KB over a 1 KB cap
    response = client.post("/api/upload", files={"file": ("big.txt", big, "text/plain")})
    assert response.status_code == 413
    assert "MB" not in response.json()["detail"] or "maximum upload size" in response.json()["detail"]


def test_given_giant_claim_upload_then_413(monkeypatch):
    monkeypatch.setattr(app_module, "settings", Settings.from_env({"MAX_UPLOAD_BYTES": "1024"}))
    client = TestClient(app)
    response = client.post(
        "/api/upload-claim-file",
        data={"claim_id": "#2026-1"},
        files={"file": ("big.txt", b"x" * 4096, "text/plain")},
    )
    assert response.status_code == 413


def test_given_giant_upload_in_async_mode_then_413_before_enqueue(monkeypatch):
    monkeypatch.setattr(
        app_module, "settings", Settings.from_env({"MAX_UPLOAD_BYTES": "1024", "INGESTION_MODE": "async"})
    )
    client = TestClient(app)
    response = client.post("/api/upload", files={"file": ("big.txt", b"x" * 4096, "text/plain")})
    assert response.status_code == 413
    assert response.json()["detail"] != 202  # never staged/enqueued


def test_given_huge_top_k_on_eval_search_then_413(monkeypatch):
    monkeypatch.setattr(app_module, "settings", Settings.from_env({"MAX_TOP_K": "5"}))
    client = TestClient(app)
    # validate_limits runs before any embedding, so this returns without ML work.
    response = client.post("/api/eval/search", json={"query": "labor", "mode": "naive", "top_k": 100})
    assert response.status_code == 413


def test_given_oversized_query_on_eval_search_then_413(monkeypatch):
    monkeypatch.setattr(app_module, "settings", Settings.from_env({"MAX_QUERY_CHARS": "10"}))
    client = TestClient(app)
    response = client.post("/api/eval/search", json={"query": "x" * 100, "mode": "naive", "top_k": 4})
    assert response.status_code == 413


def test_given_normal_top_k_then_eval_search_not_accidentally_limited(monkeypatch):
    # The cap must be a ceiling, not a floor: an in-limit top_k reaches the
    # handler and answers 400 (unknown mode) rather than 413.
    monkeypatch.setattr(app_module, "settings", Settings.from_env({"MAX_TOP_K": "5"}))
    client = TestClient(app)
    response = client.post("/api/eval/search", json={"query": "labor", "mode": "naive", "top_k": 3})
    assert response.status_code != 413


# --- abuse drill: burst answers 429 --------------------------------------------


def _install_limiter(monkeypatch, max_requests, clock):
    monkeypatch.setattr(
        app_module,
        "_rate_limiter",
        SlidingWindowRateLimiter(max_requests=max_requests, window_seconds=60.0, clock=clock),
    )


def test_given_burst_on_delete_then_over_limit_answers_429(monkeypatch):
    clock = _Clock()
    _install_limiter(monkeypatch, max_requests=2, clock=clock)
    client = TestClient(app)
    # The first two (even a 404 on a missing file) consume the allowance; the
    # third must answer 429 without doing any work.
    assert client.post("/api/delete", json={"filename": "ghost.txt"}).status_code == 404
    assert client.post("/api/delete", json={"filename": "ghost.txt"}).status_code == 404
    third = client.post("/api/delete", json={"filename": "ghost.txt"})
    assert third.status_code == 429
    assert third.headers.get("Retry-After")


def test_given_burst_on_chat_then_over_limit_answers_429(monkeypatch):
    clock = _Clock()
    _install_limiter(monkeypatch, max_requests=3, clock=clock)
    monkeypatch.setattr(app_module, "agentic_router", _StubRouter())
    client = TestClient(app)
    payload = {"query": "labor", "engine": "simulated"}
    assert client.post("/api/chat", json=payload).status_code == 200
    assert client.post("/api/chat", json=payload).status_code == 200
    assert client.post("/api/chat", json=payload).status_code == 200
    assert client.post("/api/chat", json=payload).status_code == 429


def test_given_burst_then_allowance_recovers_after_window(monkeypatch):
    clock = _Clock()
    _install_limiter(monkeypatch, max_requests=1, clock=clock)
    client = TestClient(app)
    assert client.post("/api/delete", json={"filename": "ghost.txt"}).status_code == 404
    assert client.post("/api/delete", json={"filename": "ghost.txt"}).status_code == 429
    clock.advance(60.0)  # window elapses -> allowance restored
    assert client.post("/api/delete", json={"filename": "ghost.txt"}).status_code == 404


def test_given_burst_with_different_principals_then_limits_are_isolated(monkeypatch):
    from backend.authn import ChainAuthenticator, DevelopmentAuthenticator
    from backend.tenant_context import PrincipalContext

    class _Fixed(DevelopmentAuthenticator):
        def __init__(self, subject: str) -> None:
            super().__init__("development")
            self._subject = subject

        def authenticate(self, request):
            return PrincipalContext(
                subject=self._subject, tenant_id="tenant-a", roles=frozenset({"admin"})
            )

    clock = _Clock()
    _install_limiter(monkeypatch, max_requests=1, clock=clock)
    monkeypatch.setattr(
        app_module, "_authenticator", ChainAuthenticator([_Fixed("user-a")])
    )
    client = TestClient(app)
    assert client.post("/api/delete", json={"filename": "ghost.txt"}).status_code == 404
    assert client.post("/api/delete", json={"filename": "ghost.txt"}).status_code == 429
    # A different authenticated principal has its own allowance budget.
    monkeypatch.setattr(
        app_module, "_authenticator", ChainAuthenticator([_Fixed("user-b")])
    )
    assert client.post("/api/delete", json={"filename": "ghost.txt"}).status_code == 404
