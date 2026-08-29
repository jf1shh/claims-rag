# Phase 5.2 — Dedicated Reranker Service + Pool-Bounded Rerank

> **Part of Phase 5 (Serving & LLM layer) of `docs/enterprise-migration.md`.**
> Milestone 5.2: move the in-process CPU cross-encoder behind the same
> provider-neutral-seam pattern used for the LLM, adding a **remote GPU
> reranker service** adapter while keeping the local in-process cross-encoder as
> the default/fallback, and making the **candidate pool configurable and
> wider** so rerank cost is bounded by the pool, not the corpus.

**Status: Design (draft for review).** Believed date: 2026-08-29.

---

## 1. Context and problem

Today the cross-encoder reranker runs **in-process on CPU**: `RerankingEngine`
(`backend/rag_engine.py`) is constructed in the API process and
`search_similarity` calls its `.rerank(query, passages, top_k)` on both the
SQLite and Postgres backends. The candidate pool fed to it is a hardcoded
`fused_results[:max(15, top_k)]`.

Enterprise scale has two problems:

1. **CPU cross-encoder in the request thread** competes with embeddings and
   inference for host capacity and does not scale horizontally. A dedicated GPU
   cross-encoder service is the natural fit — and matches the enterprise GPU
   footprint from 5.1.
2. **A fixed `max(15, top_k)` pool** caps recall: reranking can only re-order
   what the pool admits. Phase 5.2's exit criterion is *"rerank cost bounded by
   candidate pool, not corpus"* — which means returning **more** candidates
   (e.g. 50–100) from retrieval and letting the reranker cut to `top_k`. The
   bound that matters is the pool size, so it must be configurable.

## 2. Goals

- Add a provider-neutral `Reranker` seam with the **same duck-typed
  `.rerank(query, passages, top_k)` interface** the stores/parity already call,
  so `search_similarity` and `eval/parity_runner.py` do not change their call
  sites.
- **`LocalReranker`** — wraps the existing in-process CPU cross-encoder (default
  and fallback).
- **`RemoteReranker`** — HTTP adapter to a dedicated GPU service; sends the whole
  candidate pool batched in one request and maps the scored response back to
  `top_k`.
- Make the **candidate pool configurable** (`RERANK_CANDIDATE_POOL`, default 50)
  in both backends.
- Fail-open to local on remote error/unconfigured.
- Verified hermetically against a fake reranker service; the service itself
  (which hosts the actual GPU model) is deployment wiring.

## 3. Non-goals / out of scope

- Implementing the reranker service's internals (loading a cross-encoder on GPU,
  batching, a `POST /rerank` server). The contract the app calls is defined
  here; the service binary/deployment belongs to the operator (documented in
  Section 9).
- Changing the retrieval RRF/fusion math or the `VectorStore` interface.
- Anything that reorders the existing parity harness semantics beyond the pool
  knob (parity currently passes at `top_k=4` with recall ≥ 0.98; the pool only
  widens what rerank can select from).

## 4. Target architecture

```
        search_similarity (SQLite & Postgres)
                     │
                     ▼
              Reranker (ABC)           ← injected by app_factory
              ┌────────────┴───────────────┐
              ▼                             ▼
     LocalReranker                    RemoteReranker
     (in-process CPU                  POST {RERANK_ENDPOINT}/rerank
      cross-encoder)                  {query, passages, top_k}
                                          batched pool → scored results
              └─────────────────────────────┴──► fail-open to local on error
```

## 5. Components & interfaces

### 5.1 `Reranker` (Abstract Base Class) — `backend/reranker.py`

```python
class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, passages: list[dict], top_k: int) -> list[dict]:
        """Re-orders passages by relevance to query, returns the top_k.
        Passages are dicts with at least 'content'; the returned list is
        the same dicts (with 'rerank_score' set, descending) truncated to
        top_k. Must be side-effect-restricted to the passed lists."""
```

