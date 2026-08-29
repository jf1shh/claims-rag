# Phase 5.1 — Inference Gateway Client + Model Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded local LM Studio call in the agentic router with a provider-neutral `ChatClient` seam that targets any OpenAI-compatible endpoint (LM Studio locally, vLLM/TGI/SGLang/gateway in production) with per-stage model routing and secrets-injected auth, all verified hermetically.

**Architecture:** A `ChatClient` ABC (`models()`, `complete()`) backed by a single `OpenAICompatibleClient` that POSTs to the allowlisted `LLM_BASE_URL` `/v1/chat/completions` with a per-stage `model`. `app_factory._build_llm_client` builds it from Settings (mirroring the existing blob/queue/auth/audit/rate-limit builders); the router consumes the injected client and drops its hardcoded URL/`requests.post` and its in-router model cache.

**Tech Stack:** Python 3.12, FastAPI, PyJWT-agnostic plain HTTP (`requests` via an injectable duck-typed `http`), pytest.

**Spec:** `docs/superpowers/specs/2026-08-29-phase5-inference-gateway-design.md`

## Global Constraints

- **No real network in CI.** All tests inject a duck-typed `http` stub / fake server; never let a test hit a real LLM endpoint.
- **SSRF:** the request `engine` field must never influence the request URL. `LLM_BASE_URL` is server-set. Unknown engines are rejected with zero outbound calls.
- **No ML stack at import time.** `backend/llm_client.py` must not import `sentence_transformers`/torch. The test suite stays torch-free (fake embedder, fake HTTP).
- **Config validation** counts: every added env var is validated exactly like the existing S3/SQS/auth pattern; production still rejects `development` auth, etc.
- **Standing workflow:** commit + PR + CI on the self-hosted runner after the milestone; run the full suite (currently 325 passed / 12 skipped) plus `ruff check .` and gates before committing.

---

### Task 1: `ChatClient` ABC, `ChatClientError`, and the `FakeHTTP` test double

**Files:**
- Create: `backend/llm_client.py`
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `class ChatClient(ABC)` with `models() -> list[str]` and `complete(messages, *, model, temperature, max_tokens) -> str`; `class ChatClientError(Exception)`. Used by Tasks 2–6.

- [ ] **Step 1: Write the failing test** (`tests/test_llm_client.py`)

```python
import pytest

from backend.llm_client import ChatClient, ChatClientError


def test_chat_client_is_abstract_and_contract_is_shape_safe():
    assert issubclass(ChatClient, object)
    for name in ("models", "complete"):
        assert hasattr(ChatClient, name)


def test_chat_client_error_is_an_exception():
    assert issubclass(ChatClientError, Exception)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_llm_client.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.llm_client'`.

- [ ] **Step 3: Minimal implementation** (`backend/llm_client.py`)

```python
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Callable, Iterator


class ChatClientError(Exception):
    """A completion/transport failure from an LLM backend. Callers fall back
    to simulation on this; never leak raw exception text to clients."""


class ChatClient(ABC):
    """Contract for calling an LLM for one stage of the agentic pipeline.
    Provider-neutral: any OpenAI-compatible endpoint (LM Studio local dev,
    vLLM / TGI / SGLang / a gateway in production) implements this."""

    @abstractmethod
    def models(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        raise NotImplementedError
```

- [ ] **Step 4: Run tests — PASS**

Run: `.venv/bin/python -m pytest tests/test_llm_client.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_client.py tests/test_llm_client.py
git commit -m "feat(llm): add ChatClient seam + ChatClientError"
```

---

### Task 2: `OpenAICompatibleClient` with per-stage model resolution and auth

**Files:**
- Modify: `backend/llm_client.py` (add `OpenAICompatibleClient` + `_DefaultHTTP`)
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Consumes: `ChatClient`, `ChatClientError`, the injectable `http` duck (`.get(url, timeout=)` / `.post(url, json=, headers=, timeout=, stream=)`).
- Produces: `OpenAICompatibleClient` with `models()`, `complete()`, `model_for_stage(stage)`, and a helper `build_openai_compatible(base_url, default_model, ...)` used by `app_factory` in Task 4.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_llm_client.py`)

```python
import pytest

from backend.llm_client import OpenAICompatibleClient, ChatClientError


