# Local Reranker Cross-Request Batching Design

> **Status:** Approved and implemented
> **Date:** 2026-09-13
> **Scope:** Local in-process reranker only; `RemoteReranker` is unchanged.

## 1. Goal

Reduce concurrent local cross-encoder latency by coalescing independent rerank requests into one model inference call, while preserving the existing `Reranker.rerank(query, passages, top_k)` contract and the exact per-request ranking behavior.

The optimization targets the Phase 6.1 finding that concurrent GPU requests currently execute as separate, small `CrossEncoder.predict()` calls. A feasibility probe on the RX 9070 XT showed that flattening 2–16 independent requests into one call preserved ranking exactly and produced a measured 1.06–1.24× speedup. Explicit model inference `batch_size=16` was the best tested setting for a 240-pair probe at approximately 64 ms. These results justify a guarded production experiment, but do not establish an SLO improvement by themselves.

## 2. Non-goals

- No change to retrieval, RRF fusion, candidate-pool selection, context assembly, or answer generation.
- No change to the remote HTTP reranker protocol. `RemoteReranker` continues to issue one request using its existing `/rerank` contract.
- No process-wide or cross-application shared model service. Batching is scoped to one `LocalReranker` instance and therefore one application process/model.
- No claim that batching will meet the 100 ms concurrent p95 target without a controlled load-test result.
- No dynamic batching of unrelated data beyond the bounded rerank requests already produced by `search_similarity`.

## 3. Design

### 3.1 Public compatibility seam

Keep the existing abstract interface unchanged:

```python
class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, passages: list[dict], top_k: int = 4) -> list[dict]:
        ...
```

Add an internal scoring capability to the local model engine:

```python
def score_pairs(
    self,
    pairs: list[tuple[str, str]],
    inference_batch_size: int = 16,
) -> list[float]:
    """Return one raw cross-encoder score per pair, in input order."""
```

`RerankingEngine.score_pairs()` calls the existing `CrossEncoder.predict()` with the flattened pairs and an explicit `batch_size`. Its existing `rerank()` method remains a compatibility wrapper that scores, applies the existing sigmoid normalization, sorts descending, and returns `top_k` results.

`LocalReranker` uses `score_pairs()` only when the injected engine supports it. This preserves hermetic tests and existing callers that inject a minimal engine exposing only `rerank()`: those engines use the current semaphore-protected direct path. The production `RerankingEngine` supports batching.

Returned passage dictionaries must be copies of the input dictionaries. The batching path must not mutate a request's caller-owned list or dictionaries while another request is being assembled.

### 3.2 Micro-batching coordinator

Add a private coordinator used by `LocalReranker` when batching is enabled. Each public `rerank()` call creates a work item containing:

- the query;
- an immutable snapshot of the candidate passages' content and metadata;
- `top_k`;
- a future/event for the result or exception.

The coordinator owns one bounded pending queue and one dispatcher thread. The dispatcher:

1. waits for the first pending request;
2. collects additional requests that arrive during `max_wait_ms`;
3. stops collecting when either `max_requests` or `max_pairs` would be exceeded;
4. flattens each request's `(query, passage["content"])` pairs in FIFO order;
5. calls `engine.score_pairs(flat_pairs, inference_batch_size=...)` once;
6. partitions scores by the original request boundaries;
7. applies sigmoid normalization, descending sort, and each request's `top_k` independently;
8. resolves each request future.

A request is never partially split across model batches. If a single request exceeds `max_pairs`, it is processed as its own batch rather than silently dropping candidates. The production candidate pool is already bounded, so the default limit is sized above the default pool.

The coordinator serializes model calls through one dispatcher. This is intentional: the current model instance is shared, and one larger inference call is the optimization target. `RERANK_MAX_CONCURRENCY` remains the compatibility/fallback limit for engines without `score_pairs()` and for batching-disabled operation; it does not create multiple concurrent calls against the same batched model instance.

### 3.3 Defaults and rollback controls

