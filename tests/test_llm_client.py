import json as _json

import pytest

from backend.llm_client import ChatClient, ChatClientError, LLMDeadlineExceeded, llm_deadline_scope


def test_chat_client_is_abstract_and_contract_is_shape_safe():
    assert issubclass(ChatClient, object)
    for name in ("models", "complete"):
        assert hasattr(ChatClient, name)


def test_chat_client_error_is_an_exception():
    assert issubclass(ChatClientError, Exception)


class FakeHTTP:
    """Recording stub: fulfills the duck-typed http contract."""
    def __init__(self):
        self.posts = []
        self.gets = []
        self._post_resp = None
        self._models_resp = None
        self._health_resp = None  # Lemonade /api/v1/health; None -> 404 like a plain server

    def post(self, url, json=None, headers=None, timeout=None, **kw):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if callable(self._post_resp):
            return self._post_resp()
        return _FakeResponse(self._post_resp)

    def get(self, url, timeout=None, **kw):
        self.gets.append({"url": url, "timeout": timeout})
        if url.endswith("/api/v1/health"):
            return _FakeResponse(self._health_resp, status=200 if self._health_resp else 404)
        return _FakeResponse(self._models_resp)


class _FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload
        self.status_code = status
        self.closed = False
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")
    def json(self):
        if callable(self._payload):
            return self._payload()
        return self._payload or {}
    def iter_content(self, chunk_size=8192):
        body = self._payload() if callable(self._payload) else (self._payload or {})
        data = _json.dumps(body).encode()
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]
    def close(self):
        self.closed = True


def _completion_body(content="hi there"):
    return {"choices": [{"message": {"content": content}}]}


from backend.llm_client import OpenAICompatibleClient  # noqa: E402


def test_complete_uses_plan_timeout_for_planning_stage():
    """Planner calls must use plan_timeout (5s) even when PLANNING_MODEL is
    unset (default dev config) -- the timeout must follow the stage, not the
    resolved model name, or a hung gateway stalls planning for the synthesis
    timeout (120s). Regression for the 5.1 merge."""
    http = FakeHTTP()
    http._post_resp = _completion_body("plan ok")
    # No planning_model set -> model_for_stage returns default_model.
    client = OpenAICompatibleClient(
        base_url="http://x", default_model="M", http=http,
        timeout=120.0, plan_timeout=5.0,
    )
    client.complete([{"role": "user", "content": "q"}],
                    model=client.model_for_stage("planning"),
                    temperature=0.0, max_tokens=150, stage="planning")
    assert http.posts[0]["timeout"] == 5.0

    http2 = FakeHTTP()
    http2._post_resp = _completion_body("answer")
    client2 = OpenAICompatibleClient(
        base_url="http://x", default_model="M", http=http2,
        timeout=120.0, plan_timeout=5.0,
    )
    client2.complete([{"role": "user", "content": "q"}],
                     model=client2.model_for_stage("synthesis"),
                     temperature=0.1, max_tokens=1000)
    assert http2.posts[0]["timeout"] == 120.0


def test_complete_posts_to_chat_completions_with_stage_model():
    http = FakeHTTP()
    http._post_resp = _completion_body("plan ok")
    client = OpenAICompatibleClient(
        base_url="http://127.0.0.1:13305", default_model="M",
        planning_model="fast", synthesis_model="domain",
        http=http,
    )
    out = client.complete(
        [{"role": "user", "content": "q"}],
        model=client.model_for_stage("planning"),
        temperature=0.0, max_tokens=150,
    )
    assert out == "plan ok"
    p = http.posts[0]
    assert p["url"] == "http://127.0.0.1:13305/v1/chat/completions"
    assert p["json"]["model"] == "fast"
    assert p["json"]["temperature"] == 0.0 and p["json"]["max_tokens"] == 150
    assert p["json"]["messages"] == [{"role": "user", "content": "q"}]


def test_model_for_stage_resolves_all_stages_and_fallback():
    client = OpenAICompatibleClient(
        base_url="http://x", default_model="D",
        planning_model="P", synthesis_model="S", eval_model=None,
        http=FakeHTTP(),
    )
    assert client.model_for_stage("planning") == "P"
    assert client.model_for_stage("synthesis") == "S"
    assert client.model_for_stage("eval") == "D"   # unset stage → default
    client2 = OpenAICompatibleClient(base_url="http://x", default_model="only", http=FakeHTTP())
    assert client2.model_for_stage("synthesis") == "only"


