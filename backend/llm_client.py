from __future__ import annotations

import contextlib
import contextvars
import json
import time
from abc import ABC, abstractmethod
from typing import Callable, Iterator


class ChatClientError(Exception):
    """A completion/transport failure from an LLM backend. Callers fall back
    to simulation on this; never leak raw exception text to clients."""


class LLMDeadlineExceeded(ChatClientError):
    """The overall chat deadline (or a stage's share of it) was exhausted
    before or during an LLM call. Subclasses ChatClientError so every
    existing ``except ChatClientError`` handler keeps its current fallback
    semantics. Message text never includes prompt/claim content."""


# Set for the lifetime of one /api/chat request via llm_deadline_scope();
# None outside that scope (streaming, planning-only tools, tests), in which
# case calls fall back to their plain per-stage timeout.
llm_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "llm_deadline", default=None
)


@contextlib.contextmanager
def llm_deadline_scope(seconds: float):
    """Bounds every LLM call made (directly or via copy_context) inside the
    block to a shared wall-clock deadline, on top of each call's own stage
    timeout."""
    token = llm_deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        llm_deadline.reset(token)


# Hard cap on a response body read via complete(); a gateway cannot hold a
# chat slot open by trickling an unbounded body within the time budget.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _iter_body(resp):
    """Yields the body as bytes arrive. urllib3's read1() returns after at
    most one socket read; requests' iter_content(8192) blocks until 8192
    bytes accumulate on a Content-Length body, so a byte-trickle would never
    reach the deadline check between reads. Test stubs have no raw.read1."""
    read1 = getattr(getattr(resp, "raw", None), "read1", None)
    if not callable(read1):
        yield from resp.iter_content(chunk_size=8192)
        return
    while chunk := read1(8192, decode_content=True):
        yield chunk


