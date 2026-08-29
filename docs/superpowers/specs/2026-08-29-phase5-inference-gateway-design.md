# Phase 5.1 — Inference Gateway Client + Model Catalog

> **Part of Phase 5 (Serving & LLM layer) of `docs/enterprise-migration.md`.**
> Milestone 5.1: replace the hardcoded local LM Studio call with a
> provider-neutral inference client that targets an enterprise **self-hosted
> GPU gateway** (vLLM / TGI / SGLang, optionally fronted by a gateway such as
> LiteLLM) speaking the OpenAI-compatible `/v1/chat/completions` protocol, with
> **per-stage model routing** so the planner and the synthesis step can use
> different models, an **allowlisted server-side endpoint**, and **credentials
> injected from secrets** (the KMS thread from 4.4).

**Status: Design (draft for review).** Believed date: 2026-08-29.

---

## 1. Context and problem

Today the agentic router talks to exactly one backend, hardcoded:

- `run_query` only accepts `engine == "lm-studio"` and `_run_online_agent` uses a
  literal `engine_url = "http://127.0.0.1:1234"`.
- `_get_llm_plan` and the final synthesis each do their own raw `requests.post`
  to `{engine_url}/v1/chat/completions`.
- `config.py` already declares `llm_provider` / `llm_base_url` / `llm_model`
  **but the router never reads them** — the config surface is dead.

An enterprise insurance deployment does not run the model in LM Studio on a
laptop. It runs a controlled, private **inference tier** on GPU infra (vLLM /
TGI / SGLang) inside its own VPC or on-prem, frequently serving a *portfolio* of
models — including a domain model the company bought or built in-house. Claims
documents are sensitive PII, so the endpoint is isolated and the media is why
the company buys/builds rather than using a public SaaS.

Two consequences drive this design:

1. **Per-stage routing.** The planner (tiny, latency-critical) and the synthesis
   (large, accuracy-critical) should be different models. The config needs a
   model *catalog*, not a single `llm_model`.
2. **Secure, allowlisted endpoint.** The gateway URL is set server-side (never
   derived from a client request — the Phase-10 SSRF constraint becomes the
   enforcement mechanism at enterprise scale) and typically requires
   credentials supplied from environment/secrets, never from the client.

This milestone makes those two things real behind a provider-neutral seam,
verified hermetically against a fake OpenAI-compatible server. Connecting to the
specific bought/built model on the company's GPU platform is deployment wiring,
documented (Section 10) but not buildable here.

## 2. Goals

- Introduce a `ChatClient` seam (`backend/llm_client.py`) that abstracts "talk to
  an LLM for one stage of the agentic pipeline."
- Support **any OpenAI-compatible endpoint as `LLM_BASE_URL`** (LM Studio locally in
  dev, vLLM/TGI/SGLang/gateway in production) with the same client.
- **Per-stage model resolution**: `PLANNING_MODEL`, `SYNTHESIS_MODEL`, optional
  `EVAL_MODEL`, falling back to a single configured model when unset.
- **Allowlisted, authenticated endpoint**: server-set `LLM_BASE_URL`, optional
  `LLM_API_KEY` injected from env/secrets; reject any client-supplied URL.
- Move the **model-cache TTL** (the current 10s `_get_loaded_model`) into the
  client, keyed to the configured endpoint.
- Adapt the agentic router to consume the seam; delete the hardcoded
  `requests.post` and `engine_url` literal.
- All verified hermetically; `engine` remains the strict server-side allowlist.

## 3. Non-goals / out of scope

- **Building, deploying, or testing against a real GPU cluster or a bought/built
  model.** That requires the company's infrastructure and their proprietary
  model. This milestone delivers the *contract* + hermetic proof, and a
  deployment appendix.
- **Streaming** (`complete_stream`, `/api/chat/stream`). That is 5.3. The 5.1
  client defines the non-streaming `complete` path; the streaming method and
  its callers are added in 5.3 (the seam is designed so this is additive).
