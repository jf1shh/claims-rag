from __future__ import annotations

import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path


class DocumentBlobStore(ABC):
    @abstractmethod
    def put(self, key: str, content: bytes, content_type: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get(self, key: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def create_download_url(self, key: str, expires_seconds: int) -> str:
        raise NotImplementedError


class LocalDocumentBlobStore(DocumentBlobStore):
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not key or "\x00" in key:
            raise ValueError("invalid blob key")
        if any(part in {"", ".", ".."} for part in Path(key).parts):
            raise ValueError("blob key contains an invalid path segment")
        candidate = (self.root / key).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("blob key escapes storage root") from exc
        return candidate

    def put(self, key: str, content: bytes, content_type: str) -> None:
        del content_type  # reserved for the object-storage adapter
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def create_download_url(self, key: str, expires_seconds: int) -> str:
        if expires_seconds <= 0:
            raise ValueError("expires_seconds must be positive")
        self._path(key)
        return f"/api/blobs/{key}"