def test_complete_sends_bearer_only_when_api_key_configured():
    http = FakeHTTP()
    http._post_resp = _completion_body("x")
    client = OpenAICompatibleClient(base_url="http://x", default_model="M",
                                    api_key="secret", http=http)
    client.complete([{"role":"user","content":"q"}], model="M", temperature=0.1, max_tokens=10)
    assert http.posts[0]["headers"]["Authorization"] == "Bearer secret"
    http2 = FakeHTTP()
    http2._post_resp = _completion_body("x")
    client2 = OpenAICompatibleClient(base_url="http://x", default_model="M", http=http2)
    client2.complete([{"role":"user","content":"q"}], model="M", temperature=0.1, max_tokens=10)
    assert "Authorization" not in http2.posts[0]["headers"]


def test_complete_raises_on_http_error_missing_content():
    http = FakeHTTP()
    http._post_resp = _FakeResponse({"choices": []}, status=200)
    client = OpenAICompatibleClient(base_url="http://x", default_model="M", http=http)
    with pytest.raises(ChatClientError):
        client.complete([{"role":"user","content":"q"}], model="M", temperature=0.1, max_tokens=10)


def test_models_returns_first_and_ttl_caches():
    http = FakeHTTP()
    http._models_resp = {"data": [{"id": "served-model"}]}
    client = OpenAICompatibleClient(base_url="http://x", default_model="M",
                                    models_ttl_seconds=600, http=http)
    assert client.models() == ["served-model"]
    assert client.models() == ["served-model"]
    assert len([g for g in http.gets if g["url"].endswith("/v1/models")]) == 1  # cached


def test_models_failure_returns_local_model_without_caching():
    http = FakeHTTP()
    http._models_resp = {"data": []}
    client = OpenAICompatibleClient(base_url="http://x", default_model="M",
                                    models_ttl_seconds=600, http=http)
    assert client.models() == ["local-model"]
    http._models_resp = {"data": [{"id": "now-up"}]}
    assert client.models() == ["now-up"]  # failure wasn't cached


def test_models_prefers_lemonade_loaded_llm_over_download_list():
    """Lemonade's /v1/models lists every downloaded model; data[0] was a non-resident
    model, which forces a swap. The health endpoint names the loaded one."""
    http = FakeHTTP()
    http._models_resp = {"data": [{"id": "downloaded-not-loaded"}, {"id": "resident"}]}
    http._health_resp = {"all_models_loaded": [
        {"model_name": "nomic-embed", "type": "embedding"},
        {"model_name": "resident", "type": "llm"},
    ]}
    client = OpenAICompatibleClient(base_url="http://x", default_model="local-model", http=http)
    assert client.models() == ["resident"]


def test_models_skips_embedding_models_on_plain_servers():
    http = FakeHTTP()
    http._models_resp = {"data": [{"id": "nomic-embed-text"}, {"id": "chat-model"}]}
    client = OpenAICompatibleClient(base_url="http://x", default_model="M", http=http)
    assert client.models() == ["chat-model"]


def test_unset_model_resolves_placeholder_to_served_model():
    """With LLM_MODEL unset, requests sent the literal "local-model" and Lemonade 404'd."""
    http = FakeHTTP()
    http._health_resp = {"all_models_loaded": [{"model_name": "resident", "type": "llm"}]}
    client = OpenAICompatibleClient(base_url="http://x", default_model="local-model", http=http)
    assert client.model_for_stage("synthesis") == "resident"
    assert client.model_for_stage("planning") == "resident"


def test_configured_model_is_never_replaced():
    http = FakeHTTP()
    http._health_resp = {"all_models_loaded": [{"model_name": "resident", "type": "llm"}]}
    client = OpenAICompatibleClient(base_url="http://x", default_model="pinned", http=http)
    assert client.model_for_stage("synthesis") == "pinned"
    assert http.gets == []


# --- deadline/timeout behavior -----------------------------------------------


def test_check_stage_uses_check_timeout():
    http = FakeHTTP()
    http._post_resp = _completion_body("ok")
    client = OpenAICompatibleClient(base_url="http://x", default_model="M", http=http,
                                    timeout=120.0, plan_timeout=5.0, check_timeout=30.0)
    client.complete([{"role": "user", "content": "q"}], model="M", temperature=0, max_tokens=10,
                    stage="provenance_check")
    client.complete([{"role": "user", "content": "q"}], model="M", temperature=0, max_tokens=10,
                    stage="conflict_check")
    assert http.posts[0]["timeout"] == 30.0
    assert http.posts[1]["timeout"] == 30.0


