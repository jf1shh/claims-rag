# Local Reranker Cross-Request Batching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add bounded local cross-request micro-batching for the cross-encoder while preserving the existing `Reranker.rerank(query, passages, top_k)` contract and proving whether it improves concurrent latency.

**Architecture:** Keep `Reranker` unchanged. Add `RerankingEngine.score_pairs()` as a lazy-model scoring primitive, then have `LocalReranker` optionally submit immutable request snapshots to a bounded dispatcher that coalesces requests for a short window, performs one flattened model call, partitions scores by explicit request boundaries, and resolves one result per caller. Engines without `score_pairs()` and the disabled-feature path retain the current semaphore-protected direct call.

**Tech Stack:** Python 3.12, threading primitives, pytest, existing Torch/SentenceTransformers `CrossEncoder`, FastAPI application factory, Ruff. No new dependency, database migration, API route, or remote protocol.

**Spec:** `docs/superpowers/specs/2026-09-13-local-reranker-batching-design.md`

## Global Constraints

- Preserve the public `Reranker.rerank(query, passages, top_k)` interface and existing retrieval result shape.
- Keep Torch and SentenceTransformers imports lazy; importing `backend.reranker` and running fake-engine tests must remain ML-free.
- Never mutate caller-owned passage lists or dictionaries during batching; return isolated dictionary copies.
- Partition flattened scores by explicit per-request pair counts; never infer boundaries from filenames, IDs, or shared mutable state.
- Bound pending work and return a deterministic overload error; no unbounded queue or indefinite blocking.
- A model or score-shape failure must resolve every affected request and leave no waiter hanging.
- `RERANK_BATCHING_ENABLED=false` must retain the current direct semaphore path.
- `RemoteReranker` and its existing HTTP `/rerank` contract remain unchanged.
- Do not modify retrieval, RRF, candidate-pool, context, auth, tenant, filename, or grounding behavior.
- Use fake engines/models for unit tests; no model download, network, GPU, or wall-clock-sensitive sleeps in the focused suite.

---

### Task 1: Add the engine-level score-pairs primitive

**Files:**
- Modify: `backend/rag_engine.py:195-218`
- Test: `tests/test_rag_engine.py`

**Interfaces:**
- Consumes: the existing `RerankingEngine.model.predict()` interface.
- Produces: `RerankingEngine.score_pairs(pairs: list[tuple[str, str]], inference_batch_size: int = 16) -> list[float]`; existing `RerankingEngine.rerank()` continues to return normalized, sorted passage dictionaries.

- [x] **Step 1: Write the failing test**

Add a fake model and test that specifies the exact call contract:

```python
class _RecordingCrossEncoder:
    def __init__(self):
        self.calls = []

    def predict(self, pairs, show_progress_bar=False, batch_size=None):
        self.calls.append({
            "pairs": list(pairs),
            "show_progress_bar": show_progress_bar,
            "batch_size": batch_size,
        })
        return [float(index) for index, _ in enumerate(pairs)]


def test_reranking_engine_score_pairs_preserves_order_and_batch_size():
    from backend.rag_engine import RerankingEngine

    model = _RecordingCrossEncoder()
    engine = object.__new__(RerankingEngine)
    engine.model = model
    scores = engine.score_pairs([("q1", "p1"), ("q2", "p2")], inference_batch_size=16)

    assert scores == [0.0, 1.0]
    assert model.calls == [{
        "pairs": [("q1", "p1"), ("q2", "p2")],
        "show_progress_bar": False,
        "batch_size": 16,
    }]
```

Also add a compatibility test proving `rerank()` delegates through `score_pairs()`, retains sigmoid normalization, sorts descending, truncates to `top_k`, and does not change the input list's order.

- [x] **Step 2: Run the focused tests and confirm the expected failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_rag_engine.py -q
```

Expected: the new test fails because `RerankingEngine.score_pairs` does not exist.

- [x] **Step 3: Implement the minimal primitive**

Add:

```python
def score_pairs(self, pairs, inference_batch_size=16):
    if not pairs:
        return []
    scores = self.model.predict(
        pairs,
        show_progress_bar=False,
        batch_size=inference_batch_size,
    )
    return [float(score) for score in scores]
```

Refactor `rerank()` to build pairs, call `score_pairs(pairs)`, copy each input passage before adding `rerank_score`, sort the copies, and return `top_k`. Keep the existing sigmoid formula and result ordering. Do not import any ML library at module import time.

- [x] **Step 4: Run the focused tests and confirm they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_rag_engine.py -q
```

