"""Durable ingestion-job store tests (Phase 3.2)."""

from datetime import datetime, timezone

import pytest

from backend.ingestion import IngestionJob, IngestionStatus
from backend.job_store import SqliteJobStore


def _job(tenant_id="tenant-a", filename="labor.txt", status=IngestionStatus.queued, **overrides):
    now = datetime.now(timezone.utc)
    fields = dict(
        job_id="job_abc123",
        tenant_id=tenant_id,
        document_id="pending_x",
        claim_id=None,
        filename=filename,
        checksum="sha256:deadbeef",
        idempotency_key=None,
        status=status,
        created_at=now,
        updated_at=now,
    )
    fields.update(overrides)
    return IngestionJob(**fields)


def test_given_job_when_created_then_get_returns_it(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job())
    job = store.get("job_abc123", "tenant-a")
    assert job.filename == "labor.txt"
    assert job.status is IngestionStatus.queued
    assert job.tenant_id == "tenant-a"


def test_given_missing_job_when_get_then_key_error(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    with pytest.raises(KeyError):
        store.get("job_nope", "tenant-a")


def test_given_job_from_tenant_a_when_read_by_tenant_b_then_permission_error(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job(tenant_id="tenant-a"))
    with pytest.raises(PermissionError):
        store.get("job_abc123", "tenant-b")


def test_given_job_when_updated_then_new_state_is_returned(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job())
    updated = store.update(
        "job_abc123",
        "tenant-a",
        status=IngestionStatus.embedding,
        progress=75,
        document_id="doc_42",
    )
    assert updated.status is IngestionStatus.embedding
    assert updated.progress == 75
    assert updated.document_id == "doc_42"
    assert updated.updated_at >= updated.created_at
    # The change is durable: a fresh store on the same file sees it.
    reopened = SqliteJobStore(str(tmp_path / "jobs.db"))
    assert reopened.get("job_abc123", "tenant-a").progress == 75


def test_given_job_when_update_with_non_updatable_field_then_rejected(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job())
    with pytest.raises(ValueError):
        store.update("job_abc123", "tenant-a", filename="other.txt")


def test_given_job_when_created_twice_then_same_record_returned(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    first = store.create(_job())
    second = store.create(_job())
    assert first.job_id == second.job_id


def test_given_dupe_key_when_found_then_job_returned(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job(idempotency_key="k1"))
    found = store.find_by_dedupe_key("tenant-a", "k1")
    assert found is not None
    assert found.job_id == "job_abc123"
    assert store.find_by_dedupe_key("tenant-a", "k2") is None
    assert store.find_by_dedupe_key("tenant-b", "k1") is None


def test_given_jobs_then_persist_across_store_instances(tmp_path):
    path = str(tmp_path / "jobs.db")
    SqliteJobStore(path).create(_job(job_id="job_1"))
    SqliteJobStore(path).create(_job(job_id="job_2", filename="other.txt"))
    reopened = SqliteJobStore(path)
    assert reopened.get("job_1", "tenant-a").filename == "labor.txt"
    assert reopened.get("job_2", "tenant-a").filename == "other.txt"


def test_given_empty_db_path_then_store_is_rejected():
    with pytest.raises(ValueError):
        SqliteJobStore("")
