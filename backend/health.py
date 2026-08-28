from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def live_status() -> dict[str, str]:
    return {"status": "live"}


def ready_status(checks: Mapping[str, Callable[[], Any]]) -> tuple[dict[str, Any], int]:
    failures: dict[str, str] = {}
    for name, check in checks.items():
        try:
            check()
        except Exception as exc:
            failures[name] = type(exc).__name__
    if failures:
        return {"status": "not_ready", "failed_dependencies": failures}, 503
    return {"status": "ready", "failed_dependencies": {}}, 200