- **The Reranker service** — that is 5.2.
- Async/worker-pool chat delivery — 5.3 scopes to SSE streaming.

## 4. Target architecture

```
frontend / API (/api/chat)                     app_factory
      │                                            │
      ▼                                            ▼
 AgenticRAGRouter ──injected──► ChatClient ──HTTP──► allowlisted LLM_BASE_URL
   planner stage         OpenAICompatibleClient      (LM Studio | vLLM | TGI |
   synthesis stage              + model catalog           SGLang | gateway)
                                                                 │
                                                        + optional LLM_API_KEY
                                                          (from env/secrets)
```

- The router holds **no URL or model constants**. Its `engine == "lm-studio"`
  path resolves to the injected client's (per-stage) configured model.
- `engine == "simulated"` bypasses the client entirely (unchanged).
- The client is stateless apart from its model cache; the API process stays
  horizontally scalable.

## 5. Components & interfaces

### 5.1 `ChatClient` (Abstract Base Class) — `backend/llm_client.py`

```python
class ChatClient(ABC):
    @abstractmethod
    def models(self) -> list[str]:
        """Models currently served by the backend, newest/first first.
        TTL-cached; used for the /api/status surface and model resolution."""

    @abstractmethod
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """One-shot completion. Returns the assistant message content.
        Raises ChatClientError on transport/HTTP/non-200/parse failures so
        callers (planner/synthesis) can fall back to simulation uniformly."""
```

- `ChatClientError` exception class, distinct from auth/runtime errors.

### 5.2 `OpenAICompatibleClient(ChatClient)`

```python
class OpenAICompatibleClient(ChatClient):
    def __init__(
        self,
        base_url: str,            # allowlisted endpoint, e.g. http://127.0.0.1:1234
        default_model: str,       # fallback model when a stage leaves it unset
        planning_model: str | None = None,
        synthesis_model: str | None = None,
        eval_model: str | None = None,
        api_key: str | None = None,       # injected; sent as Authorization: Bearer
        timeout: float = 120.0,           # synthesis/completion timeout
        plan_timeout: float = 5.0,        # planner timeout
        models_ttl_seconds: float = 10.0,
        http: object | None = None,       # injectable HTTP client for hermetic tests
        clock: Callable[[], float] = time.monotonic,
    ):
        ...
```

- **Per-stage resolution helper** returned for the router:

```python
    def model_for_stage(self, stage: str) -> str:
        """stage in {'planning','synthesis','eval'} →
        planning_model / synthesis_model / eval_model / default_model
        (first non-None wins)."""
```

- `complete()` builds `POST {base_url}/v1/chat/completions` with
  `{"model": <stage model>, "messages": ..., "temperature": ..., "max_tokens": ...}`
  and, when `api_key` is set, header `Authorization: Bearer <api_key>`. Uses
  `plan_timeout` for the planner stage (passed by caller or via a `models`-
  independent `stage` hint) and `timeout` otherwise. Returns
  `choices[0]["message"]["content"]`.
