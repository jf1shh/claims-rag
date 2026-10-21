"""Publish new source bytes without mutating the currently indexed version."""
import hashlib
import logging
import uuid
from pathlib import Path

from backend.blob_store import LocalDocumentBlobStore
from backend.document_limits import CONTENT_TYPES

logger = logging.getLogger(__name__)


def source_store(store):
    return store.blob_store or LocalDocumentBlobStore(store.storage_dir)


def stage_source(store, filename, file_type, text, file_path=None):
    content = Path(file_path).read_bytes() if file_path else text.encode('utf-8')
    version = 'sha256:' + hashlib.sha256(content).hexdigest()
    # Unique keys avoid touching an existing version, even on a retry.
    key = f'versions/{uuid.uuid4().hex}/{filename}'
    content_type = CONTENT_TYPES.get(file_type, 'application/octet-stream')
    source_store(store).put(key, content, content_type)
    return key, version


def discard_staged(store, key, reason):
    """Best-effort delete of a staged or replaced version key; a failure is
    logged by exception type only and never raised."""
    try:
        source_store(store).delete(key)
    except Exception as exc:
        logger.warning("%s blob cleanup failed (%s)", reason, type(exc).__name__)
