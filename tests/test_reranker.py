import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.reranker import (
    FallbackReranker,
    LocalReranker,
    RemoteReranker,
    RerankerOverloadedError,
)


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

    monkeypatch.setattr(module, "_load_engine", lambda device="auto", model_name=None: Stub())
    assert LocalReranker().rerank("q", [{"content": "x"}], top_k=1) == [{"content": "x"}]
    assert called["loaded"] is True


def test_local_reranker_defaults_to_auto_device():
    assert LocalReranker(engine=StubEngine()).device == "auto"


def test_local_reranker_passes_configured_device_to_lazy_load(monkeypatch):
    """The device preference must reach the engine loader -- resolving it needs
    torch, so LocalReranker only carries the string."""
    import backend.reranker as module

    seen = {}

    class Stub:
        def rerank(self, query, passages, top_k=4):
            return passages[:top_k]

    def fake_load(device="auto", model_name=None):
        seen["device"] = device
        return Stub()

    monkeypatch.setattr(module, "_load_engine", fake_load)
    LocalReranker(device="cpu").rerank("q", [{"content": "x"}], top_k=1)
    assert seen["device"] == "cpu"


def test_local_reranker_default_max_concurrency_is_two():
    assert LocalReranker(engine=StubEngine()).max_concurrency == 2

def test_local_reranker_bounds_concurrent_engine_calls():
    """The direct fallback still caps concurrent calls when batching is off."""
    max_concurrency = 2
    lock = threading.Lock()
    state = {"current": 0, "peak": 0}

    class SlowEngine:
        def rerank(self, query, passages, top_k=4):
            with lock:
                state["current"] += 1
                state["peak"] = max(state["peak"], state["current"])
            time.sleep(0.05)
            with lock:
                state["current"] -= 1
            return passages[:top_k]

    reranker = LocalReranker(engine=SlowEngine(), max_concurrency=max_concurrency, batching_enabled=False)
    threads = [
        threading.Thread(target=reranker.rerank, args=("q", [{"content": "a"}], 1))
        for _ in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert state["peak"] <= max_concurrency
    assert state["peak"] >= 2
    reranker.close()


class BatchEngine:
    def __init__(self, score_fn=None):
        self.calls = []
        self._lock = threading.Lock()
        self.score_fn = score_fn or (lambda index, query, content: float(index))
        self.started = threading.Event()
        self.release = threading.Event()

    def score_pairs(self, pairs, inference_batch_size=16):
        with self._lock:
            self.calls.append({"pairs": list(pairs), "batch_size": inference_batch_size})
        self.started.set()
        return [self.score_fn(index, query, content) for index, (query, content) in enumerate(pairs)]


def test_local_reranker_batches_independent_requests_without_cross_wiring():
    engine = BatchEngine(score_fn=lambda index, query, content: float(len(content)))
    reranker = LocalReranker(
        engine=engine,
        batch_max_wait_ms=100,
        batch_max_requests=4,
        batch_max_pairs=8,
        inference_batch_size=16,
    )
    barrier = threading.Barrier(3)
    requests = [
        ("q-one", [{"content": "a"}, {"content": "long"}]),
        ("q-two", [{"content": "bb"}, {"content": "ccc"}]),
    ]

    def run(request):
        barrier.wait()
        return reranker.rerank(*request, top_k=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, request) for request in requests]
        barrier.wait()
        results = [future.result(timeout=2) for future in futures]

    assert len(engine.calls) == 1
    assert sorted(engine.calls[0]["pairs"]) == sorted([
        ("q-one", "a"), ("q-one", "long"), ("q-two", "bb"), ("q-two", "ccc")
    ])
    assert {result[0]["content"] for result in results} == {"long", "ccc"}
    assert reranker.stats()["requests_coalesced"] == 2
    reranker.close()


def test_local_reranker_respects_max_requests_and_max_pairs():
    engine = BatchEngine(score_fn=lambda index, query, content: float(index))
    reranker = LocalReranker(
        engine=engine,
        batch_max_wait_ms=100,
        batch_max_requests=2,
        batch_max_pairs=4,
    )
    barrier = threading.Barrier(4)
    requests = [(f"q-{index}", [{"content": f"p-{index}"}]) for index in range(3)]

    def run(request):
        barrier.wait()
        return reranker.rerank(*request, top_k=1)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(run, request) for request in requests]
        barrier.wait()
        [future.result(timeout=2) for future in futures]

    assert len(engine.calls) == 2
    assert all(len(call["pairs"]) <= 2 for call in engine.calls)
    reranker.close()


def test_local_reranker_delivers_batch_failure_to_every_waiter():
    class FailingBatchEngine:
        def score_pairs(self, pairs, inference_batch_size=16):
            raise RuntimeError("model unavailable")

    reranker = LocalReranker(engine=FailingBatchEngine(), batch_max_wait_ms=100)
    barrier = threading.Barrier(3)

    def run(index):
        barrier.wait()
        return reranker.rerank(f"q-{index}", [{"content": f"p-{index}"}], top_k=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, index) for index in range(2)]
        barrier.wait()
        for future in futures:
            with pytest.raises(RuntimeError, match="model unavailable"):
                future.result(timeout=2)

    assert reranker.stats()["batch_failures"] == 1
    reranker.close()


def test_local_reranker_rejects_malformed_score_count():
    class ShortBatchEngine:
        def score_pairs(self, pairs, inference_batch_size=16):
            return [0.5] * (len(pairs) - 1)

    reranker = LocalReranker(engine=ShortBatchEngine(), batch_max_wait_ms=100)
    with pytest.raises(ValueError, match="score count"):
        reranker.rerank("q", [{"content": "a"}, {"content": "b"}], top_k=1)
    reranker.close()


