# Phase 5.2 — Dedicated Reranker Service + Pool-Bounded Rerank Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the in-process CPU cross-encoder behind a provider-neutral `Reranker` seam and add a remote GPU reranker adapter with a wider, configurable candidate pool, so rerank cost is bounded by the pool (not the corpus) while local CPU remains the default and fail-open fallback.

**Architecture:** A `Reranker` ABC exposing the existing duck-typed `.rerank(query, passages, top_k)`. `LocalReranker` wraps today's `RerankingEngine`; `RemoteReranker` POSTs the batched pool to an allowlisted `RERANK_ENDPOINT`; `FallbackReranker` tries remote then local on any failure. Both `search_similarity` backends gain an optional `candidate_pool` (default 50) parameter that replaces the hardcoded `max(15, top_k)`.

**Tech Stack:** Python 3.12, FastAPI, `requests` (injectable), pytest. Cross-encoder usage stays local-only within `LocalReranker`.

**Spec:** `docs/superpowers/specs/2026-08-29-phase5-reranker-service-design.md`

## Global Constraints

- **No real network / no GPU in CI.** Inject a duck-typed `http` stub into `RemoteReranker`; never hit a live rerank service or load a real cross-encoder in tests.
- **No ML stack at import time.** `backend/reranker.py` imports `RerankingEngine` lazily inside `LocalReranker`, not at module top.
- **Preserve the `VectorStore` interface + parity semantics.** `search_similarity` callers that don't pass `candidate_pool` get the same behavior (`top_k` still cut correctly).
- **Allowlist server-side:** `RERANK_ENDPOINT` is config; no client input shapes the URL.
- **Standing workflow:** commit + PR + CI on the self-hosted runner after the milestone; full suite + `ruff` + gates green before committing.

---

### Task 1: `Reranker` ABC + `LocalReranker`

**Files:**
- Create: `backend/reranker.py`
- Test: `tests/test_reranker.py`

**Interfaces:**
- Consumes: `RerankingEngine` (existing in `backend/rag_engine.py`) lazily.
- Produces: `class Reranker(ABC)` with `rerank(query, passages, top_k) -> list[dict]`; `class LocalReranker(Reranker)`. Used by Tasks 2–5.

- [ ] **Step 1: Write failing test** (`tests/test_reranker.py`)

```python
from backend.reranker import LocalReranker, Reranker


class StubEngine:
    def rerank(self, query, passages, top_k=4):
        scored = [dict(p, rerank_score=1.0 / (i + 1)) for i, p in enumerate(passages)]
        return scored[:top_k]


def test_local_reranker_delegates_and_honors_top_k():
    reranker = LocalReranker(engine=StubEngine())
    out = reranker.rerank("q", [
        {"content": "a"}, {"content": "b"}, {"content": "c"},
    ], top_k=2)
    assert len(out) == 2
    assert all("rerank_score" in p for p in out)


def test_local_reranker_without_explicit_engine_lazy_loads(monkeypatch):
    import backend.reranker as m
    got = {}
    class Stub:
        def __init__(self, model_name="cross-encoder/ms-marco-MiniLM-L-6-v2"):
            got["model"] = model_name
        def rerank(self, q, p, top_k=4): return p[:top_k]
    monkeypatch.setattr(m, "_load_engine", lambda: Stub("cross-encoder/ms-marco-MiniLM-L-6-v2"))
    r = LocalReranker()
    assert r.rerank("q", [{"content": "x"}], top_k=1) == [{"content": "x"}]
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_reranker.py -q`
Expected: FAIL (`backend.reranker` not importable).

- [ ] **Step 3: Implement** (`backend/reranker.py`)

```python
from __future__ import annotations

from abc import ABC, abstractmethod


def _load_engine():
    """Lazy cross-encoder load so importing reranker.py stays torch-free."""
    from backend.rag_engine import RerankingEngine
    return RerankingEngine()


class Reranker(ABC):
    """Re-orders passages by relevance to a query, returns top_k. The
    duck-typed signature matches today's RerankingEngine.rerank so callers
    (SQLite/Postgres search_similarity, parity runner) are unchanged."""

    @abstractmethod
    def rerank(self, query, passages, top_k=4):
        raise NotImplementedError


class LocalReranker(Reranker):
    """In-process CPU cross-encoder (the historical behavior; default +
    fail-open fallback). Delegates to the injectable engine, which does the
    sigmoid score-squash internally (RerankingEngine.rerank)."""

    def __init__(self, engine=None):
        self._engine = engine  # injectable for tests

    def _engine_or_load(self):
        if self._engine is None:
            self._engine = _load_engine()
        return self._engine

    def rerank(self, query, passages, top_k=4):
        if not passages:
            return []
        engine = self._engine_or_load()
        return list(engine.rerank(query, passages, top_k=top_k))
```

