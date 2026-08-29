from __future__ import annotations

import json
import os
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditSink(ABC):
    @abstractmethod
    def record(self, event: dict[str, object]) -> None:
        raise NotImplementedError


class JsonlAuditSink(AuditSink):
    REQUIRED_FIELDS = frozenset({"event", "request_id", "tenant_id"})

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: dict[str, object]) -> None:
        missing = self.REQUIRED_FIELDS - event.keys()
        if missing:
            raise ValueError(f"audit event missing fields: {sorted(missing)}")
        safe_event: dict[str, Any] = dict(event)
        safe_event.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
        line = json.dumps(safe_event, sort_keys=True, separators=(",", ":")) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent, text=True)
        try:
            existing = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(existing)
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def read_events(self) -> list[dict[str, Any]]:
        """Returns every recorded event in append order. Used by tests and by
        operators inspecting the log; each line is a full, self-describing
        event so no join with job/vector-store state is required."""
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events