def test_local_reranker_rejects_when_pending_queue_is_full():
    class BlockingBatchEngine:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def score_pairs(self, pairs, inference_batch_size=16):
            self.started.set()
            assert self.release.wait(timeout=2)
            return [0.5] * len(pairs)

    engine = BlockingBatchEngine()
    reranker = LocalReranker(engine=engine, batch_max_wait_ms=0, batch_max_pending=1)
    first = threading.Thread(target=reranker.rerank, args=("q1", [{"content": "a"}], 1))
    first.start()
    assert engine.started.wait(timeout=2)

    results = []

    def run_second():
        try:
            results.append(reranker.rerank("q2", [{"content": "b"}], top_k=1))
        except RuntimeError as exc:
            results.append(exc)

    second = threading.Thread(target=run_second)
    second.start()
    deadline = time.monotonic() + 2
    while reranker.stats()["submitted_requests"] < 2 and time.monotonic() < deadline:
        time.sleep(0.001)

    with pytest.raises(RerankerOverloadedError):
        reranker.rerank("q3", [{"content": "c"}], top_k=1)

    engine.release.set()
    reranker.close()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive()
    assert not second.is_alive()
    assert results and isinstance(results[0], RuntimeError)


def test_local_reranker_uses_score_pairs_when_batching_is_enabled():
    engine = BatchEngine(score_fn=lambda index, query, content: float(len(content)))
    reranker = LocalReranker(
        engine=engine,
        batching_enabled=True,
        batch_max_wait_ms=0,
        inference_batch_size=7,
    )

    result = reranker.rerank("q", [{"content": "a"}, {"content": "long"}], top_k=1)

    assert [item["content"] for item in result] == ["long"]
    assert engine.calls[0]["batch_size"] == 7
    reranker.close()


def test_local_reranker_disabled_batching_uses_direct_path():
    class DirectEngine:
        def __init__(self):
            self.calls = []

        def rerank(self, query, passages, top_k=4):
            self.calls.append((query, passages, top_k))
            return [dict(passages[0], rerank_score=0.5)][:top_k]

    engine = DirectEngine()
    reranker = LocalReranker(engine=engine, batching_enabled=False)
    passages = [{"content": "a"}, {"content": "b"}]

    assert reranker.rerank("q", passages, top_k=1) == [{"content": "a", "rerank_score": 0.5}]
    assert len(engine.calls) == 1
    assert reranker.stats()["submitted_requests"] == 0
    reranker.close()


def test_local_reranker_close_racing_first_request_does_not_start_worker_after_shutdown():
    class SlowLoad:
        def score_pairs(self, pairs, inference_batch_size=16):
            return [0.0] * len(pairs)

    reranker = LocalReranker(engine=SlowLoad())
    load_started = threading.Event()
    release_load = threading.Event()
    original_engine_or_load = reranker._engine_or_load

    def blocked_engine_load():
        load_started.set()
        assert release_load.wait(timeout=2)
        return original_engine_or_load()

    reranker._engine_or_load = blocked_engine_load
    outcome = []

    def run_request():
        try:
            reranker.rerank("q", [{"content": "a"}], 1)
        except RuntimeError as exc:
            outcome.append(exc)

    request = threading.Thread(target=run_request)
    request.start()
    assert load_started.wait(timeout=2)

    reranker.close()
    release_load.set()
    request.join(timeout=2)

    assert not request.is_alive()
    assert reranker._coordinator is None
    assert outcome and "closed" in str(outcome[0])


def test_local_reranker_copies_passages_before_batching():
    engine = BatchEngine(score_fn=lambda index, query, content: float(index))
    reranker = LocalReranker(engine=engine, batch_max_wait_ms=0)
    passages = [{"content": "a", "metadata": {"owner": "one"}}]

    result = reranker.rerank("q", passages, top_k=1)
    result[0]["metadata"]["owner"] = "two"

    assert passages[0]["metadata"]["owner"] == "one"
    reranker.close()

def test_fallback_reranker_closes_local_fallback():
    class Primary:
        def rerank(self, query, passages, top_k=4):
            raise RuntimeError("down")

    fallback = LocalReranker(engine=BatchEngine())
    wrapper = FallbackReranker(primary=Primary(), fallback=fallback)

    wrapper.close()

    with pytest.raises(RuntimeError, match="closed"):
        fallback.rerank("q", [{"content": "a"}], top_k=1)
    class EquivalentEngine:
        def score_pairs(self, pairs, inference_batch_size=16):
            return [float(len(content)) for _, content in pairs]

        def rerank(self, query, passages, top_k=4):
            scored = [
                dict(passage, rerank_score=1.0 / (1.0 + pow(2.718281828, -float(len(passage["content"])))))
                for passage in passages
            ]
            scored.sort(key=lambda item: item["rerank_score"], reverse=True)
            return scored[:top_k]

    passages = [{"id": index, "content": content} for index, content in enumerate(("a", "long", "medium"))]
    original = [dict(passage) for passage in passages]
    engine = EquivalentEngine()
    direct = LocalReranker(engine=engine, batching_enabled=False).rerank("q", passages, top_k=2)
    batched_reranker = LocalReranker(engine=engine, batch_max_wait_ms=0)
    batched = batched_reranker.rerank("q", passages, top_k=2)

    assert [item["id"] for item in batched] == [item["id"] for item in direct]
    assert [item["rerank_score"] for item in batched] == pytest.approx(
        [item["rerank_score"] for item in direct], abs=1e-6
    )
    assert passages == original
    batched_reranker.close()


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
