import pytest

from backend.llm_client import ChatClient, ChatClientError


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

    def post(self, url, json=None, headers=None, timeout=None, **kw):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if callable(self._post_resp):
            return self._post_resp()
        return _FakeResponse(self._post_resp)

    def get(self, url, timeout=None, **kw):
        self.gets.append({"url": url, "timeout": timeout})
        return _FakeResponse(self._models_resp)


class _FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload
        self.status_code = status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")
    def json(self):
        if callable(self._payload):
            return self._payload()
        return self._payload or {}


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
        base_url="http://127.0.0.1:1234", default_model="M",
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
    assert p["url"] == "http://127.0.0.1:1234/v1/chat/completions"
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
    assert len(http.gets) == 1  # cached, no second round-trip


def test_models_failure_returns_local_model_without_caching():
    http = FakeHTTP()
    http._models_resp = {"data": []}
    client = OpenAICompatibleClient(base_url="http://x", default_model="M",
                                    models_ttl_seconds=600, http=http)
    assert client.models() == ["local-model"]
    http._models_resp = {"data": [{"id": "now-up"}]}
    assert client.models() == ["now-up"]  # failure wasn't cached
