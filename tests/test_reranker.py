import pytest

from backend.reranker import FallbackReranker, LocalReranker, RemoteReranker


class StubEngine:
    def rerank(self, query, passages, top_k=4):
        return [dict(p, rerank_score=1.0 / (i + 1)) for i, p in enumerate(passages[:top_k])]


def test_local_reranker_delegates_and_honors_top_k():
    out = LocalReranker(engine=StubEngine()).rerank(
        "q", [{"content": "a"}, {"content": "b"}, {"content": "c"}], top_k=2
    )
    assert len(out) == 2
    assert all("rerank_score" in passage for passage in out)


def test_local_reranker_without_explicit_engine_lazy_loads(monkeypatch):
    import backend.reranker as module

    called = {}

    class Stub:
        def __init__(self):
            called["loaded"] = True

        def rerank(self, query, passages, top_k=4):
            return passages[:top_k]

    monkeypatch.setattr(module, "_load_engine", lambda: Stub())
    assert LocalReranker().rerank("q", [{"content": "x"}], top_k=1) == [{"content": "x"}]
    assert called["loaded"] is True


class FakeResponse:
    def __init__(self, result, status=200):
        self.result = result
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"status {self.status}")

    def json(self):
        return self.result


class FakeHTTP:
    def __init__(self, result=None, status=200):
        self.posts = []
        self.result = result or {"results": [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.6}]}
        self.status = status

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse(self.result, self.status)


def test_remote_reranker_sends_batched_pool_and_maps_results():
    http = FakeHTTP()
    out = RemoteReranker("http://reranker", timeout=7.0, http=http).rerank(
        "q", [{"content": "a"}, {"content": "b"}, {"content": "c"}], top_k=2
    )
    assert http.posts[0] == {
        "url": "http://reranker/rerank",
        "json": {"query": "q", "passages": ["a", "b", "c"], "top_k": 2},
        "headers": {"Content-Type": "application/json"},
        "timeout": 7.0,
    }
    assert [p["content"] for p in out] == ["b", "a"]
    assert [p["rerank_score"] for p in out] == [0.9, 0.6]


def test_remote_reranker_bearer_only_when_configured():
    http = FakeHTTP({"results": [{"index": 0, "score": 0.5}]})
    RemoteReranker("http://reranker", api_key="secret", http=http).rerank("q", [{"content": "a"}], 1)
    assert http.posts[0]["headers"]["Authorization"] == "Bearer secret"


def test_remote_reranker_rejects_malformed_results():
    with pytest.raises(ValueError):
        RemoteReranker("http://reranker", http=FakeHTTP({"results": [{"index": 9, "score": 1}]})).rerank(
            "q", [{"content": "a"}], 1
        )


def test_fallback_reranker_falls_back_to_local_on_failure():
    class Boom:
        def rerank(self, query, passages, top_k=4):
            raise RuntimeError("down")

    out = FallbackReranker(Boom(), LocalReranker(engine=StubEngine())).rerank(
        "q", [{"content": "a"}, {"content": "b"}], top_k=1
    )
    assert len(out) == 1
