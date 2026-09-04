from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import threading

logger = logging.getLogger(__name__)


def _load_engine(device: str = "auto"):
    """Load the cross-encoder lazily so imports remain torch-free.

    ``device`` is passed through untouched -- resolving "auto" needs torch, and
    that import must stay inside the lazy path.
    """
    from backend.rag_engine import RerankingEngine

    return RerankingEngine(device=device)


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

    def __init__(self, engine=None, max_concurrency: int = 2, device: str = "auto"):
        self._engine = engine
        self.max_concurrency = max_concurrency
        self.device = device
        self._semaphore = threading.Semaphore(max_concurrency)

    def _engine_or_load(self):
        if self._engine is None:
            self._engine = _load_engine(self.device)
        return self._engine

    def rerank(self, query: str, passages: list[dict], top_k: int = 4) -> list[dict]:
        if not passages:
            return []
        with self._semaphore:
            return list(self._engine_or_load().rerank(query, passages, top_k=top_k))


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

    def rerank(self, query: str, passages: list[dict], top_k: int = 4) -> list[dict]:
        try:
            return self._primary.rerank(query, passages, top_k=top_k)
        except Exception:  # noqa: BLE001 - availability fallback is intentional
            logger.warning("Remote reranker failed; using local fallback", exc_info=True)
            return self._fallback.rerank(query, passages, top_k=top_k)
