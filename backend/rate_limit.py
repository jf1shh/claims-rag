from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import deque
from threading import Lock
from typing import Callable


class RateLimitExceeded(Exception):
    """A key has exceeded its allowed request rate (mapped to HTTP 429)."""


class RateLimiter(ABC):
    """Contract for a request-rate limiter keyed by a principal identity.

    Sensitive/costly endpoints call ``check`` once per request; the limiter
    records the hit and raises ``RateLimitExceeded`` (→ 429) once the key is
    over its configured allowance for the window.
    """

    @abstractmethod
    def check(self, key: str) -> None:
        raise NotImplementedError


class SlidingWindowRateLimiter(RateLimiter):
    """Per-key sliding-window request limiter.

    Each key keeps the timestamps of its recent requests in a deque. On every
    ``check`` hits that have fallen outside ``window_seconds`` are pruned; if
    the remaining count is already ``max_requests`` the request is rejected
    (and not counted), otherwise it is allowed and counted.

    The clock is injectable so tests drive the window deterministically -- the
    same pattern as the OIDC/JWKS cache clock and the SQS/InProcess queue
    clock. In-process by design: distributed state (Redis) is a deployment
    concern on the same axis as SQS-vs-loopback in Phase 3.
    """

    def __init__(
        self,
        max_requests: int = 60,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._lock = Lock()
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            hits = self._hits.get(key)
            if hits is None:
                hits = deque()
                self._hits[key] = hits
            # Prune hits that have fallen outside the sliding window.
            while hits and now - hits[0] >= self.window_seconds:
                hits.popleft()
            if len(hits) >= self.max_requests:
                raise RateLimitExceeded("rate limit exceeded")
            hits.append(now)

    def allow(self, key: str) -> bool:
        """Non-raising check for convenience (tests, soft limits)."""
        try:
            self.check(key)
        except RateLimitExceeded:
            return False
        return True

    def reset(self) -> None:
        """Clears all recorded hits (used by tests between cases)."""
        self._hits.clear()
