import pytest

from backend.ingestion import IngestionService, IngestionStatus


def test_given_same_tenant_and_idempotency_key_when_upload_is_replayed_then_one_job_is_returned():
    service = IngestionService()
    first = service.submit(tenant_id="tenant-a", filename="a.txt", content=b"x", idempotency_key="k1")
    second = service.submit(tenant_id="tenant-a", filename="a.txt", content=b"x", idempotency_key="k1")
    assert first.job_id == second.job_id
    assert first.status is IngestionStatus.indexed
    assert first.progress == 100


def test_given_different_tenant_when_same_idempotency_key_is_used_then_jobs_are_distinct():
    service = IngestionService()
    first = service.submit(tenant_id="tenant-a", filename="a.txt", content=b"x", idempotency_key="k1")
    second = service.submit(tenant_id="tenant-b", filename="a.txt", content=b"x", idempotency_key="k1")
    assert first.job_id != second.job_id


def test_given_indexer_failure_when_submitted_then_job_is_failed_without_exposing_exception():
    def fail(filename, content, claim_id):
        raise RuntimeError("internal details")

    job = IngestionService(indexer=fail).submit(tenant_id="tenant-a", filename="a.txt", content=b"x")
    assert job.status is IngestionStatus.failed
    assert job.error_code == "INDEXING_FAILED"
    assert job.error_message == "document indexing failed"
    assert "internal" not in (job.error_message or "")


def test_given_job_from_tenant_a_when_read_by_tenant_b_then_access_is_denied():
    service = IngestionService()
    job = service.submit(tenant_id="tenant-a", filename="a.txt", content=b"x")
    with pytest.raises(PermissionError):
        service.get(job.job_id, "tenant-b")