- [ ] **Step 4: Run tests — PASS**

Run: `.venv/bin/python -m pytest tests/test_reranker.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/reranker.py tests/test_reranker.py
git commit -m "feat(rerank): Reranker ABC + LocalReranker (in-process fallback)"
```

---

### Task 2: `RemoteReranker` + `FallbackReranker`

**Files:**
- Modify: `backend/reranker.py`
- Test: `tests/test_reranker.py`

**Interfaces:**
- Consumes: `Reranker`, `LocalReranker` (Task 1).
- Produces: `RemoteReranker(endpoint, timeout, api_key, http)`, `FallbackReranker(primary, fallback)`. Used by Task 4.

- [ ] **Step 1: Write failing tests** (append to `tests/test_reranker.py`)

```python
import pytest
from backend.llm_client import ChatClientError  # reused generic transport error
from backend.reranker import FallbackReranker, LocalReranker, RemoteReranker


class FakeHTTP:
    def __init__(self, result=None, status=200):
        self.posts = []
        self.result = result or {"results": [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.6}]}
        self.status = status
    def post(self, url, json=None, headers=None, timeout=None, **kw):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        class R:
            status_code = self.status
            def raise_for_status(self):
                if self.status >= 400: raise RuntimeError(f"status {self.status}")
            def json(self): return self.result
        return R()


def test_remote_reranker_sends_batched_pool_and_maps_results():
    http = FakeHTTP()
    r = RemoteReranker(endpoint="http://reranker", timeout=7.0, http=http)
    passages = [{"content": "a"}, {"content": "b"}, {"content": "c"}]
    out = r.rerank("q", passages, top_k=2)
    assert http.posts[0]["url"] == "http://reranker/rerank"
    assert http.posts[0]["json"] == {"query": "q", "passages": ["a", "b", "c"], "top_k": 2}
    assert http.posts[0]["timeout"] == 7.0
    assert [p["content"] for p in out] == ["b", "a"]  # ordered by results
    assert out[0]["rerank_score"] == 0.9 and out[1]["rerank_score"] == 0.6


def test_remote_reranker_bearer_only_when_configured():
    http = FakeHTTP()
    RemoteReranker(endpoint="http://reranker", api_key="k", http=http).rerank("q", [{"content":"a"}], top_k=1)
    assert http.posts[0]["headers"]["Authorization"] == "Bearer k"
    http2 = FakeHTTP()
    RemoteReranker(endpoint="http://reranker", http=http2).rerank("q", [{"content":"a"}], top_k=1)
    assert "Authorization" not in http2.posts[0]["headers"]


def test_remote_reranker_raises_on_bad_status():
    http = FakeHTTP(status=500)
    with pytest.raises(RuntimeError):
        RemoteReranker(endpoint="http://reranker", http=http).rerank("q", [{"content":"a"}], top_k=1)


def test_fallback_reranker_falls_back_to_local_on_failure():
    class BoomRemote:
        def rerank(self, q, p, top_k=4): raise RuntimeError("down")
    fallback = LocalReranker(engine=_SortEngine())
    r = FallbackReranker(primary=BoomRemote(), fallback=fallback)
    out = r.rerank("q", [{"content": "a"}, {"content": "b"}], top_k=1)
    assert len(out) == 1  # no exception propagates


class _SortEngine:
    def rerank(self, query, passages, top_k=4):
        scored = [dict(p, rerank_score=float(99 - i)) for i, p in enumerate(passages)]
        return scored[:top_k]
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_reranker.py -q`
Expected: FAIL (`RemoteReranker`/`FallbackReranker` not defined).

- [ ] **Step 3: Implement** (append to `backend/reranker.py`)