- `models()` calls `GET {base_url}/v1/models`, TTL-caches the first `id`, and
  returns `["local-model"]` on failure **without caching the failure** (same
  semantics as today's `_get_loaded_model`).
- The injected `http` is duck-typed (`.get(url, timeout=)`, `.post(url,
  json=, headers=, timeout=)`); the real default is `requests`. Tests supply a
  recording stub, so no real network ever touches CI.

### 5.3 `build_llm_client(settings)` — `app_factory.py`

Mirrors `_build_blob_store` / `_build_authenticator` / `_build_rate_limiter`:

```python
def _build_llm_client(settings: Settings) -> ChatClient | None:
    if settings.llm_provider in (None, "none"):
        return None  # online path disabled → simulation only
    return OpenAICompatibleClient(
        base_url=settings.llm_base_url,
        default_model=settings.llm_model or "local-model",
        planning_model=settings.planning_model,
        synthesis_model=settings.synthesis_model,
        eval_model=settings.eval_model,
        api_key=settings.llm_api_key,
        timeout=settings.llm_synthesis_timeout_seconds,
        plan_timeout=settings.llm_plan_timeout_seconds,
        models_ttl_seconds=settings.model_cache_ttl_seconds,
    )
```

- `llm_provider` is still bounded to `{"lm-studio", "openai-compatible", "none"}`
  by config validation (unchanged); both non-`none` values use the same client —
  the provider is a config label for the same wire protocol.

### 5.4 Agentic router adaptation — `backend/agentic_router.py`

- `run_query` gains an `llm_client: ChatClient | None` parameter.
  - `engine == "simulated"` or `llm_client is None` → simulation path
    (unchanged).
  - `engine == "lm-studio"` and `llm_client` present → online path.
  - any other `engine` → rejected allowlist error (unchanged, SSRF intact).
- `_run_online_agent(...)` replaces its final-synthesis `requests.post` with:

```python
answer = llm_client.complete(
    [{"role": "system", "content": system_prompt},
     {"role": "user", "content": user_prompt}],
    model=llm_client.model_for_stage("synthesis"),
    temperature=0.1,
    max_tokens=1000,
)
```

- `_get_llm_plan(...)` replaces its planner `requests.post` with the same client,
  using `model_for_stage("planning")`, `temperature=0.0`, `max_tokens=150`, and
  the client's plan timeout. JSON-extraction + per-field coercion logic is
  unchanged.
- `self._get_loaded_model / _model_cache` are **deleted**; the router calls
  `llm_client.models()` instead (plus `models()` is surfaced by `/api/status`).
- Any `ChatClientError` / network exception in either method falls back to the
  existing simulation path (unchanged fallback posture) and logs it.

### 5.5 `backend/app.py` + config

- `app.py` builds the client via `_build_llm_client(settings)` and injects it
  into `agentic_router.run_query` and `get_status` (replacing the inline
  `get_loaded_models` call with `client.models()` when the client is present).
- `config.py` adds (all optional where defaults allow a local-only dev run):

| Env var | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `lm-studio` | `lm-studio` / `openai-compatible` / `none` (existing) |
| `LLM_BASE_URL` | `http://127.0.0.1:1234` | Allowlisted gateway endpoint (existing) |
| `LLM_MODEL` | *(empty → `local-model`)* | Fallback single model (existing) |
| `PLANNING_MODEL` | *(empty → LLM_MODEL)* | Planner stage model |
| `SYNTHESIS_MODEL` | *(empty → LLM_MODEL)* | Synthesis stage model |
| `EVAL_MODEL` | *(empty → LLM_MODEL)* | Optional eval-judge stage model |
| `LLM_API_KEY` | *(empty)* | Bearer credential from env/secrets |
| `LLM_SYNTHESIS_TIMEOUT_SECONDS` | `120` | Synthesis/`complete` timeout |
| `LLM_PLAN_TIMEOUT_SECONDS` | `5` | Planner `complete` timeout |
| `MODEL_CACHE_TTL_SECONDS` | `10` | Models() cache TTL |

- `.env.example` documents each with a comment noting the production value is a
  private gateway URL + per-stage model names a company would choose.

## 6. Data flow (online path, one query)

1. `POST /api/chat` → `get_current_tenant` (401) → RBAC `documents:read`
   (403) → rate limit (429) → audit already wired; body validated.
2. `agentic_router.run_query(..., llm_client=...)` with `engine="lm-studio"`.
3. `_get_llm_plan` → `llm_client.complete(model=planning_model, temperature=0.0,
   max_tokens=150)` → JSON plan.
4. Retrieval identical to today (search_similarity / get_claim_chunks).
5. Final synthesis → `llm_client.complete(model=synthesis_model, temperature=0.1,
   max_tokens=1000)` → answer.
6. Response + audit event (unchanged from Phase 4.3).

## 7. Error handling & robustness

- **Transport/HTTP/non-200/parse failures** raise `ChatClientError`; the router
  catches it and falls back to simulation (unchanged logging). No raw exception
  text leaks to the client.
- **Models-unreachable** → `models()` returns `["local-model"]` without caching a
  failure, so a transient outage self-heals on the next call.
- **Wrong/missing payload shape** → `complete()` raises `ChatClientError` with a
  safe, generic message.
- **Auth failures** (401/403 from the gateway) → surface as `ChatClientError`; no
  credential material in any response or log.
- **Engine allowlist** rejects unknown engines before any network activity.

## 8. Security

- **SSRF:** `LLM_BASE_URL` is server-side config; the request field `engine` never
  influences the URL. Regression test asserts an unknown/URL-shaped `engine` is
  rejected with zero outbound calls.
- **Credential handling:** `LLM_API_KEY` read from env/secrets at config build;
  never echoed, logged, or included in responses; sent only as the Bearer header
  by the client.
- **PII/residency:** the endpoint is allowlisted inside the company footprint;
  document text flows only to the configured private gateway.

## 9. Testing strategy (hermetic — no GPU/cloud/real model)

`backend/llm_client.py` + a recording HTTP stub (duck-typed `http`):

**Unit — `tests/test_llm_client.py`**
- `complete` posts to `{base_url}/v1/chat/completions` with the expected body
  (`model`, `messages`, `temperature`, `max_tokens`) and returns the assistant
  content from an OpenAI-shaped response.
- `model_for_stage` resolves planning/synthesis/eval/fallback correctly.
- `complete` attaches `Authorization: Bearer <api_key>` exactly when configured,
  and never otherwise.
- Non-200 → `ChatClientError`; malformed JSON → `ChatClientError`; missing
  `choices[0].message.content` → `ChatClientError`.
- `models()` returns the first served model, TTL-caches it (manual clock), and
  returns `["local-model"]` on failure without caching (a later success is seen
  on the next call).
- Per-stage models in the payload: planning vs synthesis stage sends the right
  `model` name.

**API-level — `tests/test_api_chat_online.py`**
- A fake OpenAI-compatible server (an in-process `requests`-shape stub or tiny
  ASGI) serves planner + synthesis responses; `POST /api/chat` with a stubbed
  router-heavy real path returns a grounded answer and mirrors the exact
  per-stage model names the server observed.
- Unknown engine → rejection, zero outbound calls to the stub.
- `LLM_API_KEY` set → stub asserts the Bearer header arrives.

The complete-docstring/`GroundedResponse` and the existing `test_api_audit` /
`test_api_rbac` suites keep running (the `/api/chat` JSON shape is unchanged in
5.1).

## 10. Deployment wiring appendix (not built here)

- **Self-hosted vLLM/TGI/SGLang** in the insurer VPC: set `LLM_BASE_URL` to the
  gateway URL (e.g. `https://llm-gateway.internal/v1`), `SYNTHESIS_MODEL` to the
  bought/built domain model id, `PLANNING_MODEL` to a fast small model, and
  `LLM_API_KEY` from the company's secrets manager (KMS). Streaming/live
  verification happens against their endpoint; the hermetic suite already proves
  the request/response contract.
- **Gateway (e.g. LiteLLM)**: front the model portfolio; the app needs only the
  gateway URL + per-stage model ids, unchanged code.
- Anything that needs their GPU infra or proprietary model is out of the repo and
  verified in the company environment.

## 11. Exit-criteria mapping

| Migration doc exit criterion | How this milestone meets it |
|---|---|
| "Planner + synthesis via new backend" | Both use the injected `ChatClient` at the configured private gateway, per-stage models. |
| "`engine` stays a server-side allowlist (SSRF intact)" | Unknown/URL engines rejected with zero network; endpoint is server-set. |
| "model cache adapted" | `_get_loaded_model` moves into `OpenAICompatibleClient.models()` with the same TTL semantics. |