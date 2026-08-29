from __future__ import annotations

import uuid
from typing import Any

from backend.queue import Queue

# Physical S3 object keys are `{tenant}/{scope}/{filename}` -- the tenant prefix
# is added by S3DocumentBlobStore._object_key, and the scope is either a claim
# id or the literal "global" (see rag_engine._blob_key). The bridge validates
# that prefix and enqueues the *logical* key so consumers stay tenant-agnostic.
GLOBAL_SCOPE = "global"


class S3EventIngestBridge:
    """Converts S3 object-created event records into ingestion queue messages.

    This is the testable unit behind the "S3 put-event -> queue" trigger: a
    deployment wires S3 bucket notifications (or a Lambda) to call
    ``handle_event`` with the standard S3 event envelope. Records for the wrong
    bucket, foreign tenants, malformed keys, or non-creation events are skipped
    rather than raised -- a misconfigured notification must not poison the queue.
    """

    def __init__(self, queue: Queue, expected_bucket: str, tenant_id: str):
        if not expected_bucket.strip():
            raise ValueError("expected_bucket must not be empty")
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        self.queue = queue
        self.expected_bucket = expected_bucket
        self.tenant_id = tenant_id

    def handle_event(self, records: list[dict[str, Any]]) -> int:
        """Processes an S3 event's records; returns how many messages were enqueued."""
        enqueued = 0
        for record in records:
            if not self._is_object_created(record):
                continue
            if not self._matches_bucket(record):
                continue
            key = self._object_key(record)
            if not key:
                continue
            logical_key = self._tenant_scope_key(key)
            if logical_key is None:
                # Foreign tenant or malformed `{tenant}/{scope}/{filename}` shape.
                continue
            scope, filename = logical_key.split("/", 1)
            if not filename or filename.startswith("/"):
                continue
            claim_id = None if scope == GLOBAL_SCOPE else scope
            self.queue.enqueue(
                {
                    "job_id": f"job_{uuid.uuid4().hex}",
                    "tenant_id": self.tenant_id,
                    "filename": filename,
                    "claim_id": claim_id,
                    "blob_key": logical_key,
                    "etag": self._etag(record),
                }
            )
            enqueued += 1
        return enqueued

    @staticmethod
    def _is_object_created(record: dict[str, Any]) -> bool:
        return str(record.get("eventName", "")).startswith("ObjectCreated:")

    def _matches_bucket(self, record: dict[str, Any]) -> bool:
        bucket = ((record.get("s3") or {}).get("bucket") or {}).get("name")
        return bucket == self.expected_bucket

    @staticmethod
    def _object_key(record: dict[str, Any]) -> str | None:
        key = ((record.get("s3") or {}).get("object") or {}).get("key")
        if not key:
            return None
        # S3 event keys are URL-encoded when they contain special characters.
        from urllib.parse import unquote

        return unquote(key)

    def _tenant_scope_key(self, physical_key: str) -> str | None:
        parts = physical_key.split("/", 2)
        if len(parts) != 3 or parts[0] != self.tenant_id:
            return None
        scope, filename = parts[1], parts[2]
        if not scope or not filename:
            return None
        return f"{scope}/{filename}"

    @staticmethod
    def _etag(record: dict[str, Any]) -> str:
        etag = ((record.get("s3") or {}).get("object") or {}).get("eTag") or ""
        return etag.strip('"')