Expected: PASS with no model loading or network access.

---

### Task 2: Build the bounded micro-batching coordinator

**Files:**
- Modify: `backend/reranker.py`
- Test: `tests/test_reranker.py`

**Interfaces:**
- Consumes: an engine exposing `score_pairs(flat_pairs, inference_batch_size=...)`.
- Produces: a private coordinator owned by `LocalReranker`, plus a controlled overload exception such as `RerankerOverloadedError` and coordinator `stats()`/`close()` behavior.

- [x] **Step 1: Write deterministic failing tests**

Use `threading.Event`, `ThreadPoolExecutor`, and fake engines that record calls; do not use timing assertions. Add tests with these behaviors:

```python
def test_local_reranker_batches_independent_requests_without_cross_wiring():
    # Fake score_pairs records one flattened call and returns scores whose order
    # makes each request's result visibly different. Submit two calls while the
    # fake engine is held by an Event, then release it. Assert one engine call,
    # each result contains only its own passage dictionaries, and each result is
    # independently sorted/truncated by top_k.


def test_local_reranker_respects_max_requests_and_max_pairs():
    # Submit requests that would exceed max_requests and max_pairs. Assert the
    # recorded flattened calls contain whole requests only and no call exceeds
    # either bound unless one individual request itself exceeds max_pairs.


def test_local_reranker_delivers_batch_failure_to_every_waiter():
    # Fake score_pairs raises RuntimeError. Submit two coalesced calls and assert
    # both futures raise RuntimeError rather than hanging.


def test_local_reranker_rejects_malformed_score_count():
    # Fake score_pairs returns one fewer score than flattened pairs. Assert all
    # callers in that batch receive a ValueError and no result is returned.


def test_local_reranker_rejects_when_pending_queue_is_full():
    # Hold the dispatcher on one model call, fill max_pending, and assert the
    # next submission raises RerankerOverloadedError immediately.


def test_local_reranker_close_is_idempotent_and_rejects_new_work():
    # Call close twice, then assert a non-empty rerank call fails with the
    # documented closed-state exception.
```

Add tests for empty input, passage-copy isolation, direct fallback for an engine exposing only `rerank()`, and `stats()` counters. Use a dispatcher start barrier or an injectable dispatch hook to control when requests are pending; avoid relying on a 5 ms sleep to prove coalescing.

- [x] **Step 2: Run the focused tests and confirm the expected failures**

Run:

```bash
.venv/bin/python -m pytest tests/test_reranker.py -q
```

Expected: the new coordinator tests fail because the coordinator, batching parameters, and exception do not exist.

- [x] **Step 3: Implement the request work item and coordinator**

Use a private work item containing an immutable tuple of copied passage dictionaries, the query, `top_k`, and a `concurrent.futures.Future`. The coordinator should use:

```python
queue.Queue(maxsize=max_pending)
threading.Condition or queue blocking primitives
threading.Thread(daemon=True)
```

Submission must use non-blocking `put_nowait()` and translate `queue.Full` to `RerankerOverloadedError`. The dispatcher must wait for the first item, collect additional work until the configured deadline or request/pair limit, and remove only complete requests from the queue.

For each batch:

```python
flat_pairs = [
    (item.query, passage["content"])
    for item in batch
    for passage in item.passages
]
scores = engine.score_pairs(
    flat_pairs,
    inference_batch_size=self.inference_batch_size,
)
```

Validate `len(scores) == len(flat_pairs)` before assigning any score. Partition with a running offset and each item's exact passage count. For each item, create fresh dictionaries, apply the existing sigmoid normalization, sort descending, and truncate to that item's `top_k`. Resolve or fail its Future. On any batch exception, fail every Future in that batch.

Use a sentinel or explicit closed flag for shutdown. `close()` must stop intake, fail or drain pending work deterministically, join the dispatcher, and be safe when called repeatedly. `stats()` must return a copy of counters and never include query/content data.

- [x] **Step 4: Run the focused tests and confirm they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_reranker.py -q
```

Expected: all reranker unit tests PASS without loading the real model.

---

### Task 3: Integrate batching into `LocalReranker` with rollback behavior

**Files:**
- Modify: `backend/reranker.py`
- Test: `tests/test_reranker.py`

**Interfaces:**
- Consumes: the coordinator from Task 2 and `RerankingEngine.score_pairs()` from Task 1.
- Produces: `LocalReranker(engine=None, max_concurrency=2, device="auto", model_name=..., batching_enabled=True, batch_max_wait_ms=5, batch_max_requests=16, batch_max_pairs=256, inference_batch_size=16, batch_max_pending=1024)` with the existing `rerank()` method.

- [x] **Step 1: Write failing integration tests**

Add:

```python
def test_local_reranker_uses_score_pairs_when_batching_is_enabled():
    # Fake engine implements score_pairs but raises if rerank() is called.
    # Assert LocalReranker returns the expected result and one flattened call
    # reaches score_pairs with the configured inference batch size.


