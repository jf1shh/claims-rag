from __future__ import annotations

import json
import os
from backend.file_lock import file_lock
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
        with file_lock(str(self.path) + ".lock"):
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o600)
            previous_size = os.fstat(fd).st_size
            # Recover a torn final append left by process termination. Earlier
            # complete records are retained, and the recovery is recorded.
            if previous_size and _read_at(fd, 1, previous_size - 1) != b"\n":
                position = previous_size
                end = 0
                while position:
                    start = max(0, position - 65536)
                    chunk = _read_at(fd, position - start, start)
                    newline = chunk.rfind(b"\n")
                    if newline >= 0:
                        end = start + newline + 1
                        break
                    position = start
                os.ftruncate(fd, end)
                previous_size = end
                safe_event["recovered_incomplete_tail"] = True
                line = json.dumps(safe_event, sort_keys=True, separators=(",", ":")) + "\n"
            try:
                data = memoryview(line.encode("utf-8"))
                while data:
                    written = os.write(fd, data)
                    if written <= 0:
                        raise OSError("audit append failed")
                    data = data[written:]
                os.fsync(fd)
            except BaseException:
                os.ftruncate(fd, previous_size)
                raise
            finally:
                os.close(fd)

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


def _read_at(fd, count, offset):
    os.lseek(fd, offset, os.SEEK_SET)
    return os.read(fd, count)
