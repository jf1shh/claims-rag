from urllib.parse import parse_qs, urlparse

import boto3
import botocore
import pytest
from moto import mock_aws

from backend.blob_store import PRESIGN_EXPIRES_SECONDS, S3DocumentBlobStore

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


def test_given_filename_when_presigned_url_requested_then_content_disposition_is_attachment(s3):
    _, store = s3
    store.put("report.txt", b"x", "application/pdf")
    url = store.create_download_url("report.txt", 3600, filename="loss statement Brooks.pdf")
    query = parse_qs(urlparse(url).query)
    assert query["response-content-disposition"][0] == "attachment; filename*=UTF-8''loss%20statement%20Brooks.pdf"


def test_given_no_filename_when_presigned_url_requested_then_no_disposition_override(s3):
    _, store = s3
    store.put("report.txt", b"x", "application/pdf")
    url = store.create_download_url("report.txt", 3600)
    query = parse_qs(urlparse(url).query)
    assert "response-content-disposition" not in query


def test_presign_lifetime_constant_is_300_seconds():
    assert PRESIGN_EXPIRES_SECONDS == 300


def test_given_traversal_key_when_written_then_rejected(s3):
    _, store = s3
    with pytest.raises(ValueError):
        store.put("tenant-a/../tenant-b/leak.txt", b"x", "text/plain")


def test_given_empty_bucket_name_then_store_is_rejected():
    with pytest.raises(ValueError):
        S3DocumentBlobStore(bucket="", tenant_id="tenant-a")


def test_given_versioned_bucket_when_deleted_then_all_versions_are_gone(s3):
    client, store = s3
    client.put_bucket_versioning(
        Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"}
    )
    store.put("report.txt", b"old", "text/plain")
    store.put("report.txt", b"new", "text/plain")
    store.delete("report.txt")

    with pytest.raises(botocore.exceptions.ClientError):
        store.get("report.txt")
    versions = client.list_object_versions(
        Bucket=BUCKET, Prefix="tenant-a/report.txt"
    )
    assert versions.get("Versions", []) == []
    assert versions.get("DeleteMarkers", []) == []


def test_given_versioned_bucket_when_deleted_then_similarly_named_key_is_untouched(s3):
    client, store = s3
    client.put_bucket_versioning(
        Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"}
    )
    store.put("a/b.pdf", b"keep-version-1", "application/pdf")
    store.put("a/b.pdf", b"keep-version-2", "application/pdf")
    store.put("a/b.pdf.bak", b"unrelated", "application/pdf")

    store.delete("a/b.pdf")

    assert store.get("a/b.pdf.bak") == b"unrelated"
    with pytest.raises(botocore.exceptions.ClientError):
        store.get("a/b.pdf")


def test_given_many_versions_when_deleted_then_batches_beyond_1000_are_handled(s3, monkeypatch):
    client, store = s3
    client.put_bucket_versioning(
        Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"}
    )
    store.put("report.txt", b"v", "text/plain")

    fake_versions = [
        {"Key": "tenant-a/report.txt", "VersionId": f"v{i}"} for i in range(1234)
    ]
    monkeypatch.setattr(
        store, "_list_versions", lambda object_key: fake_versions
    )

    seen = []
    real_delete_objects = client.delete_objects

    def tracking_delete_objects(**kwargs):
        seen.append(len(kwargs["Delete"]["Objects"]))
        return real_delete_objects(**kwargs)

    monkeypatch.setattr(store._client, "delete_objects", tracking_delete_objects)

    store.delete("report.txt")

    assert seen == [1000, 234]


def test_given_access_denied_on_version_listing_when_deleted_then_falls_back_to_plain_delete(
    s3, monkeypatch, caplog
):
    client, store = s3
    store.put("report.txt", b"x", "text/plain")

    def deny(*args, **kwargs):
        raise botocore.exceptions.ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "no"}}, "ListObjectVersions"
        )

    monkeypatch.setattr(store, "_list_versions", deny)

    import logging

    with caplog.at_level(logging.WARNING):
        store.delete("report.txt")

    with pytest.raises(botocore.exceptions.ClientError):
        store.get("report.txt")
    assert "AccessDenied" in caplog.text
    assert "report.txt" not in caplog.text


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