def test_local_reranker_disabled_batching_uses_direct_semaphore_path():
    # Fake engine exposes only rerank(). Assert exactly the existing direct
    # delegation occurs and no coordinator thread is started.
```

- [x] **Step 2: Run to confirm failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_reranker.py -q
```

Expected: FAIL because `LocalReranker` has no batching options or coordinator integration.

- [x] **Step 3: Implement the adapter integration**

Keep lazy engine loading under the existing `_load_lock`. After loading, choose the batching path only when `batching_enabled` is true and the engine has a callable `score_pairs` attribute. Otherwise use the existing semaphore-protected `engine.rerank()` path.

For injected engines with `score_pairs`, the coordinator should use the same engine instance. For lazy production engines, construct the coordinator only after the engine is loaded, or provide the coordinator a loader callback that resolves the engine exactly once under `_load_lock`; do not load Torch at `LocalReranker` construction time.

Ensure empty passages return before coordinator creation/submission. Ensure `close()` handles the coordinator-not-created case and `stats()` returns zeroed/direct-path data when batching is disabled.

- [x] **Step 4: Run focused tests and the existing direct-concurrency regression**

Run:

```bash
.venv/bin/python -m pytest tests/test_reranker.py -q
```

Expected: PASS, including existing lazy-load/device/fallback/semaphore tests.

---

### Task 4: Add settings, factory wiring, and lifecycle shutdown

**Files:**
- Modify: `config.py`
- Modify: `.env.example`
- Modify: `app_factory.py`
- Test: `tests/test_config.py`
- Test: `tests/test_factory.py`

**Interfaces:**
- Consumes: `LocalReranker` batching constructor from Task 3.
- Produces: six typed settings and `RERANK_BATCHING_ENABLED` rollback configuration; factory-created reranker receives all values; app lifespan closes local batching resources.

- [x] **Step 1: Write failing configuration and factory tests**

Add tests for defaults, overrides, positive-value validation, and the candidate-pool relationship:

```python
def test_rerank_batching_defaults_are_safe():
    s = Settings.from_env({})
    assert s.rerank_batching_enabled is True
    assert s.rerank_batch_max_wait_ms == 5
    assert s.rerank_batch_max_requests == 16
    assert s.rerank_batch_max_pairs == 256
    assert s.rerank_inference_batch_size == 16
    assert s.rerank_batch_max_pending == 1024


def test_rerank_batching_overrides_are_parsed():
    s = Settings.from_env({
        "RERANK_BATCHING_ENABLED": "false",
        "RERANK_BATCH_MAX_WAIT_MS": "8",
        "RERANK_BATCH_MAX_REQUESTS": "4",
        "RERANK_BATCH_MAX_PAIRS": "64",
        "RERANK_INFERENCE_BATCH_SIZE": "16",
        "RERANK_BATCH_MAX_PENDING": "32",
        "RERANK_CANDIDATE_POOL": "50",
    })
    assert s.rerank_batching_enabled is False
    assert s.rerank_batch_max_wait_ms == 8
    assert s.rerank_batch_max_requests == 4
    assert s.rerank_batch_max_pairs == 64
    assert s.rerank_inference_batch_size == 16
    assert s.rerank_batch_max_pending == 32


def test_rerank_batch_pairs_must_cover_candidate_pool():
    s = Settings.from_env({"RERANK_BATCH_MAX_PAIRS": "8", "RERANK_CANDIDATE_POOL": "15"})
    with pytest.raises(ValueError, match="RERANK_BATCH_MAX_PAIRS"):
        s.validate_for_environment()
```

Add a factory test that builds a local reranker from overrides and asserts every constructor setting is present on the created object. Add a lifecycle test using a fake dependency/reranker if the existing factory test harness supports it; otherwise test the small close hook directly without constructing a real model.

