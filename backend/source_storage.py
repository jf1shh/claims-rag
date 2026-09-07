"""Publish new source bytes without mutating the currently indexed version."""
import hashlib
import uuid
from pathlib import Path

from backend.blob_store import LocalDocumentBlobStore


def source_store(store):
    return store.blob_store or LocalDocumentBlobStore(store.storage_dir)


def stage_source(store, filename, file_type, text, file_path=None):
    content = Path(file_path).read_bytes() if file_path else text.encode('utf-8')
    version = 'sha256:' + hashlib.sha256(content).hexdigest()
    # Unique keys avoid touching an existing version, even on a retry.
    key = f'versions/{uuid.uuid4().hex}/{filename}'
    source_store(store).put(key, content, file_type or 'application/octet-stream')
    return key, version