def test_call_timeout_capped_by_remaining_deadline(monkeypatch):
    import backend.llm_client as llm_mod
    fake_time = [0.0]
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: fake_time[0])
    http = FakeHTTP()
    http._post_resp = _completion_body("ok")
    client = OpenAICompatibleClient(base_url="http://x", default_model="M", http=http,
                                    timeout=120.0, clock=lambda: fake_time[0])
    with llm_deadline_scope(10.0):
        client.complete([{"role": "user", "content": "q"}], model="M", temperature=0, max_tokens=10)
    assert http.posts[0]["timeout"] == 10.0


def test_deadline_already_exceeded_raises_without_http_call(monkeypatch):
    import backend.llm_client as llm_mod
    fake_time = [0.0]
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: fake_time[0])
    http = FakeHTTP()
    client = OpenAICompatibleClient(base_url="http://x", default_model="M", http=http,
                                    clock=lambda: fake_time[0])
    with llm_deadline_scope(5.0):
        fake_time[0] += 10  # deadline is now in the past
        with pytest.raises(LLMDeadlineExceeded):
            client.complete([{"role": "user", "content": "q"}], model="M", temperature=0, max_tokens=10)
    assert http.posts == []


class _TrickleResponse:
    """Yields one byte per iteration while the injected clock advances past the bound."""
    def __init__(self, clock_box, advance_per_chunk=1.0):
        self.status_code = 200
        self.closed = False
        self._clock_box = clock_box
        self._advance = advance_per_chunk
    def raise_for_status(self):
        pass
    def iter_content(self, chunk_size=8192):
        while True:
            self._clock_box[0] += self._advance
            yield b"x"
    def close(self):
        self.closed = True


def test_trickle_body_raises_within_bound_and_closes_response(monkeypatch):
    import backend.llm_client as llm_mod
    fake_time = [0.0]
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: fake_time[0])
    resp = _TrickleResponse(fake_time, advance_per_chunk=1.0)
    http = FakeHTTP()
    http._post_resp = lambda: resp
    client = OpenAICompatibleClient(base_url="http://x", default_model="M", http=http,
                                    timeout=5.0, clock=lambda: fake_time[0])
    with pytest.raises(LLMDeadlineExceeded):
        client.complete([{"role": "user", "content": "q"}], model="M", temperature=0, max_tokens=10)
    assert resp.closed
    assert fake_time[0] <= 5.0 + 1.0  # raised promptly at the bound, not after a long trickle


class _BigResponse:
    def __init__(self):
        self.status_code = 200
        self.closed = False
    def raise_for_status(self):
        pass
    def iter_content(self, chunk_size=8192):
        chunk = b"x" * chunk_size
        while True:
            yield chunk
    def close(self):
        self.closed = True


def test_body_size_cap_raises_chat_client_error():
    resp = _BigResponse()
    http = FakeHTTP()
    http._post_resp = lambda: resp
    client = OpenAICompatibleClient(base_url="http://x", default_model="M", http=http, timeout=120.0)
    with pytest.raises(ChatClientError):
        client.complete([{"role": "user", "content": "q"}], model="M", temperature=0, max_tokens=10)
    assert resp.closed


def test_content_length_trickle_is_cut_off_at_the_deadline():
    # Real socket, real requests transport: a Content-Length body trickled one
    # byte at a time must hit the wall-clock bound, not block until 8192
    # bytes accumulate (iter_content's behavior on non-chunked bodies).
    import socket
    import threading
    import time

    from backend.llm_client import OpenAICompatibleClient

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    stop = threading.Event()

    def serve():
        conn, _ = srv.accept()
        try:
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n"
                         b"Content-Type: application/json\r\n\r\n")
            while not stop.is_set():
                conn.sendall(b" ")
                time.sleep(0.05)
        except OSError:
            pass
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    client = OpenAICompatibleClient(f"http://127.0.0.1:{srv.getsockname()[1]}", "m", timeout=30.0)
    started = time.monotonic()
    try:
        with llm_deadline_scope(0.5), pytest.raises(LLMDeadlineExceeded):
            client.complete([{"role": "user", "content": "q"}], model="m", temperature=0, max_tokens=1)
        assert time.monotonic() - started < 3.0
    finally:
        stop.set()
        thread.join(timeout=2)
        srv.close()
