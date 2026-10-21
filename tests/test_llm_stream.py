import pytest
from backend.llm_client import OpenAICompatibleClient, ChatClientError, LLMDeadlineExceeded


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


class _TrickleStreamResponse(_StreamResponse):
    """Advances the injected clock past the bound on every line read."""
    def __init__(self, lines, clock_box, advance_per_line=1.0):
        super().__init__(lines)
        self._clock_box = clock_box
        self._advance = advance_per_line

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            self._clock_box[0] += self._advance
            yield line


def test_complete_stream_raises_within_bound_on_trickling_lines(monkeypatch):
    import backend.llm_client as llm_mod
    fake_time = [0.0]
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: fake_time[0])
    lines = _sse_lines("a", "b", "c", "d", "e", "f", "g", "h", "i", "j")
    resp = _TrickleStreamResponse(lines, fake_time, advance_per_line=1.0)
    http = type("H", (), {
        "post": lambda self, *a, **k: resp,
        "get": lambda self, *a, **k: None,
    })()
    client = OpenAICompatibleClient(base_url="http://gw", default_model="M", http=http,
                                    timeout=3.0, clock=lambda: fake_time[0])
    with pytest.raises(LLMDeadlineExceeded):
        list(client.complete_stream([{"role": "user", "content": "q"}], model="M",
                                     temperature=0.1, max_tokens=10))
