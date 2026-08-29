from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Callable


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
            raise ChatClientError("LLM request failed") from exc

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