class FakeHTTP:
    """Recording stub: fulfills the duck-typed http contract."""
    def __init__(self):
        self.posts = []
        self.gets = []
        self._post_resp = None
        self._models_resp = None

    def post(self, url, json=None, headers=None, timeout=None, **kw):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if callable(self._post_resp):
            return self._post_resp()
        return _FakeResponse(self._post_resp)

    def get(self, url, timeout=None, **kw):
        self.gets.append({"url": url, "timeout": timeout})
        return _FakeResponse(self._models_resp)


class _FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload
        self.status_code = status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")
    def json(self):
        if callable(self._payload):
            return self._payload()
        return self._payload or {}


def _completion_body(content="hi there"):
    return {"choices": [{"message": {"content": content}}]}


def test_complete_posts_to_chat_completions_with_stage_model():
    http = FakeHTTP()
    http._post_resp = _completion_body("plan ok")
    client = OpenAICompatibleClient(
        base_url="http://127.0.0.1:1234", default_model="M",
        planning_model="fast", synthesis_model="domain",
        http=http,
    )
    out = client.complete(
        [{"role": "user", "content": "q"}],
        model=client.model_for_stage("planning"),
        temperature=0.0, max_tokens=150,
    )
    assert out == "plan ok"
    p = http.posts[0]
    assert p["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert p["json"]["model"] == "fast"
    assert p["json"]["temperature"] == 0.0 and p["json"]["max_tokens"] == 150
    assert p["json"]["messages"] == [{"role": "user", "content": "q"}]


def test_model_for_stage_resolves_all_stages_and_fallback():
    client = OpenAICompatibleClient(
        base_url="http://x", default_model="D",
        planning_model="P", synthesis_model="S", eval_model=None,
        http=FakeHTTP(),
    )
    assert client.model_for_stage("planning") == "P"
    assert client.model_for_stage("synthesis") == "S"
    assert client.model_for_stage("eval") == "D"   # unset stage → default
    client2 = OpenAICompatibleClient(base_url="http://x", default_model="only", http=FakeHTTP())
    assert client2.model_for_stage("synthesis") == "only"


def test_complete_sends_bearer_only_when_api_key_configured():
    http = FakeHTTP(); http._post_resp = _completion_body("x")
    client = OpenAICompatibleClient(base_url="http://x", default_model="M",
                                    api_key="secret", http=http)
    client.complete([{"role":"user","content":"q"}], model="M", temperature=0.1, max_tokens=10)
    assert http.posts[0]["headers"]["Authorization"] == "Bearer secret"
    http2 = FakeHTTP(); http2._post_resp = _completion_body("x")
    client2 = OpenAICompatibleClient(base_url="http://x", default_model="M", http=http2)
    client2.complete([{"role":"user","content":"q"}], model="M", temperature=0.1, max_tokens=10)
    assert "Authorization" not in http2.posts[0]["headers"]


def test_complete_raises_on_http_error_missing_content():
    http = FakeHTTP(); http._post_resp = _FakeResponse({"choices": []}, status=200)
    client = OpenAICompatibleClient(base_url="http://x", default_model="M", http=http)
    with pytest.raises(ChatClientError):
        client.complete([{"role":"user","content":"q"}], model="M", temperature=0.1, max_tokens=10)


def test_models_returns_first_and_ttl_caches():
    http = FakeHTTP()
    http._models_resp = {"data": [{"id": "served-model"}]}
    client = OpenAICompatibleClient(base_url="http://x", default_model="M",
                                    models_ttl_seconds=600, http=http)
    assert client.models() == ["served-model"]
    assert client.models() == ["served-model"]
    assert len(http.gets) == 1  # cached, no second round-trip


def test_models_failure_returns_local_model_without_caching():
    http = FakeHTTP(); http._models_resp = {"data": []}
    client = OpenAICompatibleClient(base_url="http://x", default_model="M",
                                    models_ttl_seconds=600, http=http)
    assert client.models() == ["local-model"]
    http._models_resp = {"data": [{"id": "now-up"}]}
    assert client.models() == ["now-up"]  # failure wasn't cached
```

- [ ] **Step 2: Run to confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_llm_client.py -q`
Expected: FAIL (`OpenAICompatibleClient` not defined / attribute errors).

- [ ] **Step 3: Implement** (append to `backend/llm_client.py`)

```python
class _DefaultHTTP:
    """Real transport. Replaced by a recording stub in tests."""
    def post(self, url, json=None, headers=None, timeout=None, **kw):
        import requests
        return requests.post(url, json=json, headers=headers, timeout=timeout, **kw)
    def get(self, url, timeout=None, **kw):
        import requests
        return requests.get(url, timeout=timeout, **kw)


class OpenAICompatibleClient(ChatClient):
    STAGES = ("planning", "synthesis", "eval")

    def __init__(self, base_url, default_model, planning_model=None,
                 synthesis_model=None, eval_model=None, api_key=None,
                 timeout=120.0, plan_timeout=5.0, models_ttl_seconds=10.0,
                 http=None, clock: Callable[[], float] = time.monotonic):
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.planning_model = planning_model
        self.synthesis_model = synthesis_model
        self.eval_model = eval_model
        self.api_key = api_key
        self.timeout = timeout
        self.plan_timeout = plan_timeout
        self.models_ttl_seconds = models_ttl_seconds
        self._http = http or _DefaultHTTP()
        self._clock = clock
        self._models_cache: list[str] | None = None
        self._models_at = -float("inf")

    def model_for_stage(self, stage: str) -> str:
        return {
            "planning": self.planning_model,
            "synthesis": self.synthesis_model,
            "eval": self.eval_model,
        }.get(stage) or self.default_model

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def complete(self, messages, *, model, temperature, max_tokens):
        timeout = self.plan_timeout if model == self.planning_model else self.timeout
        try:
            resp = self._http.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                headers=self._headers(),
                timeout=timeout,
            )
            resp.raise_for_status()
            try:
                return resp.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise ChatClientError("LLM response missing content") from exc
        except ChatClientError:
            raise
        except Exception as exc:
            raise ChatClientError(f"LLM request failed") from exc

    def models(self) -> list[str]:
        now = self._clock()
        if self._models_cache is not None and (now - self._models_at) < self.models_ttl_seconds:
            return self._models_cache
        try:
            resp = self._http.get(f"{self.base_url}/v1/models", timeout=2.0)
            if resp.status_code == 200 and resp.json().get("data"):
                self._models_cache = [resp.json()["data"][0]["id"]]
                self._models_at = now
                return self._models_cache
        except Exception:
            pass
        # Do not cache a failure: a transient outage self-heals next call.
        return ["local-model"]
```

- [ ] **Step 4: Run tests — PASS**

Run: `.venv/bin/python -m pytest tests/test_llm_client.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_client.py tests/test_llm_client.py
git commit -m "feat(llm): OpenAICompatibleClient with per-stage models, auth, TTL cache"
```

---

### Task 3: Config surface + validation

**Files:**
- Modify: `config.py`, `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings` fields `planning_model`, `synthesis_model`, `eval_model`, `llm_api_key`, `llm_synthesis_timeout_seconds`, `llm_plan_timeout_seconds`, `model_cache_ttl_seconds` (existing `llm_provider`/`llm_base_url`/`llm_model` retained). Used by Task 4.

- [ ] **Step 1: Write failing tests** (append to `tests/test_config.py`)

```python
def test_given_stage_models_when_parsed_then_catalog_is_configured():
    s = Settings.from_env({
        "PLANNING_MODEL": "fast-plan",
        "SYNTHESIS_MODEL": "domain-v2",
        "EVAL_MODEL": "judge-1",
        "LLM_API_KEY": "sk-test",
        "LLM_PLAN_TIMEOUT_SECONDS": "3",
        "LLM_SYNTHESIS_TIMEOUT_SECONDS": "90",
        "MODEL_CACHE_TTL_SECONDS": "20",
    })
    assert s.planning_model == "fast-plan"
    assert s.synthesis_model == "domain-v2"
    assert s.eval_model == "judge-1"
    assert s.llm_api_key == "sk-test"
    assert s.llm_plan_timeout_seconds == 3
    assert s.llm_synthesis_timeout_seconds == 90
    assert s.model_cache_ttl_seconds == 20


def test_given_no_stage_models_then_defaults_fall_back_to_single_model():
    s = Settings.from_env({"LLM_MODEL": "fallback-model"})
    assert s.planning_model is None
    assert s.synthesis_model is None
    assert s.eval_model is None
    assert s.llm_api_key is None
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: FAIL (no such Settings fields).

- [ ] **Step 3: Implement**

In `config.py`, within the `Settings` dataclass (near the existing LLM fields):

```python
    llm_provider: str = "lm-studio"
    llm_base_url: str = "http://127.0.0.1:1234"
    llm_model: str | None = None
    planning_model: str | None = None       # Phase 5.1 per-stage routing
    synthesis_model: str | None = None
    eval_model: str | None = None
    llm_api_key: str | None = None           # injected from env/secrets; never client-supplied
    llm_synthesis_timeout_seconds: int = 120
    llm_plan_timeout_seconds: int = 5
    model_cache_ttl_seconds: int = 10
```

And in `from_env`:

```python
            llm_model=env.get("LLM_MODEL") or None,
            planning_model=env.get("PLANNING_MODEL") or None,
            synthesis_model=env.get("SYNTHESIS_MODEL") or None,
            eval_model=env.get("EVAL_MODEL") or None,
            llm_api_key=env.get("LLM_API_KEY") or None,
            llm_synthesis_timeout_seconds=_int(env.get("LLM_SYNTHESIS_TIMEOUT_SECONDS"), 120, "LLM_SYNTHESIS_TIMEOUT_SECONDS"),
            llm_plan_timeout_seconds=_int(env.get("LLM_PLAN_TIMEOUT_SECONDS"), 5, "LLM_PLAN_TIMEOUT_SECONDS"),
            model_cache_ttl_seconds=_int(env.get("MODEL_CACHE_TTL_SECONDS"), 10, "MODEL_CACHE_TTL_SECONDS"),
```

In `validate_for_environment`, confirm `llm_base_url` is required for non-`none` providers (already enforced for `openai-compatible`):

```python
        if self.app_env == "production" and self.llm_provider == "openai-compatible" and not self.llm_base_url:
            raise ValueError("LLM_BASE_URL is required for openai-compatible production")
```

Append to `.env.example` under the existing LLM block:

```ini
# Phase 5.1: per-stage model catalog against the allowlisted LLM_BASE_URL
# (LM Studio locally; a private vLLM/TGI/SGLang gateway in production).
PLANNING_MODEL=            # fast model for query decomposition (else LLM_MODEL)
SYNTHESIS_MODEL=           # bought/built domain model for the final answer (else LLM_MODEL)
EVAL_MODEL=                # optional judge model (else LLM_MODEL)
LLM_API_KEY=               # Bearer credential from secrets; never in .env
LLM_SYNTHESIS_TIMEOUT_SECONDS=120
LLM_PLAN_TIMEOUT_SECONDS=5
MODEL_CACHE_TTL_SECONDS=10
```

- [ ] **Step 4: Run tests — PASS**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS (all existing config tests still pass).

- [ ] **Step 5: Commit**

```bash
git add config.py .env.example tests/test_config.py
git commit -m "feat(llm): per-stage model catalog + LLM credential/timeout config"
```

---

### Task 4: `_build_llm_client` in `app_factory.py`

**Files:**
- Modify: `app_factory.py`
- Test: `tests/test_factory.py` (new, small)

**Interfaces:**
- Consumes: `Settings` fields from Task 3; `OpenAICompatibleClient` from Task 2.
- Produces: `_build_llm_client(settings) -> OpenAICompatibleClient | None` and `AppDependencies.llm_client`. Used by Task 5/6.

- [ ] **Step 1: Write failing test** (`tests/test_factory.py`)

```python
from config import Settings
from app_factory import _build_llm_client, AppDependencies


def test_build_llm_client_returns_none_for_none_provider():
    s = Settings.from_env({"LLM_PROVIDER": "none"})
    assert _build_llm_client(s) is None


def test_build_llm_client_uses_stage_models_from_settings():
    s = Settings.from_env({"PLANNING_MODEL": "p", "SYNTHESIS_MODEL": "sy", "LLM_BASE_URL": "http://gw"})
    client = _build_llm_client(s)
    assert client is not None
    assert client.base_url == "http://gw"
    assert client.model_for_stage("planning") == "p"
    assert client.model_for_stage("synthesis") == "sy"


def test_app_dependencies_expose_llm_client():
    from app_factory import build_dependencies
    deps = build_dependencies(Settings.from_env({"LLM_PROVIDER": "none"}))
    assert deps.llm_client is None
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_factory.py -q`
Expected: FAIL (`_build_llm_client` not defined).

- [ ] **Step 3: Implement** (in `app_factory.py`)

Add `llm_client` to `AppDependencies`:

```python
    audit_sink: Any | None = None
    rate_limiter: Any | None = None
    llm_client: Any | None = None
```

Add the builder (after `_build_rate_limiter`):

```python
def _build_llm_client(settings: Settings):
    """Provider-neutral LLM client (Phase 5.1). ``LLM_PROVIDER=none`` disables
    the online path (simulation only); otherwise builds an OpenAI-compatible
    client for the allowlisted LLM_BASE_URL with per-stage models. Mirrors the
    other builders: config selects the provider, this returns the adapter."""
    if settings.llm_provider in (None, "none"):
        return None
    from backend.llm_client import OpenAICompatibleClient
    return OpenAICompatibleClient(
        base_url=settings.llm_base_url,
        default_model=settings.llm_model or "local-model",
        planning_model=settings.planning_model,
        synthesis_model=settings.synthesis_model,
        eval_model=settings.eval_model,
        api_key=settings.llm_api_key,
        timeout=float(settings.llm_synthesis_timeout_seconds),
        plan_timeout=float(settings.llm_plan_timeout_seconds),
        models_ttl_seconds=float(settings.model_cache_ttl_seconds),
    )
```

And in `build_dependencies`, set `llm_client=_build_llm_client(settings)` in the returned `AppDependencies`.

- [ ] **Step 4: Run tests — PASS**

Run: `.venv/bin/python -m pytest tests/test_factory.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app_factory.py tests/test_factory.py
git commit -m "feat(llm): build ChatClient from settings in app_factory"
```

---

### Task 5: Adapt the agentic router to the injected client

**Files:**
- Modify: `backend/agentic_router.py`
- Test: `tests/test_agentic_router_llm.py` (new)

**Interfaces:**
- Consumes: `ChatClient`/`ChatClientError` (Task 2), `build_llm_client`'s client.
- Produces: `run_query(..., llm_client=None)`, `_run_online_agent(..., llm_client, ...)`, `_get_llm_plan(..., llm_client, ...)`. Consumed by Task 6 and 5.3.

- [ ] **Step 1: Write failing tests** (`tests/test_agentic_router_llm.py`)

Use deterministic fakes so the real online path runs without ML/network:

```python
import pytest
from backend.agentic_router import AgenticRAGRouter
from backend.llm_client import ChatClient, ChatClientError


class RecordingClient(ChatClient):
    def __init__(self):
        self.plans = []
        self.synthesis = []
        self.models_calls = 0
    def models(self):
        self.models_calls += 1
        return ["M"]
    def complete(self, messages, *, model, temperature, max_tokens):
        record = {"model": model, "temperature": temperature,
                  "max_tokens": max_tokens, "messages": messages}
        if "Claims Planner" in messages[0]["content"]:
            self.plans.append(record)
            return '{"needs_global_policies": true, "needs_claim_dossier": false, ' \
                   '"sub_queries": ["labor"]}'
        self.synthesis.append(record)
        return "The Nevada mechanical cap is $110/hr."


class FakeVectorStore:
    def search_similarity(self, qe, qt, claim_id=None, reranking_engine=None, top_k=4, **kw):
        return [{"id": 1, "content": "Nevada mechanical labor cap is $110.",
                "filename": "labor.txt", "file_type": "txt", "score": 0.9},
               {"id": 2, "content": "California mechanical cap is $120.",
                "filename": "labor2.txt", "file_type": "txt", "score": 0.7}]
    def get_claim_chunks(self, claim_id):
        return []
    def get_all_documents(self):
        return []


class FakeEmbedder:
    def embed_query(self, q):
        return [0.0] * 384


def test_online_path_uses_per_stage_models_and_returns_answer():
    router = AgenticRAGRouter()
    client = RecordingClient()
    result = router.run_query(
        query_text="what is the labor cap?",
        claim_id=None,
        engine="lm-studio",
        embedding_engine=FakeEmbedder(),
        vector_store=FakeVectorStore(),
        reranking_engine=None,
        llm_client=client,
    )
    assert result["answer"] == "The Nevada mechanical cap is $110/hr."
    assert len(client.plans) == 1
    assert len(client.synthesis) == 1
    assert client.plans[0]["model"] == "M"
    assert client.plans[0]["temperature"] == 0.0
    assert client.plans[0]["max_tokens"] == 150
    assert client.synthesis[0]["model"] == "M"
    assert client.synthesis[0]["temperature"] == 0.1
    assert client.synthesis[0]["max_tokens"] == 1000
    assert "Nevada" in result["answer"]


def test_engine_allowlist_rejects_unknown_without_call():
    router = AgenticRAGRouter()
    client = RecordingClient()
    result = router.run_query(
        query_text="q", claim_id=None, engine="https://evil.example/x",
        embedding_engine=FakeEmbedder(), vector_store=FakeVectorStore(),
        reranking_engine=None, llm_client=client,
    )
    assert "Unknown engine" in result["answer"]
    assert client.plans == [] and client.synthesis == []
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_agentic_router_llm.py -q`
Expected: FAIL (`run_query` has no `llm_client` param; `_run_simulated_agent` returns default text instead).

- [ ] **Step 3: Implement**

In `backend/agentic_router.py`:

Import `ChatClientError`:

```python
from backend.llm_client import ChatClientError
```

Change `run_query` signature and the online branch. Delete `_get_loaded_model`, `_model_cache`, `_model_cache_at`, and `MODEL_CACHE_TTL_SECONDS` (moved into the client):

```python
    def run_query(
        self,
        query_text: str,
        claim_id: Optional[str],
        engine: str,
        embedding_engine: Any,
        vector_store: Any,
        reranking_engine: Any = None,
        llm_client: Any = None,
    ) -> Dict[str, Any]:
        ...
        if engine == "simulated":
            return self._run_simulated_agent(query_text, claim_id, vector_store, embedding_engine, reranking_engine, logs, start_time)
        if llm_client is None:
            return self._run_simulated_agent(query_text, claim_id, vector_store, embedding_engine, reranking_engine, logs, start_time)
        if engine != "lm-studio":
            logs.append(f"❌ [Config Error] Unknown engine '{engine}'. Only 'simulated' and 'lm-studio' are supported.")
            return {
                "answer": f"Unknown engine '{engine}'. Please select 'simulated' or 'lm-studio'.",
                "sources": [], "claim_dossier": None, "engine": engine, "pipeline_logs": logs,
            }
        return self._run_online_agent(query_text, claim_id, vector_store, embedding_engine, reranking_engine, logs, start_time, llm_client)
```

Change `_run_online_agent` signature (drop `engine_url`) and the synthesis block:

```python
    def _run_online_agent(self, query_text, claim_id, vector_store, embedding_engine,
                          reranking_engine, logs, start_time, llm_client):
        ...
        model_name = llm_client.models()[0]
        plan = self._get_llm_plan(query_text, claim_id, llm_client, model_name)
        ...
        try:
            answer = llm_client.complete(
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": user_prompt}],
                model=llm_client.model_for_stage("synthesis"),
                temperature=0.1,
                max_tokens=1000,
            )
        except ChatClientError as e:
            logs.append(f"❌ [Synthesis Error] LLM generation failed: {e}. Falling back to simulation.")
            return self._run_simulated_agent(query_text, claim_id, vector_store, embedding_engine, reranking_engine, logs, start_time)
```

Change `_get_llm_plan` to use the client:

```python
    def _get_llm_plan(self, query_text, claim_id, llm_client, model_name):
        ...
        try:
            text = llm_client.complete(
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": user_prompt}],
                model=llm_client.model_for_stage("planning"),
                temperature=0.0,
                max_tokens=150,
            )
            ...unchanged JSON extraction to `parsed`...
        except ChatClientError:
            pass
        except Exception:
            pass
        return fallback_plan
```

(The `needs_claim_dossier` fallback logic and `fallback_plan` construction stay as-is; only the transport changes.)

- [ ] **Step 4: Run tests — PASS**

Run: `.venv/bin/python -m pytest tests/test_agentic_router_llm.py tests/test_agentic_router.py -q` (and the existing router suite)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/agentic_router.py tests/test_agentic_router_llm.py
git commit -m "feat(llm): route planner+synthesis through injected ChatClient; engine stays allowlist"
```

---

### Task 6: Wire the client into `app.py` + `/api/status`

**Files:**
- Modify: `backend/app.py`
- Test: `tests/test_api_status.py` (new/appended)

**Interfaces:**
- Consumes: `_build_llm_client` (Task 4), router `run_query(llm_client=...)` (Task 5).
- Produces: module `_llm_client`, `/api/chat` passes it, `/api/status` uses `client.models()`.

- [ ] **Step 1: Write failing test** (`tests/test_api_status.py`)

```python
from fastapi.testclient import TestClient
import backend.app as app_module
from backend.app import app
from backend.llm_client import OpenAICompatibleClient


def test_status_reports_gateway_models_via_client(monkeypatch):
    class FakeHTTP:
        def __init__(self): self.gets = []
        def post(self, *a, **k): raise NotImplementedError
        def get(self, url, timeout=None, **k):
            self.gets.append(url)
            class R:
                status_code = 200
                def json(self): return {"data": [{"id": "qwen2.5-14b"}]}
            return R()
    client = OpenAICompatibleClient(base_url="http://gw", default_model="M", http=FakeHTTP())
    monkeypatch.setattr(app_module, "_llm_client", client)
    monkeypatch.setattr(app_module, "settings", __import__("config").Settings.from_env({"LLM_PROVIDER": "lm-studio"}))
    resp = TestClient(app).get("/api/status")
    assert resp.status_code == 200
    assert resp.json()["lm_studio"]["models"] == ["qwen2.5-14b"]
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/bin/python -m pytest tests/test_api_status.py -q`
Expected: FAIL (`app_module._llm_client` not present / status does not use client).

- [ ] **Step 3: Implement**

In `backend/app.py`:

```python
from app_factory import _build_blob_store, _build_authenticator, _build_claim_access_policy, _build_audit_sink, _build_rate_limiter, _build_llm_client  # noqa: E402
...
_llm_client = _build_llm_client(settings)
```

Replace `get_status`'s LLM section:

```python
@app.get("/api/status", dependencies=[Depends(get_current_tenant)])
def get_status():
    models = _llm_client.models() if _llm_client is not None else []
    lm_studio_models = models or []
    lm_studio_active = _llm_client is not None
    return {
        "lm_studio": {
            "active": lm_studio_active,
            "url": f"{settings.llm_base_url}/v1",
            "models": lm_studio_models,
        },
        "database": {... unchanged ...},
        "ingestion": {"mode": settings.ingestion_mode},
    }
```

`get_loaded_models` may remain (now unused) or be removed; prefer removing it and its import of `requests` usage in `get_status` only. Pass `llm_client=_llm_client` into the `/api/chat` `agentic_router.run_query(...)` call.

- [ ] **Step 4: Run tests — PASS**

Run: `.venv/bin/python -m pytest tests/test_api_status.py tests/test_api_chat_stream.py tests/test_api_audit.py tests/test_api_rbac.py -q` (chat remains simulation when `_llm_client=None`)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app.py tests/test_api_status.py
git commit -m "feat(llm): wire ChatClient into app.py + /api/status; /api/chat uses it"
```

---

### Task 7: Full verification + docs

**Files:** none new; run everything; update `CLAUDE.md`/`docs/enterprise-migration.md`.

- [ ] **Step 1: Full suite + lint + gates**

Run:
```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check .
.venv/bin/python scripts/run_foundation_gates.py --mode gate
.venv/bin/python -m compileall -q backend app_factory.py config.py
```
Expected: full suite **≥ 325 passed / 12 skipped**, ruff clean, gates 0 blocking, compileall clean.

- [ ] **Step 2: update migration doc** — mark milestone 5.1 with a status paragraph; update CLAUDE.md Current State / What's Next / Session Log (Phase 4 → **Done**, next milestone 5.2).

- [ ] **Step 3: Commit**

```bash
git add docs/enterprise-migration.md CLAUDE.md
git commit -m "docs: Phase 5.1 inference gateway seam status"
```

- [ ] **Step 4: Standing workflow** — branch → push → `gh pr create` → watch CI on the self-hosted runner → merge when green.