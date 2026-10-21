from __future__ import annotations

import uuid
from typing import Any

from backend.queue import Queue

# Physical S3 object keys the bridge accepts are `{tenant}/inbound/{scope}/{filename}`
# -- the tenant prefix is added by S3DocumentBlobStore._object_key, "inbound" is a
# fixed marker that is never used by the app's own object keys (`versions/<uuid>/...`
# from source_storage.stage_source, `staging/<uuid>/...` from the async upload path),
# and the scope is either a claim id or the literal "global" (see
# rag_engine._blob_key). Configure the S3 bucket notification with a
# `{tenant}/inbound/` key-prefix filter so only genuine client uploads reach this
# bridge. The bridge enqueues the *logical* key so consumers stay tenant-agnostic.
GLOBAL_SCOPE = "global"
INBOUND_PREFIX = "inbound"
# Scopes reserved for the app's own object namespaces; never treated as a claim id.
_RESERVED_SCOPES = frozenset({"versions", "staging", "inbound"})


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
            parsed = self._tenant_scope_key(key)
            if parsed is None:
                # Foreign tenant, reserved/empty scope, or malformed
                # `{tenant}/inbound/{scope}/{filename}` shape.
                continue
            scope, filename = parsed
            logical_key = f"{INBOUND_PREFIX}/{scope}/{filename}"
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

    def _tenant_scope_key(self, physical_key: str) -> tuple[str, str] | None:
        """Parses `{tenant}/inbound/{scope}/{filename}`; filename must be a
        single path segment (no `/`), and scope must be non-empty and not one
        of the app's own reserved namespaces."""
        parts = physical_key.split("/")
        if len(parts) != 4:
            return None
        tenant, inbound, scope, filename = parts
        if tenant != self.tenant_id or inbound != INBOUND_PREFIX:
            return None
        if not scope or scope in _RESERVED_SCOPES:
            return None
        if not filename or filename.startswith("/"):
            return None
        return scope, filename

    @staticmethod
    def _etag(record: dict[str, Any]) -> str:
        etag = ((record.get("s3") or {}).get("object") or {}).get("eTag") or ""
        return etag.strip('"')
