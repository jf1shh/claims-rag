from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from concurrent.futures import Future
import copy
import logging
import math
import threading
import time

logger = logging.getLogger(__name__)


def _load_engine(device: str = "auto", model_name="cross-encoder/ms-marco-MiniLM-L-6-v2"):
    """Load the cross-encoder lazily so imports remain torch-free.

    ``device`` is passed through untouched -- resolving "auto" needs torch, and
    that import must stay inside the lazy path.
    """
    from backend.rag_engine import RerankingEngine

    return RerankingEngine(device=device, model_name=model_name)


class RerankerOverloadedError(RuntimeError):
    """Raised when the bounded local reranker queue cannot accept work."""


class _BatchWorkItem:
    def __init__(self, query: str, passages: list[dict], top_k: int):
        self.query = query
        self.passages = tuple(copy.deepcopy(passage) for passage in passages)
        self.top_k = top_k
        self.future: Future[list[dict]] = Future()


class _RerankBatchCoordinator:
    """Coalesces independent local rerank requests into one model call."""

    def __init__(
        self,
        engine,
        *,
        max_wait_ms: int,
        max_requests: int,
        max_pairs: int,
        inference_batch_size: int,
        max_pending: int,
    ):
        self._engine = engine
        self._max_wait_seconds = max_wait_ms / 1000.0
        self._max_requests = max_requests
        self._max_pairs = max_pairs
        self._inference_batch_size = inference_batch_size
        self._max_pending = max_pending
        self._condition = threading.Condition()
        self._pending = deque()
        self._closed = False
        self._stats = {
            "submitted_requests": 0,
            "dispatched_batches": 0,
            "total_pairs": 0,
            "requests_coalesced": 0,
            "overload_rejections": 0,
            "batch_failures": 0,
            "max_pending_depth": 0,
        }
        self._dispatcher = threading.Thread(target=self._dispatch, name="reranker-batcher", daemon=True)
        self._dispatcher.start()

    def submit(self, query: str, passages: list[dict], top_k: int) -> list[dict]:
        work = _BatchWorkItem(query, passages, top_k)
        with self._condition:
            if self._closed:
                raise RuntimeError("local reranker is closed")
            if len(self._pending) >= self._max_pending:
                self._stats["overload_rejections"] += 1
                raise RerankerOverloadedError("local reranker queue is full")
            self._pending.append(work)
            self._stats["submitted_requests"] += 1
            self._stats["max_pending_depth"] = max(self._stats["max_pending_depth"], len(self._pending))
            self._condition.notify()
        return work.future.result()

    def _dispatch(self):
        while True:
            batch = self._next_batch()
            if batch is None:
                return
            self._process(batch)

    def _next_batch(self):
        with self._condition:
            while not self._pending and not self._closed:
                self._condition.wait()
            if not self._pending:
                return None

            batch = [self._pending.popleft()]
            pair_count = len(batch[0].passages)
            deadline = time.monotonic() + self._max_wait_seconds
            while len(batch) < self._max_requests and pair_count < self._max_pairs:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if not self._pending:
                    self._condition.wait(remaining)
                    continue
                candidate = self._pending[0]
                candidate_pairs = len(candidate.passages)
                if pair_count + candidate_pairs > self._max_pairs:
                    break
                batch.append(self._pending.popleft())
                pair_count += candidate_pairs
            return batch

    def _process(self, batch):
        flat_pairs = [
            (work.query, passage["content"])
            for work in batch
            for passage in work.passages
        ]
        try:
            scores = list(self._engine.score_pairs(flat_pairs, inference_batch_size=self._inference_batch_size))
            if len(scores) != len(flat_pairs):
                raise ValueError(
                    f"reranker returned an invalid score count: expected {len(flat_pairs)}, got {len(scores)}"
                )
            results = []
            offset = 0
            for work in batch:
                end = offset + len(work.passages)
                scored = []
                for passage, score in zip(work.passages, scores[offset:end], strict=True):
                    scored_passage = dict(passage)
                    scored_passage["rerank_score"] = float(1.0 / (1.0 + math.exp(-float(score))))
                    scored.append(scored_passage)
                scored.sort(key=lambda item: item["rerank_score"], reverse=True)
                results.append(scored[:work.top_k])
                offset = end
        except Exception as exc:
            with self._condition:
                self._stats["batch_failures"] += 1
            for work in batch:
                work.future.set_exception(exc)
            return

        with self._condition:
            self._stats["dispatched_batches"] += 1
            self._stats["total_pairs"] += len(flat_pairs)
            if len(batch) > 1:
                self._stats["requests_coalesced"] += len(batch)
        for work, result in zip(batch, results, strict=True):
            work.future.set_result(result)

    def stats(self) -> dict[str, int]:
        with self._condition:
            return dict(self._stats)

    def close(self):
        with self._condition:
            if self._closed:
                dispatcher = self._dispatcher
            else:
                self._closed = True
                while self._pending:
                    work = self._pending.popleft()
                    work.future.set_exception(RuntimeError("local reranker is closed"))
                self._condition.notify_all()
                dispatcher = self._dispatcher
        if threading.current_thread() is not dispatcher:
            dispatcher.join()


class Reranker(ABC):
    """Re-rank passages by relevance and return at most ``top_k`` items."""

    @abstractmethod
    def rerank(self, query: str, passages: list[dict], top_k: int = 4) -> list[dict]:
        raise NotImplementedError