```python
class RemoteReranker(Reranker):
    def __init__(self, endpoint, timeout=10.0, api_key=None, http=None):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key
        self._http = http or _DefaultHTTP()

    def _headers(self):
        if self.api_key:
            return {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        return {"Content-Type": "application/json"}

    def rerank(self, query, passages, top_k=4):
        body = {"query": query, "passages": [p["content"] for p in passages], "top_k": top_k}
        resp = self._http.post(
            f"{self.endpoint}/rerank", json=body, headers=self._headers(), timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        out = []
        for item in data.get("results") or []:
            idx = item["index"]
            score = item["score"]
            if 0 <= idx < len(passages):
                passage = dict(passages[idx])
                passage["rerank_score"] = float(score)
                out.append(passage)
        return out[:top_k]


class _DefaultHTTP:
    def post(self, url, json=None, headers=None, timeout=None, **kw):
        import requests
        return requests.post(url, json=json, headers=headers, timeout=timeout, **kw)


class FallbackReranker(Reranker):
    """Tries primary first; on any failure logs one line and uses fallback.
    Never raises to the caller."""

    def __init__(self, primary, fallback):
        self._primary = primary
        self._fallback = fallback

    def rerank(self, query, passages, top_k=4):
        try:
            result = self._primary.rerank(query, passages, top_k=top_k)
            if not result:
                raise RuntimeError("empty remote result")
            return result
        except Exception as exc:  # noqa: BLE001 - deliberate fail-open
            print(f"[reranker] remote failure, using local fallback: {exc}")
            return self._fallback.rerank(query, passages, top_k=top_k)
```

- [ ] **Step 4: Run tests — PASS**

Run: `.venv/bin/python -m pytest tests/test_reranker.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/reranker.py tests/test_reranker.py
git commit -m "feat(rerank): RemoteReranker (batched HTTP) + FallbackReranker (fail-open to local)"
```

---

### Task 3: Config surface + validation

**Files:**
- Modify: `config.py`, `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings` fields `rerank_provider`, `rerank_endpoint`, `rerank_candidate_pool`, `rerank_timeout_seconds`, `rerank_api_key`. Used by Task 4/5.

- [ ] **Step 1: Write failing tests** (append to `tests/test_config.py`)

```python
def test_given_rerank_remote_when_parsed_then_pool_and_endpoint_configured():
    s = Settings.from_env({
        "RERANK_PROVIDER": "remote",
        "RERANK_ENDPOINT": "https://reranker.internal",
        "RERANK_CANDIDATE_POOL": "50",
        "RERANK_TIMEOUT_SECONDS": "7",
        "RERANK_API_KEY": "sk-rerank",
    })
    assert s.rerank_provider == "remote"
    assert s.rerank_endpoint == "https://reranker.internal"
    assert s.rerank_candidate_pool == 50
    assert s.rerank_timeout_seconds == 7
    assert s.rerank_api_key == "sk-rerank"


def test_given_rerank_remote_without_endpoint_then_validation_fails():
    s = Settings.from_env({"RERANK_PROVIDER": "remote"})
    with pytest.raises(ValueError, match="RERANK_ENDPOINT"):
        s.validate_for_environment()


def test_given_rerank_local_then_defaults():
    s = Settings.from_env({"RERANK_PROVIDER": "local"})
    assert s.rerank_endpoint is None
    assert s.rerank_candidate_pool == 50


def test_given_bad_rerank_provider_then_validation_fails():
    s = Settings.from_env({"RERANK_PROVIDER": "cpu"})
    with pytest.raises(ValueError, match="RERANK_PROVIDER"):
        s.validate_for_environment()
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: FAIL (no such fields/validation).

- [ ] **Step 3: Implement**

In `config.py` dataclass (near reranker_model):

```python
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_provider: str = "local"   # "local" (in-process CPU) | "remote" (GPU service)
    rerank_endpoint: str | None = None
    rerank_candidate_pool: int = 50
    rerank_timeout_seconds: int = 10
    rerank_api_key: str | None = None
```

In `from_env`:

```python
            rerank_provider=env.get("RERANK_PROVIDER", "local").strip().lower(),
            rerank_endpoint=env.get("RERANK_ENDPOINT") or None,
            rerank_candidate_pool=_int(env.get("RERANK_CANDIDATE_POOL"), 50, "RERANK_CANDIDATE_POOL"),
            rerank_timeout_seconds=_int(env.get("RERANK_TIMEOUT_SECONDS"), 10, "RERANK_TIMEOUT_SECONDS"),
            rerank_api_key=env.get("RERANK_API_KEY") or None,
```

In `validate_for_environment`:

```python
        if self.rerank_provider not in {"local", "remote"}:
            raise ValueError("RERANK_PROVIDER must be local or remote")
        if self.rerank_provider == "remote" and not self.rerank_endpoint:
            raise ValueError("RERANK_ENDPOINT is required when RERANK_PROVIDER is remote")
```

Append to `.env.example`:

```ini
# Phase 5.2: reranker provider. "local" = in-process CPU cross-encoder
# (default); "remote" = a dedicated GPU service exposing POST /rerank.
RERANK_PROVIDER=local
RERANK_ENDPOINT=          # required when RERANK_PROVIDER=remote
RERANK_CANDIDATE_POOL=50  # pre-rerank candidate pool; cost is bounded by this
RERANK_TIMEOUT_SECONDS=10
RERANK_API_KEY=           # optional bearer for the remote service, from secrets
```

- [ ] **Step 4: Run tests — PASS**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config.py .env.example tests/test_config.py
git commit -m "feat(rerank): config for remote provider + candidate pool"
```

