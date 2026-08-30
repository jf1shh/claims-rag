import pytest
from backend.llm_client import OpenAICompatibleClient, ChatClientError


class StreamHTTP:
    def __init__(self, lines):
        self.lines = lines
        self.posted = []
    def post(self, url, json=None, headers=None, timeout=None, stream=False, **kw):
        self.posted.append({"url": url, "json": json, "stream": stream})
        return _StreamResponse(self.lines)
    def get(self, *a, **k): raise NotImplementedError


class _StreamResponse:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200
    def raise_for_status(self): pass
    def iter_lines(self, decode_unicode=False):
        for l in self._lines:
            yield l


def _sse_lines(*chunks, done=True):
    return ["data: " + '{"choices":[{"delta":{"content": "%s"}}]}' % c for c in chunks] \
           + (["data: [DONE]"] if done else [])


def test_complete_stream_yields_token_deltas_in_order():
    http = StreamHTTP(_sse_lines("Hello", ", ", "world"))
    client = OpenAICompatibleClient(base_url="http://gw", default_model="M", http=http)
    out = list(client.complete_stream([{"role":"user","content":"q"}], model="M", temperature=0.1, max_tokens=100))
    assert out == ["Hello", ", ", "world"]
    assert http.posted[0]["json"]["stream"] is True
    assert http.posted[0]["json"]["model"] == "M"


def test_complete_stream_raises_on_http_error():
    class Bad(_StreamResponse):
        status_code = 500
        def raise_for_status(self):
            raise RuntimeError("boom")
    http = type("H", (), {
        "post": lambda self, *a, **k: Bad(_sse_lines("x")),
        "get": lambda self, *a, **k: None,
    })()
    client = OpenAICompatibleClient(base_url="http://gw", default_model="M", http=http)
    with pytest.raises(ChatClientError):
        list(client.complete_stream([{"role":"user","content":"q"}], model="M", temperature=0.1, max_tokens=10))
