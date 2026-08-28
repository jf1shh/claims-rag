import pytest

from backend.blob_store import LocalDocumentBlobStore


def test_given_tenant_scoped_key_when_written_then_content_can_be_read_back(tmp_path):
    store = LocalDocumentBlobStore(tmp_path)
    store.put("tenant-a/claim-1/report.txt", b"evidence", "text/plain")
    assert store.get("tenant-a/claim-1/report.txt") == b"evidence"


def test_given_traversal_key_when_written_then_operation_is_rejected(tmp_path):
    store = LocalDocumentBlobStore(tmp_path)
    with pytest.raises(ValueError):
        store.put("tenant-a/../tenant-b/leak.txt", b"x", "text/plain")


def test_given_existing_blob_when_replaced_then_old_content_is_not_returned(tmp_path):
    store = LocalDocumentBlobStore(tmp_path)
    store.put("tenant-a/report.txt", b"old", "text/plain")
    store.put("tenant-a/report.txt", b"new", "text/plain")
    assert store.get("tenant-a/report.txt") == b"new"


def test_given_non_positive_expiration_when_url_is_requested_then_it_is_rejected(tmp_path):
    store = LocalDocumentBlobStore(tmp_path)
    with pytest.raises(ValueError):
        store.create_download_url("a.txt", 0)
