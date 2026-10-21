"""Audit finding F: the download route must presign with the short, named TTL
and pass an attachment content-disposition carrying the document's filename."""

from fastapi.testclient import TestClient

from backend.app import app
from backend.app import runtime as app_module
from backend.blob_store import PRESIGN_EXPIRES_SECONDS


class _FakeBlobStore:
    def __init__(self):
        self.calls = []

    def create_download_url(self, key, expires_seconds, filename=None):
        self.calls.append((key, expires_seconds, filename))
        return "https://example.com/presigned"


class _FakeStore:
    def __init__(self, blob_store):
        self.blob_store = blob_store

    def get_document_metadata(self, filename, claim_id=None):
        return {"document_version": "1", "claim_id": claim_id, "storage_key": f"global/{filename}"}


def test_given_object_store_backed_download_when_requested_then_presign_uses_short_ttl_and_filename(monkeypatch):
    blob = _FakeBlobStore()
    monkeypatch.setattr(app_module, "vector_store", _FakeStore(blob))

    response = TestClient(app).get("/api/documents/download/report.pdf", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert blob.calls == [("global/report.pdf", PRESIGN_EXPIRES_SECONDS, "report.pdf")]