---

### Task 4: `_build_reranker` + wiring into `app.py`

**Files:**
- Modify: `app_factory.py`, `backend/app.py`
- Test: `tests/test_factory.py`

**Interfaces:**
- Consumes: `RemoteReranker`/`LocalReranker`/`FallbackReranker` (Task 2), Settings (Task 3).
- Produces: `_build_reranker(settings) -> Reranker`, `AppDependencies.reranker`; `app.py` builds `_reranker` and passes it as `reranking_engine` to the router and to `search_similarity` calls. Used by Task 5.

- [ ] **Step 1: Write failing test** (append to `tests/test_factory.py`)

```python
from backend.reranker import FallbackReranker, LocalReranker, RemoteReranker
from app_factory import _build_reranker


def test_build_reranker_local_returns_local():
    from config import Settings
    r = _build_reranker(Settings.from_env({"RERANK_PROVIDER": "local"}))
    assert isinstance(r, LocalReranker)


def test_build_reranker_remote_returns_fallback_wrapping_remote():
    from config import Settings
    r = _build_reranker(Settings.from_env({"RERANK_PROVIDER": "remote", "RERANK_ENDPOINT": "http://rr"}))
    assert isinstance(r, FallbackReranker)
    assert isinstance(r._primary, RemoteReranker)
    assert isinstance(r._fallback, LocalReranker)
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_factory.py -q`
Expected: FAIL (`_build_reranker` not defined).

- [ ] **Step 3: Implement**

In `app_factory.py`, add `reranker` to `AppDependencies` and a builder:

```python
    rate_limiter: Any | None = None
    llm_client: Any | None = None
    reranker: Any | None = None
```

```python
def _build_reranker(settings: Settings):
    """Provider-neutral reranker (Phase 5.2). local = in-process CPU
    cross-encoder (default); remote = a dedicated GPU service, with a
    FallbackReranker that fails open to local. Mirrors the other builders."""
    from backend.reranker import FallbackReranker, LocalReranker, RemoteReranker
    local = LocalReranker()
    if settings.rerank_provider != "remote":
        return local
    remote = RemoteReranker(
        endpoint=settings.rerank_endpoint,
        timeout=float(settings.rerank_timeout_seconds),
        api_key=settings.rerank_api_key,
    )
    return FallbackReranker(primary=remote, fallback=local)
```

In `build_dependencies`, set `reranker=_build_reranker(settings)`.

In `backend/app.py`:

- import `_build_reranker`; build `_reranker = _build_reranker(settings)`.
- replace the lazily-loaded `RerankingEngine` usage in `_get_reranking_engine`/the router with `_reranker` (the duck-typed `.rerank` is interchangeable). Specifically `agentic_router.run_query(... reranking_engine=_reranker ...)`, and pass `reranking_engine=_reranker` wherever `search_similarity` is called from `app.py` (`/api/eval/search`).

- [ ] **Step 4: Run tests — PASS**

Run: `.venv/bin/python -m pytest tests/test_factory.py tests/test_api_rbac.py tests/test_api_audit.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app_factory.py backend/app.py tests/test_factory.py
git commit -m "feat(rerank): build reranker from settings; wire into app.py"
```

---

### Task 5: Candidate-pool parameter in both search_similarity backends

**Files:**
- Modify: `backend/rag_engine.py`, `backend/postgres_store.py`
- Test: `tests/test_store_rerank_pool.py` (new) + a PG-gated case in `tests/test_postgres_store.py`

**Interfaces:**
- Consumes: `Settings.rerank_candidate_pool` (Task 3); the `Reranker` (Task 2) passed as `reranking_engine`.
- Produces: `VectorStore.search_similarity(..., reranking_engine=None, top_k=15, use_fts=True, candidate_pool=50)`; `app.py` passes `candidate_pool=settings.rerank_candidate_pool`.

- [ ] **Step 1: Write failing test** (`tests/test_store_rerank_pool.py`)