- Signature deliberately matches `RerankingEngine.rerank(query, passages,
  top_k)` so every caller (SQLite `search_similarity`, Postgres
  `search_similarity`, the router's inline rerank use via `search_similarity`)
  is unchanged.

### 5.2 `LocalReranker(Reranker)`

- Owns an optional lazily-imported `RerankingEngine` (keeps the lazy-ML-import
  constraint) and delegates to `engine.rerank(query, passages, top_k)`. This is
  byte-for-byte today's behavior when it is the selected provider.

### 5.3 `RemoteReranker(Reranker)`

```python
class RemoteReranker(Reranker):
    def __init__(
        self,
        endpoint: str,            # allowlisted service base URL, e.g. http://reranker.internal
        timeout: float = 10.0,
        api_key: str | None = None,   # optional bearer credential from secrets
        http: object | None = None,   # injectable HTTP client for hermetic tests
    ):
        ...
    def rerank(self, query, passages, top_k):
        body = {"query": query, "passages": [p["content"] for p in passages],
                "top_k": top_k}
        resp = self._http.post(f"{self.endpoint.rstrip('/')}/rerank",
                               json=body, timeout=self.timeout,
                               headers=self._auth_headers())
        resp.raise_for_status()
        data = resp.json()
        # data: {"results": [{"index": int, "score": float}, ...]}, sorted desc,
        # length <= top_k. Rebuild the original passage list ordered by those
        # scores (index refers to position in `passages`), attach rerank_score,
        # return the truncated list.
```

- **Contract with the service**: `POST /rerank` with
  `{"query": str, "passages": [str, ...], "top_k": int}` → `200` with
  `{"results": [{"index": int, "score": float}, ...]}` ordered descending,
  up to `top_k` items. `index` is the 0-based position in the `passages` array.
- Fail-open: any exception (connection, non-200, malformed `results`) is caught
  by the caller (below) and falls back to `LocalReranker`.

### 5.4 Config — `config.py` / `.env.example`

| Env var | Default | Purpose |
|---|---|---|
| `RERANK_PROVIDER` | `local` | `local` (in-process CPU) or `remote` (GPU service) |
| `RERANK_ENDPOINT` | *(empty)* | Allowlisted reranker base URL; required when `remote` |
| `RERANK_CANDIDATE_POOL` | `50` | Candidate pool passed to rerank before cutting to `top_k` |
| `RERANK_TIMEOUT_SECONDS` | `10` | Remote rerank HTTP timeout |
| `RERANK_API_KEY` | *(empty)* | Optional bearer credential for the service |

Validation: `RERANK_PROVIDER` in `{local, remote}`; `remote` requires a non-empty,
http(s) `RERANK_ENDPOINT` (reject in `validate_for_environment`, mirroring the
SQS/queue validation pattern). `RERANK_CANDIDATE_POOL >= top_k` enforced (raise
`ValueError` if a request's `top_k` exceeds the pool).

### 5.5 `_build_reranker(settings)` — `app_factory.py`

```python
def _build_reranker(settings: Settings) -> Reranker:
    if settings.rerank_provider != "remote":
        return LocalReranker()
    remote = RemoteReranker(endpoint=settings.rerank_endpoint,
                            timeout=settings.rerank_timeout_seconds,
                            api_key=settings.rerank_api_key)
    return FallbackReranker(primary=remote, fallback=LocalReranker())
```

- `FallbackReranker(Reranker)` wraps `rerank()` in try/except → `primary` first,
  on any failure logs (one line) and delegates to `fallback`. No exception
  propagates to `search_similarity`.
- Expose `reranker` on `AppDependencies` and thread it into `app.py` (replacing
  the directly-constructed `RerankingEngine`).

### 5.6 Backend pool change — `rag_engine.py` + `postgres_store.py`

Replace the hardcoded candidate-pool cut in both `search_similarity`
implementations:

```python
# settings.rerank_candidate_pool (default 50)
top_candidates = fused_results[:settings_or_pool]
```

- **Signature:** rather than coupling stores to `Settings`, pass the pool into
  `search_similarity` as a new optional keyword `candidate_pool: int = 50`
  (default preserves today's behavior for any existing callers/parity that
  don't pass it). `app.py` passes `settings.rerank_candidate_pool`. The `VectorStore`
  ABC `search_similarity` signature gains the same optional param.
- Semantics unchanged: `fused_results` (RRF) sorted desc, cut to
  `candidate_pool`, then `rerank` (when a `Reranker` is provided) cuts to
  `top_k`. Without a reranker, `top_candidates[:top_k]` unchanged.
- Both backends use the `Reranker` passed in (LocalReranker wraps the old
  `RerankingEngine`, so behavior is identical for `RERANK_PROVIDER=local`).

## 6. Data flow (online query with rerank)

1. `search_similarity(query, …)` fuses vector + FTS via RRF → `fused_results`.
2. Cut to `candidate_pool` (default 50).
3. `reranker.rerank(query, top_candidates, top_k)` →
   - local: in-process cross-encoder (unchanged);
   - remote: one batched `POST /rerank`, scored, reordered to `top_k`, or
     falls back to local on failure.
4. Returns `top_k` reordered passages.

## 7. Error handling & robustness

- Remote timeouts/HTTP errors/malformed `results` → `FallbackReranker` logs and
  uses local. query correctness is never blocked by the service being briefly
  down.
- `top_k > RERANK_CANDIDATE_POOL` → config/API raises `ValueError` (mapped to
  400 at the route where `top_k` is user-supplied, mirroring the Phase-4.4
  bounded-input 413/400 handling).
- The service returning more/fewer than `top_k` results: clamp to `top_k`; if
  `results` is malformed or a referenced `index` is out of range, fail-open to
  local.

## 8. Security

- **SSRF:** `RERANK_ENDPOINT` is server-side config; no client input ever shapes
  the URL. Regression test: a request cannot redirect the reranker target.
- **Credential handling:** `RERANK_API_KEY` from env/secrets, sent only as the
  Bearer header; never logged or echoed.
- **Isolation:** the reranker call passes only passage *content* (already
  retrieved, in-scope text), never filenames/paths or un-scoped data.

## 9. Deployment wiring appendix (not built here)

- Stand up a small service that loads the cross-encoder on GPU and exposes
  `POST /rerank` matching Section 5.3's contract (e.g. vLLM/TGI can host a
  cross-encoder model, or a thin container around `sentence-transformers`
  `CrossEncoder`). Set `RERANK_PROVIDER=remote`, `RERANK_ENDPOINT` to its URL,
  `RERANK_API_KEY` from secrets, and tune `RERANK_CANDIDATE_POOL` to the
  service's batch capacity.
- The hermetic `RemoteReranker` tests + parity runner prove the app side; live
  verification against the actual service is the operator's step.

## 10. Testing strategy (hermetic — no GPU in CI)

**Unit — `tests/test_reranker.py`**
- `LocalReranker` delegates to a stub engine and honors `top_k` (so it wraps
  today's behavior).
- `RemoteReranker` sends the exact `{"query", "passages", "top_k"}` body to
  `{endpoint}/rerank`, attaches the Bearer header only when `api_key` is set,
  and maps `results[index].score` back onto the original passages preserving
  order, truncating to `top_k`.
- `RemoteReranker` raises on non-200 / malformed results / out-of-range index.
- `FallbackReranker` falls back to local on any failure and still returns a valid
  top_k; does not raise.
- `model/score` bookkeeping: returned dicts carry `rerank_score`, descending.
- Config: `RERANK_PROVIDER` validation, `remote`-without-endpoint rejected,
  pool < top_k rejected.

**Battery/parity — `tests/test_store_rerank_pool.py` + parity runner**
- `search_similarity(..., candidate_pool=N)` returns up to `top_k` after rerank
  and that a wider pool can surface a result that a pool of exactly `top_k`
  misses on a crafted corpus (proving the pool bounds recall, not `top_k`).
- SQLite + Postgres (PG-gated) both honor the pool; Postgres case runs under the
  existing PG gate in CI.
- `eval/parity_runner.py` still passes `@4` with the default pool; a parity run
  with an explicit wider pool regression-checks no ordering regression.

## 11. Exit-criteria mapping

| Migration doc exit criterion | How this milestone meets it |
|---|---|
| "Dedicated cross-encoder reranker service (GPU, batched)" | `RemoteReranker` = the app's contract to that service; batched pool sent in one request. |
| "pgvector returns top 50–100 candidates, rerank cuts to `top_k`" | `RERANK_CANDIDATE_POOL` (default 50) widens the pre-rerank pool on both backends; rerank cuts to `top_k`. |
| "Rerank cost bounded by candidate pool, not corpus" | Cost = `RERANK_CANDIDATE_POOL`, configurable, independent of corpus size. |