Add settings and environment variables:

| Setting | Environment variable | Default | Purpose |
|---|---|---:|---|
| `rerank_batching_enabled` | `RERANK_BATCHING_ENABLED` | `true` | Immediate rollback switch to the existing direct path |
| `rerank_batch_max_wait_ms` | `RERANK_BATCH_MAX_WAIT_MS` | `5` | Maximum coalescing delay before dispatch |
| `rerank_batch_max_requests` | `RERANK_BATCH_MAX_REQUESTS` | `16` | Maximum independent requests per inference call |
| `rerank_batch_max_pairs` | `RERANK_BATCH_MAX_PAIRS` | `256` | Maximum flattened query/passage pairs per inference call |
| `rerank_inference_batch_size` | `RERANK_INFERENCE_BATCH_SIZE` | `16` | `CrossEncoder.predict(batch_size=...)` value |
| `rerank_batch_max_pending` | `RERANK_BATCH_MAX_PENDING` | `1024` | Bound pending work to prevent an overload-induced memory queue |

All numeric settings must use the existing positive-value parsing helpers. `RERANK_BATCH_MAX_PAIRS` must be at least `RERANK_CANDIDATE_POOL`; invalid combinations fail settings validation rather than truncating a production request.

`app_factory._build_reranker()` passes these settings to `LocalReranker`. The remote provider receives no batching options and remains behaviorally unchanged.

### 3.4 Empty input, overload, errors, and shutdown

- Empty passage lists return `[]` immediately without entering the queue, matching current behavior.
- A full pending queue raises a controlled `RerankerOverloadedError` (or equivalent project-local exception) to the request caller. It must not block indefinitely or grow memory without bound.
- A model exception resolves every request represented in that failed model batch with the same exception. No request may hang waiting for a result.
- A malformed score count from the engine is treated as a batch failure and propagated to all affected requests; scores must never be assigned to the wrong request.
- `LocalReranker.close()` stops accepting new work, drains or fails pending work deterministically, and joins the dispatcher thread. Closing twice is safe. The app lifespan calls close when the configured reranker exposes it; the existing remote adapter does not require lifecycle handling.
- If batching is disabled, all of these calls use the current direct semaphore path, preserving the rollback behavior without loading or starting a dispatcher.

### 3.5 Observability

The coordinator should keep lightweight process-local counters accessible through a testable `stats()` method:

- submitted requests;
- dispatched model batches;
- total pairs scored;
- requests coalesced into batches;
- queue-overload rejections;
- model-batch failures;
- maximum pending depth.

No query text, document content, claim identifier, or source filename is logged or retained in metrics. The counters are diagnostic only and do not change the audit contract.

## 4. Data flow

```text
search_similarity()
    -> LocalReranker.rerank(query, candidate_pool, top_k)
        -> bounded pending queue
            -> micro-batch dispatcher
                -> RerankingEngine.score_pairs(flat_pairs, batch_size=16)
                    -> CrossEncoder.predict(...)
                -> split scores by request boundaries
            -> each request receives its own sorted top_k copies
```

The retrieval callers in `SQLiteVectorStore` and `PostgresVectorStore` remain unchanged. They continue to overwrite the returned item's `score` with `rerank_score`, so the public result shape and downstream behavior do not change.

## 5. Correctness and safety requirements

1. A request receives exactly the same number of results and the same passage identities as direct scoring, subject only to documented floating-point differences from execution shape.
2. Score-to-request partitioning is based on explicit pair counts, never on filenames, IDs, or list positions from another request.
3. Passage metadata is isolated between requests; one caller cannot observe another caller's query or passages through shared mutable objects.
4. The queue is bounded and has a deterministic overload response.
5. Model, embedding, and torch imports remain lazy. Importing `backend.reranker` and exercising fake-engine tests must not import torch.
6. `engine` allowlisting, tenant/claim scope, filename sanitization, and all existing security/grounding constraints remain untouched.
7. The remote fallback chain remains valid: `FallbackReranker` still calls `primary.rerank()` and falls back to the local reranker if the remote request fails.

