from __future__ import annotations

import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

import boto3


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


class S3DocumentBlobStore(DocumentBlobStore):
    """Object-storage adapter backed by an S3-compatible service (AWS S3, MinIO,
    LocalStack, …). Keys are addressed under a tenant-scoped prefix so one bucket
    can host many tenants/claims without cross-tenant key collisions.

    The client honors the standard AWS credential chain (env vars, shared config,
    IAM role) and is therefore usable unchanged against a real bucket or a
    mock -- moto for hermetic tests, or MinIO/LocalStack via ``S3_ENDPOINT_URL``.
    """

    def __init__(
        self,
        bucket: str,
        tenant_id: str,
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        sse_kms_key_id: str | None = None,
    ):
        if not bucket.strip():
            raise ValueError("S3 bucket name must not be empty")
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        self.bucket = bucket
        self.tenant_id = tenant_id
        self.endpoint_url = endpoint_url
        self.sse_kms_key_id = sse_kms_key_id
        client_kwargs: dict = {"region_name": region, "endpoint_url": endpoint_url} if endpoint_url else {"region_name": region}
        self._client = boto3.client("s3", **client_kwargs)

    def _object_key(self, key: str) -> str:
        if not key or "\x00" in key or key.startswith("/"):
            raise ValueError("invalid blob key")
        if any(part in {"", ".", ".."} for part in key.split("/")):
            raise ValueError("blob key contains an invalid path segment")
        # Namespace every object under the tenant so one bucket never mixes tenants.
        return f"{self.tenant_id}/{key}"

    def put(self, key: str, content: bytes, content_type: str) -> None:
        params: dict = {
            "Bucket": self.bucket,
            "Key": self._object_key(key),
            "Body": content,
            "ContentType": content_type or "application/octet-stream",
        }
        if self.sse_kms_key_id:
            params["ServerSideEncryption"] = "aws:kms"
            params["SSEKMSKeyId"] = self.sse_kms_key_id
        self._client.put_object(**params)

    def get(self, key: str) -> bytes:
        response = self._client.get_object(
            Bucket=self.bucket, Key=self._object_key(key)
        )
        try:
            return response["Body"].read()
        finally:
            response["Body"].close()

    def delete(self, key: str) -> None:
        # S3/DC delete is idempotent; deleting a missing object is a no-op,
        # mirroring LocalDocumentBlobStore.delete.
        self._client.delete_object(Bucket=self.bucket, Key=self._object_key(key))

    def create_download_url(self, key: str, expires_seconds: int) -> str:
        if expires_seconds <= 0:
            raise ValueError("expires_seconds must be positive")
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self._object_key(key)},
            ExpiresIn=expires_seconds,
        )
