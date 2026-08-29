import boto3
import botocore
import pytest
from moto import mock_aws

from backend.blob_store import S3DocumentBlobStore

BUCKET = "test-bucket"


@pytest.fixture
def s3():
    """An in-process S3 mock with a ready bucket; yields (client, store)."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        store = S3DocumentBlobStore(bucket=BUCKET, tenant_id="tenant-a")
        yield client, store


def test_given_tenant_scoped_key_when_written_then_content_is_stored_under_tenant_prefix(s3):
    client, store = s3
    store.put("claim-1/report.txt", b"evidence", "text/plain")
    assert store.get("claim-1/report.txt") == b"evidence"
    # The object must live under the tenant prefix so one bucket never mixes tenants.
    body = client.get_object(Bucket=BUCKET, Key="tenant-a/claim-1/report.txt")["Body"].read()
    assert body == b"evidence"


def test_given_existing_blob_when_replaced_then_old_content_is_not_returned(s3):
    _, store = s3
    store.put("report.txt", b"old", "text/plain")
    store.put("report.txt", b"new", "text/plain")
    assert store.get("report.txt") == b"new"


def test_given_existing_blob_when_deleted_then_get_raises(s3):
    client, store = s3
    store.put("report.txt", b"x", "text/plain")
    store.delete("report.txt")
    with pytest.raises(botocore.exceptions.ClientError):
        store.get("report.txt")
    # Delete is idempotent for a missing object (mirrors the local adapter).
    store.delete("report.txt")
    keys = [
        o["Key"]
        for o in client.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    ]
    assert "tenant-a/report.txt" not in keys


def test_given_blob_when_presigned_url_requested_then_it_is_http_and_scoped_to_object(s3):
    _, store = s3
    store.put("report.txt", b"x", "application/pdf")
    url = store.create_download_url("report.txt", 3600)
    assert url.startswith("https://")
    assert "tenant-a/report.txt" in url


def test_given_non_positive_expiration_when_url_requested_then_rejected(s3):
    _, store = s3
    store.put("report.txt", b"x", "text/plain")
    with pytest.raises(ValueError):
        store.create_download_url("report.txt", 0)


def test_given_traversal_key_when_written_then_rejected(s3):
    _, store = s3
    with pytest.raises(ValueError):
        store.put("tenant-a/../tenant-b/leak.txt", b"x", "text/plain")


def test_given_empty_bucket_name_then_store_is_rejected():
    with pytest.raises(ValueError):
        S3DocumentBlobStore(bucket="", tenant_id="tenant-a")


def test_given_distinct_tenants_then_keys_do_not_collide():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        a = S3DocumentBlobStore(bucket=BUCKET, tenant_id="tenant-a")
        b = S3DocumentBlobStore(bucket=BUCKET, tenant_id="tenant-b")
        a.put("report.txt", b"a-content", "text/plain")
        b.put("report.txt", b"b-content", "text/plain")
        assert a.get("report.txt") == b"a-content"
        assert b.get("report.txt") == b"b-content"
