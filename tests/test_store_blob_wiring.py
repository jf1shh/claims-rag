import boto3
import pytest
from moto import mock_aws

from backend.blob_store import S3DocumentBlobStore
from backend.rag_engine import SQLiteVectorStore


class _FakeEmbedder:
    """Deterministic, torch-free embedder (mirrors tests/test_rag_engine.py)."""

    def _vec(self, text):
        import hashlib

        digest = hashlib.md5((text or "").encode()).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(16)]

    def embed_chunks(self, chunks):
        return [self._vec(c) for c in chunks]

    def embed_query(self, query):
        return self._vec(query)


BUCKET = "test-bucket"


@pytest.fixture
def s3_db_store(tmp_path):
    """An SQLite store whose source bytes are routed to an S3 adapter (moto)."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        blob = S3DocumentBlobStore(bucket=BUCKET, tenant_id="tenant-a")
        store = SQLiteVectorStore(
            db_path=str(tmp_path / "test.db"),
            storage_dir=str(tmp_path / "stored_documents"),
            blob_store=blob,
        )
        yield client, store, blob, tmp_path / "stored_documents"


LABOR_TEXT = (
    "Regional labor rate schedule 2026. The maximum allowed mechanical labor "
    "rate for Nevada is $110 per hour. Sheet metal repair is capped at $62 "
    "per hour and refinishing at $62 per hour statewide."
)


def test_add_document_writes_source_bytes_to_s3_under_tenant_scoped_key(s3_db_store):
    client, store, blob, local_dir = s3_db_store
    store.add_document("labor.txt", "txt", 100, LABOR_TEXT, _FakeEmbedder(), claim_id="claim-9")
    # Object lives at {tenant}/{scope}/{filename}.
    body = client.get_object(Bucket=BUCKET, Key="tenant-a/" + store.get_blob_key("labor.txt"))["Body"].read()
    assert body.decode() == LABOR_TEXT
    # The local storage_dir must NOT receive a copy (server is stateless re files).
    assert not (local_dir / "labor.txt").exists()


def test_add_document_global_scopes_key_with_global(s3_db_store):
    client, store, blob, local_dir = s3_db_store
    store.add_document("labor.txt", "txt", 100, LABOR_TEXT, _FakeEmbedder())
    keys = [o["Key"] for o in client.list_objects_v2(Bucket=BUCKET).get("Contents", [])]
    assert keys == ["tenant-a/" + store.get_blob_key("labor.txt")]


def test_get_blob_key_resolves_scope_for_presigned_download(s3_db_store):
    client, store, blob, local_dir = s3_db_store
    store.add_document("labor.txt", "txt", 100, LABOR_TEXT, _FakeEmbedder(), claim_id="claim-9")
    assert store.get_blob_key("labor.txt").startswith("versions/")
    url = blob.create_download_url(store.get_blob_key("labor.txt"), 3600)
    assert "tenant-a/" + store.get_blob_key("labor.txt") in url


def test_delete_document_removes_s3_object(s3_db_store):
    client, store, blob, local_dir = s3_db_store
    store.add_document("labor.txt", "txt", 100, LABOR_TEXT, _FakeEmbedder(), claim_id="claim-9")
    assert store.delete_document("labor.txt")
    keys = [o["Key"] for o in client.list_objects_v2(Bucket=BUCKET).get("Contents", [])]
    assert "tenant-a/claim-9/labor.txt" not in keys


def test_delete_missing_document_is_noop_and_leaves_blobs(s3_db_store):
    client, store, blob, local_dir = s3_db_store
    store.add_document("labor.txt", "txt", 100, LABOR_TEXT, _FakeEmbedder(), claim_id="claim-9")
    assert not store.delete_document("nope.txt")
    # The stored blob is untouched.
    body = client.get_object(Bucket=BUCKET, Key="tenant-a/" + store.get_blob_key("labor.txt"))["Body"].read()
    assert body.decode() == LABOR_TEXT