class LocalReranker(Reranker):
    """In-process cross-encoder, retained as default and fallback.

    Phase 6.1 profiling (py-spy under concurrent load, then controlled A/B
    tests -- see docs/enterprise-migration.md) found that neither torch
    intra-op thread count nor separate worker processes change concurrent
    p95 at all *on CPU*: latency scales with the number of *simultaneous*
    forward passes, not with how each one is threaded. A bounded semaphore
    trades unlimited concurrency for queuing, which measurably lowers p95.

    ``device`` moves those forward passes onto a local GPU when one is
    present, which is the actual fix for that bottleneck -- the semaphore
    stays because it still bounds work in flight, and it is what CPU-only
    deployments (CI included) keep falling back to.
    """

    def __init__(
        self,
        engine=None,
        max_concurrency: int = 2,
        device: str = "auto",
        model_name="cross-encoder/ms-marco-MiniLM-L-6-v2",
        batching_enabled: bool = True,
        batch_max_wait_ms: int = 5,
        batch_max_requests: int = 16,
        batch_max_pairs: int = 256,
        inference_batch_size: int = 16,
        batch_max_pending: int = 1024,
    ):
        self.model_name = model_name
        self._load_lock = threading.Lock()
        self._coordinator_lock = threading.Lock()
        self._engine = engine
        self._closed = False
        self.max_concurrency = max_concurrency
        self.device = device
        self._semaphore = threading.Semaphore(max_concurrency)
        self.batching_enabled = batching_enabled
        self.batch_max_wait_ms = batch_max_wait_ms
        self.batch_max_requests = batch_max_requests
        self.batch_max_pairs = batch_max_pairs
        self.inference_batch_size = inference_batch_size
        self.batch_max_pending = batch_max_pending
        self._coordinator = None

    def _engine_or_load(self):
        with self._load_lock:
            if self._engine is None:
                self._engine = _load_engine(self.device, self.model_name)
        return self._engine

    def _coordinator_or_none(self, engine):
        if not self.batching_enabled or not callable(getattr(engine, "score_pairs", None)):
            return None
        with self._coordinator_lock:
            if self._closed:
                raise RuntimeError("local reranker is closed")
            if self._coordinator is None:
                self._coordinator = _RerankBatchCoordinator(
                    engine,
                    max_wait_ms=self.batch_max_wait_ms,
                    max_requests=self.batch_max_requests,
                    max_pairs=self.batch_max_pairs,
                    inference_batch_size=self.inference_batch_size,
                    max_pending=self.batch_max_pending,
                )
            return self._coordinator

    def rerank(self, query: str, passages: list[dict], top_k: int = 4) -> list[dict]:
        with self._coordinator_lock:
            if self._closed:
                raise RuntimeError("local reranker is closed")
        if not passages:
            return []
        engine = self._engine_or_load()
        coordinator = self._coordinator_or_none(engine)
        if coordinator is not None:
            return coordinator.submit(query, passages, top_k)
        with self._semaphore:
            return list(engine.rerank(query, passages, top_k=top_k))

    def stats(self) -> dict[str, int]:
        if self._coordinator is None:
            return {
                "submitted_requests": 0,
                "dispatched_batches": 0,
                "total_pairs": 0,
                "requests_coalesced": 0,
                "overload_rejections": 0,
                "batch_failures": 0,
                "max_pending_depth": 0,
            }
        return self._coordinator.stats()

    def close(self):
        with self._coordinator_lock:
            if self._closed:
                return
            self._closed = True
            coordinator = self._coordinator
        if coordinator is not None:
            coordinator.close()


class RemoteReranker(Reranker):
    """HTTP adapter for a dedicated GPU reranker service."""

    def __init__(self, endpoint: str, timeout: float = 10.0, api_key: str | None = None, http=None):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key
        self._http = http

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _client(self):
        if self._http is None:
            import requests

            self._http = requests
        return self._http

    def rerank(self, query: str, passages: list[dict], top_k: int = 4) -> list[dict]:
        if not passages:
            return []
        response = self._client().post(
            f"{self.endpoint}/rerank",
            json={"query": query, "passages": [p["content"] for p in passages], "top_k": top_k},
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results")
        if not isinstance(results, list):
            raise ValueError("reranker response must contain a results list")

        output = []
        seen = set()
        for item in results:
            if not isinstance(item, dict) or not isinstance(item.get("index"), int):
                raise ValueError("reranker result has an invalid index")
            index = item["index"]
            if index < 0 or index >= len(passages) or index in seen:
                raise ValueError("reranker result index is out of range or duplicated")
            try:
                score = float(item["score"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("reranker result has an invalid score") from exc
            passage = dict(passages[index])
            passage["rerank_score"] = score
            output.append(passage)
            seen.add(index)
            if len(output) == top_k:
                break
        return output


class FallbackReranker(Reranker):
    """Try a primary reranker and fail open to a local fallback."""

    def __init__(self, primary: Reranker, fallback: Reranker):
        self._primary = primary
        self._fallback = fallback

    def close(self):
        for reranker in (self._primary, self._fallback):
            close = getattr(reranker, "close", None)
            if callable(close):
                close()

    def stats(self) -> dict[str, int]:
        stats = getattr(self._fallback, "stats", None)
        return stats() if callable(stats) else {}

    def rerank(self, query: str, passages: list[dict], top_k: int = 4) -> list[dict]:
        try:
            return self._primary.rerank(query, passages, top_k=top_k)
        except Exception:  # noqa: BLE001 - availability fallback is intentional
            logger.warning("Remote reranker failed; using local fallback", exc_info=True)
            return self._fallback.rerank(query, passages, top_k=top_k)