class ChatClient(ABC):
    """Contract for calling an LLM for one stage of the agentic pipeline.
    Provider-neutral: any OpenAI-compatible endpoint (Lemonade local dev,
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
        stage: str = "synthesis",
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def complete_stream(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        stage: str = "synthesis",
    ) -> Iterator[str]:
        raise NotImplementedError


class _DefaultHTTP:
    """Real transport. Replaced by a recording stub in tests.

    Holds one pooled ``requests.Session`` for the client's lifetime instead of
    opening a new TCP/TLS connection on every call."""

    def __init__(self):
        import requests
        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=16)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def post(self, url, json=None, headers=None, timeout=None, **kw):
        return self._session.post(url, json=json, headers=headers, timeout=timeout, **kw)

    def get(self, url, timeout=None, **kw):
        return self._session.get(url, timeout=timeout, **kw)

    def close(self):
        self._session.close()


# Placeholder model name used when LLM_MODEL is unset and no server model is known.
UNRESOLVED_MODEL = "local-model"

# How long a failed models() probe is cached, so repeated calls during an
# outage don't each pay the health/models-endpoint timeouts.
MODELS_FAILURE_TTL_SECONDS = 30.0


class OpenAICompatibleClient(ChatClient):
    STAGES = ("planning", "synthesis", "eval")

    def __init__(self, base_url, default_model, planning_model=None,
                 synthesis_model=None, eval_model=None, api_key=None,
                 timeout=120.0, plan_timeout=5.0, check_timeout=30.0,
                 models_ttl_seconds=10.0,
                 http=None, clock: Callable[[], float] = time.monotonic):
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.planning_model = planning_model
        self.synthesis_model = synthesis_model
        self.eval_model = eval_model
        self.api_key = api_key
        self.timeout = timeout
        self.plan_timeout = plan_timeout
        self.check_timeout = check_timeout
        self.models_ttl_seconds = models_ttl_seconds
        self._http = http or _DefaultHTTP()
        self._clock = clock
        self._models_cache: list[str] | None = None
        self._models_at = -float("inf")
        self._models_failed_at = -float("inf")

    def _stage_timeout(self, stage: str) -> float:
        # The timeout follows the stage, not the resolved model name: under the
        # default dev config (PLANNING_MODEL unset) the planner's resolved model
        # equals default_model, so picking the timeout by `model == planning_model`
        # would silently give the planner the 120s synthesis timeout.
        if stage == "planning":
            return self.plan_timeout
        if stage.endswith("_check"):
            return self.check_timeout
        return self.timeout

    def _call_budget(self, stage: str) -> float:
        """Timeout for one call: its stage timeout, capped by whatever is left
        of the overall chat deadline. Raises before any request is made if the
        deadline is already gone."""
        stage_timeout = self._stage_timeout(stage)
        deadline = llm_deadline.get()
        if deadline is None:
            return stage_timeout
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise LLMDeadlineExceeded("LLM call deadline already exceeded")
        return min(stage_timeout, remaining)

    def _read_body(self, resp, bound: float) -> bytes:
        """Reads the response body in chunks, enforcing a wall-clock bound
        between reads (not just a per-read socket timeout) and a maximum
        total size."""
        chunks = []
        total = 0
        for chunk in _iter_body(resp):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ChatClientError("LLM response exceeded maximum size")
            chunks.append(chunk)
            if self._clock() >= bound:
                raise LLMDeadlineExceeded("LLM response exceeded the allotted time")
        return b"".join(chunks)

    def model_for_stage(self, stage: str) -> str:
        name = {
            "planning": self.planning_model,
            "synthesis": self.synthesis_model,
            "eval": self.eval_model,
        }.get(stage) or self.default_model
        if name and name != UNRESOLVED_MODEL:
            return name
        # No LLM_MODEL configured: send the model the server actually has loaded.
        # Sending the "local-model" placeholder made Lemonade answer 404.
        return self.models()[0]

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def complete(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        timeout = self._call_budget(stage)
        bound = self._clock() + timeout
        resp = None
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
                stream=True,
            )
            resp.raise_for_status()
            body = self._read_body(resp, bound)
            try:
                return json.loads(body)["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ChatClientError("LLM response missing content") from exc
        except ChatClientError:
            raise
        except Exception as exc:
            raise ChatClientError("LLM request failed") from exc
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                close()

    def complete_stream(self, messages, *, model, temperature, max_tokens, stage="synthesis"):
        timeout = self._call_budget(stage)
        bound = self._clock() + timeout
        resp = None
        try:
            resp = self._http.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                },
                headers=self._headers(),
                timeout=timeout,
                stream=True,
            )
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if self._clock() >= bound:
                    raise LLMDeadlineExceeded("LLM stream exceeded the allotted time")
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    return
                chunk = json.loads(data)
                delta = chunk["choices"][0]["delta"]
                content = delta.get("content")
                if content:
                    yield content
        except ChatClientError:
            raise
        except Exception as exc:
            raise ChatClientError("LLM stream failed") from exc
        finally:
            # Releases the upstream connection whether the stream finished,
            # errored, or the caller closed it early (GeneratorExit) --
            # otherwise a disconnected client leaks the open HTTP response.
            close = getattr(resp, "close", None)
            if callable(close):
                close()

    def models(self) -> list[str]:
        now = self._clock()
        if self._models_cache is not None and (now - self._models_at) < self.models_ttl_seconds:
            return self._models_cache
        if (now - self._models_failed_at) < MODELS_FAILURE_TTL_SECONDS:
            return [UNRESOLVED_MODEL]
        # Lemonade's /v1/models lists every *downloaded* model, so data[0] can name one
        # that is not resident and requesting it forces a model swap. Its health
        # endpoint names the loaded ones; other servers fall through to /v1/models.
        loaded = self._loaded_llms()
        if loaded:
            self._models_cache, self._models_at = loaded, now
            return loaded
        try:
            resp = self._http.get(f"{self.base_url}/v1/models", timeout=2.0)
            ids = [m["id"] for m in resp.json().get("data") or []] if resp.status_code == 200 else []
            ids = [i for i in ids if "embed" not in i.lower()] or ids
            if ids:
                self._models_cache = [ids[0]]
                self._models_at = now
                return self._models_cache
        except Exception:
            pass
        self._models_failed_at = now
        return [UNRESOLVED_MODEL]

    def close(self) -> None:
        close = getattr(self._http, "close", None)
        if callable(close):
            close()

    def _loaded_llms(self) -> list[str]:
        try:
            resp = self._http.get(f"{self.base_url}/api/v1/health", timeout=2.0)
            if resp.status_code != 200:
                return []
            loaded = resp.json().get("all_models_loaded") or []
            return [m["model_name"] for m in loaded
                    if m.get("model_name") and m.get("type", "llm") == "llm"]
        except Exception:
            return []
