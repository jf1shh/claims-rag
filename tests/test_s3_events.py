"""S3 put-event -> ingestion-queue bridge tests (Phase 3 async ingestion)."""

import pytest

from backend.queue import InProcessQueue
from backend.s3_events import S3EventIngestBridge


def _created_record(bucket: str, key: str, etag: str = '"abc123"') -> dict:
    return {
        "eventName": "ObjectCreated:Put",
        "s3": {
            "bucket": {"name": bucket},
            "object": {"key": key, "eTag": etag},
        },
    }


def make_bridge(**kwargs):
    queue = kwargs.pop("queue", InProcessQueue())
    bridge = S3EventIngestBridge(
        queue=queue,
        expected_bucket=kwargs.pop("expected_bucket", "my-bucket"),
        tenant_id=kwargs.pop("tenant_id", "tenant-a"),
    )
    return queue, bridge


def test_given_global_and_claim_events_when_handled_then_both_are_enqueued():
    queue, bridge = make_bridge()
    records = [
        _created_record("my-bucket", "tenant-a/global/policy.txt"),
        _created_record("my-bucket", "tenant-a/claim-9/receipt.xlsx"),
    ]
    assert bridge.handle_event(records) == 2
    first = queue.dequeue()
    second = queue.dequeue()
    assert first.payload["job_id"].startswith("job_")
    assert first.payload["tenant_id"] == "tenant-a"
    assert first.payload["filename"] == "policy.txt"
    assert first.payload["claim_id"] is None
    assert first.payload["blob_key"] == "global/policy.txt"
    assert first.payload["etag"] == "abc123"
    assert second.payload["blob_key"] == "claim-9/receipt.xlsx"
    assert second.payload["claim_id"] == "claim-9"
    assert second.payload["filename"] == "receipt.xlsx"


def test_given_wrong_bucket_event_when_handled_then_skipped():
    queue, bridge = make_bridge()
    assert bridge.handle_event([_created_record("other-bucket", "tenant-a/global/x.txt")]) == 0
    assert queue.pending_count == 0


def test_given_foreign_tenant_event_when_handled_then_skipped():
    queue, bridge = make_bridge()
    assert bridge.handle_event([_created_record("my-bucket", "tenant-b/global/x.txt")]) == 0
    assert queue.pending_count == 0


def test_given_non_creation_event_when_handled_then_skipped():
    queue, bridge = make_bridge()
    record = _created_record("my-bucket", "tenant-a/global/x.txt")
    record["eventName"] = "ObjectRemoved:Delete"
    assert bridge.handle_event([record]) == 0
    assert queue.pending_count == 0


def test_given_malformed_key_when_handled_then_skipped():
    queue, bridge = make_bridge()
    # Only {tenant}/{scope} -- no filename segment.
    assert bridge.handle_event([_created_record("my-bucket", "tenant-a/global")]) == 0
    assert queue.pending_count == 0


def test_given_url_encoded_key_when_handled_then_decoded():
    queue, bridge = make_bridge()
    record = _created_record("my-bucket", "tenant-a/global/repair%20order.txt")
    assert bridge.handle_event([record]) == 1
    message = queue.dequeue()
    assert message.payload["filename"] == "repair order.txt"
    assert message.payload["blob_key"] == "global/repair order.txt"


def test_given_empty_bucket_or_tenant_then_bridge_is_rejected():
    with pytest.raises(ValueError):
        S3EventIngestBridge(queue=InProcessQueue(), expected_bucket="", tenant_id="tenant-a")
    with pytest.raises(ValueError):
        S3EventIngestBridge(queue=InProcessQueue(), expected_bucket="my-bucket", tenant_id="")