```python
from backend.rag_engine import SQLiteVectorStore
from backend.reranker import LocalReranker


class _HashEmbed:
    def _vec(self, text):
        import hashlib
        d = hashlib.md5((text or "").encode()).digest()
        return [d[i % len(d)] / 255.0 for i in range(16)]
    def embed_chunks(self, chunks): return [self._vec(c) for c in chunks]
    def embed_query(self, q): return self._vec(q)


class _PoolReranker(LocalReranker):
    """Records the pool size handed to rerank."""
    def __init__(self): self.last_pool = 0
    def rerank(self, query, passages, top_k=4):
        self.last_pool = len(passages)
        return passages[:top_k]


def _corpus():
    return [
        ("a.txt", "txt", 3, "Alfa alpha apple angle anchor", None),
        ("b.txt", "txt", 3, "Bravo beta banana", None),
        ("c.txt", "txt", 3, "Charlie charlie cat", None),
    ]


def test_candidate_pool_accepts_more_than_top_k(tmp_path):
    store = SQLiteVectorStore(db_path=str(tmp_path/"s.db"), storage_dir=str(tmp_path/"d"))
    emb = _HashEmbed()
    for fn, ft, fs, text, claim in _corpus():
        store.add_document(fn, ft, fs, text, emb, claim_id=claim)
    rr = _PoolReranker()
    q = emb.embed_query("alpha banana")
    store.search_similarity(q, "alpha banana", reranking_engine=rr, top_k=2, candidate_pool=50)
    assert rr.last_pool == 3  # whole tiny corpus admitted (pool >= corpus)


def test_candidate_pool_tight_limits_recall(tmp_path):
    store = SQLiteVectorStore(db_path=str(tmp_path/"s.db"), storage_dir=str(tmp_path/"d"))
    emb = _HashEmbed()
    for fn, ft, fs, text, claim in _corpus():
        store.add_document(fn, ft, fs, text, emb, claim_id=claim)
    rr = _PoolReranker()
    q = emb.embed_query("alpha banana")
    store.search_similarity(q, "alpha banana", reranking_engine=rr, top_k=2, candidate_pool=1)
    assert rr.last_pool <= 1
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_store_rerank_pool.py -q`
Expected: FAIL (`search_similarity` rejects `candidate_pool` kwarg).

- [ ] **Step 3: Implement**

In `backend/rag_engine.py`, `VectorStore.search_similarity` and `SQLiteVectorStore.search_similarity`:

```python
    def search_similarity(self, query_embedding, query_text, claim_id=None, reranking_engine=None, top_k=15, use_fts=True, candidate_pool=50):
```

Replace the cut line:

```python
        top_candidates = fused_results[:max(15, top_k)]
```
with:
```python
        top_candidates = fused_results[:candidate_pool]
```

_(Semantics change is safe: without a reranker the code returns `top_candidates[:top_k]`; with a reranker it calls `rerank(..., top_k=top_k)`. The default `candidate_pool=50` preserves a superset of the old `max(15, top_k)` pool.)_

In `backend/postgres_store.py`, mirror the same signature change and replace its equivalent hardcoded cap with `candidate_pool`.

In `backend/app.py`, `/api/eval/search` passes `candidate_pool=settings.rerank_candidate_pool`.

- [ ] **Step 4: Run tests — PASS**

Run: `.venv/bin/python -m pytest tests/test_store_rerank_pool.py tests/test_rag_engine.py tests/test_api_rbac.py -q`
Expected: PASS. Add a PG-gated pool case in `tests/test_postgres_store.py` (asserts `candidate_pool` honored) that runs only when local PG is present (`@pytest.mark.skipif(not _schema_ready, ...)` pattern already used there).

- [ ] **Step 5: Commit**

```bash
git add backend/rag_engine.py backend/postgres_store.py backend/app.py tests/test_store_rerank_pool.py tests/test_postgres_store.py
git commit -m "feat(rerank): candidate_pool param bounds rerank cost on both backends"
```

---

### Task 6: Full verification + docs

**Files:** none new; run everything; update docs.

- [ ] **Step 1: Full suite + lint + gates + parity**

Run:
```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check .
.venv/bin/python scripts/run_foundation_gates.py --mode gate
.venv/bin/python -m compileall -q backend app_factory.py config.py
.venv/bin/python eval/parity_runner.py
```
Expected: full suite green, ruff clean, gates 0 blocking, compileall clean, parity recall@4 ≥ 0.9 (unchanged behavior with default pool).

- [ ] **Step 2: update migration doc** — milestone 5.2 status paragraph; CLAUDE.md Current State / What's Next / Session Log (next milestone 5.3).

- [ ] **Step 3: Commit**

```bash
git add docs/enterprise-migration.md CLAUDE.md
git commit -m "docs: Phase 5.2 reranker service status"
```

- [ ] **Step 4: Standing workflow** — branch → push → `gh pr create` → watch CI on the self-hosted runner → merge when green.