## 6. Testing strategy

### Unit tests

Add focused tests in `tests/test_reranker.py` and configuration/factory tests in the existing test modules:

- `RerankingEngine.score_pairs()` forwards flattened pairs in order and passes the configured inference batch size to a fake model.
- Two simultaneous local requests are coalesced into one fake-engine batch; each caller receives only its own correctly sorted `top_k` results.
- A request arriving after the coalescing window is dispatched independently.
- `max_requests` and `max_pairs` split work into multiple batches without splitting an individual request.
- A fake engine failure is delivered to every request in the failed batch without hanging any caller.
- A malformed score count fails the batch rather than cross-wiring scores.
- A full bounded queue rejects work deterministically.
- Returned passage dictionaries are isolated copies and input dictionaries remain unchanged.
- Empty input bypasses the queue.
- `RERANK_BATCHING_ENABLED=false` preserves the existing direct path and semaphore cap.
- `close()` rejects new work and is idempotent.
- Existing lazy-load, device, remote adapter, fallback, and direct-path tests continue to pass.
- Settings parse defaults and overrides, reject non-positive values, and reject a batch-pair limit below the candidate pool.
- Factory wiring passes all batching settings to `LocalReranker`.

All unit tests use fake engines/models and remain independent of model downloads, GPUs, network access, and timing-sensitive sleeps where possible. Coordinator tests should use events/barriers or an injectable clock rather than relying on narrow scheduler timing.

### Deterministic equivalence test

Run the 19 golden queries through the real retrieval path twice, once with batching disabled and once enabled, using the same local model/device. Compare passage IDs/order and score deltas. Any order difference is a blocking regression for this optimization; small raw-score differences must be reported with a defined tolerance and must not change the returned top-k set.

### Performance verification

Reuse the existing retrieval load-test harness and compare on the same machine, corpus, model, device, request mix, and duration:

- batching disabled;
- batching enabled with defaults;
- batching enabled with the measured `inference_batch_size=16`.

Record single-request p50/p95/p99, concurrent p50/p95/p99, throughput, error rate, coalescing rate, average pairs per model batch, and overload count. The optimization is considered successful only if:

- deterministic equivalence passes;
- focused and full test suites pass;
- error rate remains zero under the selected load;
- single-request p95 does not regress by more than 10%; and
- concurrent p95 improves in a repeatable run, with the actual result recorded rather than inferred from the unit tests.

A result that fails to improve concurrent p95 is a valid finding: disable the feature by configuration and document the measurement rather than weakening correctness or queue bounds.

## 7. Files expected to change during implementation

- `backend/reranker.py` — score-pair capability, bounded micro-batching coordinator, local adapter integration, lifecycle/stats.
- `backend/rag_engine.py` — `RerankingEngine.score_pairs()` and compatibility wrapper refactor only.
- `config.py` — typed batching settings, environment parsing, validation.
- `.env.example` — documented local batching controls and rollback switch.
- `app_factory.py` — construct the local reranker with batching settings and close it during app shutdown.
- `tests/test_reranker.py`, `tests/test_config.py`, `tests/test_factory.py` — unit, configuration, and wiring coverage.
- `docs/enterprise-migration.md` and `CLAUDE.md` — record the measured result and update Phase 6.1/Current State only after performance verification.

No database migration, API route, frontend, dependency, remote service, or retrieval-caller change is expected.

## 8. Implementation outcome

The design was approved and implemented on 2026-09-13. The deterministic and full repository verification requirements passed. The warm in-process GPU probe showed approximately a 5.1% throughput improvement, while the authoritative Postgres/HTTP concurrent-load comparison remains blocked by the unavailable local PostgreSQL instance on port 55432. Phase 6.1 therefore remains open for SLO verification; the feature is enabled by default but can be rolled back with `RERANK_BATCHING_ENABLED=false`.