- [x] **Step 2: Run to confirm failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py tests/test_factory.py -q
```

Expected: FAIL because the settings and factory arguments do not exist.

- [x] **Step 3: Implement configuration and wiring**

Add to `Settings` near the existing reranker fields:

```python
rerank_batching_enabled: bool = True
rerank_batch_max_wait_ms: int = 5
rerank_batch_max_requests: int = 16
rerank_batch_max_pairs: int = 256
rerank_inference_batch_size: int = 16
rerank_batch_max_pending: int = 1024
```

Parse the boolean with `_bool` and positive integers with `_int`. Validate `rerank_batch_max_pairs >= rerank_candidate_pool` with a specific error. Add the six documented variables to `.env.example`, including the rollback switch and the fact that batching is local-process only.

Update `_build_reranker()` to pass all fields to `LocalReranker`. In the app lifespan's `finally` block, call `close()` on the configured reranker when it exposes a callable close method, before closing the queue. Preserve the remote wrapper behavior; only the local fallback may own a batching thread.

- [x] **Step 4: Run focused tests and lint**

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py tests/test_factory.py -q
.venv/bin/ruff check backend/reranker.py backend/rag_engine.py config.py app_factory.py tests/test_reranker.py tests/test_config.py tests/test_factory.py
```

Expected: PASS and no Ruff findings.

---

### Task 5: Run deterministic equivalence and integration verification

**Files:**
- Test: add or modify the existing deterministic retrieval test location identified during implementation.
- Modify only if needed: `tests/test_rag_engine.py`, `tests/test_postgres_store.py`, or a focused new test file.

**Interfaces:**
- Consumes: the complete local batching implementation.
- Produces: evidence that batching does not alter the returned top-k passage identities/order and that the existing vector-store callers remain unchanged.

- [x] **Step 1: Add a deterministic equivalence test**

Use the same fake scoring behavior to run the retrieval path with a batching-enabled local reranker and a batching-disabled local reranker. Assert returned passage IDs and order are identical, input passage structures remain unchanged, and any score difference is within the documented floating-point tolerance. If the real local model is available, prepare the 19-golden-query comparison as a manual/recorded check rather than making CI depend on model files.

- [x] **Step 2: Run focused retrieval tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_reranker.py tests/test_rag_engine.py tests/test_store_rerank_pool.py -q
```

Expected: PASS with no model download or network requirement.

- [x] **Step 3: Run the full verification suite**

Run:

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check .
.venv/bin/python scripts/run_foundation_gates.py --mode gate
.venv/bin/python -m compileall -q backend app_factory.py config.py
.venv/bin/python eval/parity_runner.py
```

Expected: full suite passes, Ruff is clean, foundation gates report zero blocking findings, compilation succeeds, and parity remains at or above the documented tolerance.

---

### Task 6: Measure the optimization and update project memory

**Files:**
- Modify: `docs/enterprise-migration.md`
- Modify: `CLAUDE.md`
- Modify if needed: `README.md`, `.env.example`

**Interfaces:**
- Consumes: the verified implementation and the existing retrieval load-test harness.
- Produces: a reproducible comparison and honest Phase 6.1 status; no claim of success without measured evidence.

- [x] **Step 1: Run the same controlled load with batching disabled**

Use the existing retrieval load harness and record:

- model/device and candidate pool;
- request count, duration, and request mix;
- single-request and concurrent p50/p95/p99;
- throughput and error rate;
- current `LocalReranker.stats()` counters.

- [x] **Step 2: Run the same load with batching enabled**

Repeat with identical corpus, model/device, request mix, duration, and concurrency. Record coalescing rate, average pairs per model batch, pending depth, overload count, and model-batch failures alongside the latency metrics.

- [x] **Step 3: Compare against acceptance criteria**

Accept the optimization only when deterministic equivalence passes, focused/full verification passes, errors remain zero, single-request p95 regresses by no more than 10%, and concurrent p95 improves repeatably. If concurrent p95 does not improve, retain the implementation only if the feature can be disabled safely and document the negative result rather than presenting batching as a performance fix.

- [x] **Step 4: Update the engineering guide and migration record**

Append the actual benchmark conditions/results and any failure or tuning lesson to `docs/enterprise-migration.md` and `docs/build-history.md`. Update `CLAUDE.md` Current State, Known Issues, and What's Next to reflect the measured result. Keep the feasibility-probe result distinct from the production-load result, and do not alter historical eval scores without a new full evaluation.

- [x] **Step 5: Final diff and status review**

Run:

```bash
git diff --check
git status --short
```

Confirm only the approved spec/plan and implementation/docs files changed; preserve the pre-existing untracked `AGENTS.md` and do not commit unrelated artifacts.
