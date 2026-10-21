"""Durable ingestion-job store tests (Phase 3.2)."""

from datetime import datetime, timedelta, timezone

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


# ---------------------------------------------------------------------------
# M12c-D -- claim-by-job-id (concurrent redelivery / replica protection)
# ---------------------------------------------------------------------------

def test_given_unclaimed_job_when_claimed_then_claimed(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job())
    assert store.claim("job_abc123", "tenant-a", "token-1", lease_seconds=60) == "claimed"


def test_given_live_claim_when_claimed_again_then_held(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job())
    now = datetime.now(timezone.utc)
    assert store.claim("job_abc123", "tenant-a", "token-1", lease_seconds=60, now=now) == "claimed"
    assert store.claim("job_abc123", "tenant-a", "token-2", lease_seconds=60, now=now) == "held"


def test_given_stale_claim_older_than_lease_when_claimed_then_reclaimable(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job())
    first = datetime.now(timezone.utc)
    assert store.claim("job_abc123", "tenant-a", "token-1", lease_seconds=60, now=first) == "claimed"
    later = first + timedelta(seconds=61)
    assert store.claim("job_abc123", "tenant-a", "token-2", lease_seconds=60, now=later) == "claimed"


def test_given_job_already_indexed_when_claimed_then_duplicate(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job(status=IngestionStatus.indexed))
    assert store.claim("job_abc123", "tenant-a", "token-1", lease_seconds=60) == "duplicate"


def test_given_job_already_deleted_when_claimed_then_duplicate(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job(status=IngestionStatus.deleted))
    assert store.claim("job_abc123", "tenant-a", "token-1", lease_seconds=60) == "duplicate"


def test_given_missing_job_when_claimed_then_key_error(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    with pytest.raises(KeyError):
        store.claim("job_nope", "tenant-a", "token-1", lease_seconds=60)


# ---------------------------------------------------------------------------
# M12c-E -- claim purge + job retention
# ---------------------------------------------------------------------------


def test_given_active_and_terminal_jobs_when_counted_then_only_active_counted(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job(job_id="job_queued", claim_id="claim-1", status=IngestionStatus.queued))
    store.create(_job(job_id="job_indexed", claim_id="claim-1", status=IngestionStatus.indexed))
    store.create(_job(job_id="job_failed", claim_id="claim-1", status=IngestionStatus.failed))
    store.create(_job(job_id="job_deleted", claim_id="claim-1", status=IngestionStatus.deleted))
    assert store.active_jobs_for_claim("tenant-a", "claim-1") == 1


def test_given_no_jobs_for_claim_when_counted_then_zero(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    assert store.active_jobs_for_claim("tenant-a", "claim-1") == 0


def test_given_claim_jobs_in_other_tenant_when_counted_then_not_counted(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job(job_id="job_1", tenant_id="tenant-b", claim_id="claim-1", status=IngestionStatus.queued))
    assert store.active_jobs_for_claim("tenant-a", "claim-1") == 0


def test_given_claim_jobs_when_deleted_then_count_and_removed(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job(job_id="job_1", claim_id="claim-1"))
    store.create(_job(job_id="job_2", claim_id="claim-1"))
    store.create(_job(job_id="job_other_claim", claim_id="claim-2"))
    store.create(_job(job_id="job_other_tenant", tenant_id="tenant-b", claim_id="claim-1"))
    assert store.delete_for_claim("tenant-a", "claim-1") == 2
    with pytest.raises(KeyError):
        store.get("job_1", "tenant-a")
    # Untouched: a different claim and a different tenant with the same claim_id.
    store.get("job_other_claim", "tenant-a")
    store.get("job_other_tenant", "tenant-b")


def test_given_no_jobs_for_claim_when_deleted_then_zero(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    assert store.delete_for_claim("tenant-a", "claim-1") == 0


def test_given_claim_jobs_when_counted_for_claim_then_total_regardless_of_status(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job(job_id="job_1", claim_id="claim-1", status=IngestionStatus.queued))
    store.create(_job(job_id="job_2", claim_id="claim-1", status=IngestionStatus.indexed))
    assert store.count_for_claim("tenant-a", "claim-1") == 2


def test_given_matching_job_when_document_scrubbed_then_identity_fields_cleared(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job(job_id="job_1", claim_id="claim-1", filename="report.pdf", idempotency_key="k1"))
    assert store.scrub_document("tenant-a", "claim-1", "report.pdf") == 1
    job = store.get("job_1", "tenant-a")
    assert job.filename == "[deleted]"
    assert job.checksum is None
    assert job.idempotency_key is None
    assert job.status is IngestionStatus.queued  # status/timestamps are kept


def test_given_global_scope_job_when_document_scrubbed_then_null_claim_matched(tmp_path):
    """NULL-safe: claim_id IS ? must match a global (claim_id=None) job row,
    not silently scrub zero rows."""
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job(job_id="job_1", claim_id=None, filename="policy.pdf"))
    assert store.scrub_document("tenant-a", None, "policy.pdf") == 1
    assert store.get("job_1", "tenant-a").filename == "[deleted]"


def test_given_different_claim_or_filename_when_document_scrubbed_then_untouched(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    store.create(_job(job_id="job_1", claim_id="claim-1", filename="report.pdf"))
    assert store.scrub_document("tenant-a", "claim-2", "report.pdf") == 0
    assert store.scrub_document("tenant-a", "claim-1", "other.pdf") == 0
    assert store.get("job_1", "tenant-a").filename == "report.pdf"


def test_given_old_terminal_jobs_when_swept_then_removed(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    old = datetime.now(timezone.utc) - timedelta(days=31)
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    store.create(_job(job_id="job_old_indexed", status=IngestionStatus.indexed, created_at=old, updated_at=old))
    store.create(_job(job_id="job_recent_indexed", status=IngestionStatus.indexed, created_at=recent, updated_at=recent))
    store.create(_job(job_id="job_old_active", status=IngestionStatus.queued, created_at=old, updated_at=old))

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    assert store.sweep_terminal("tenant-a", cutoff) == 1
    with pytest.raises(KeyError):
        store.get("job_old_indexed", "tenant-a")
    store.get("job_recent_indexed", "tenant-a")  # kept: not old enough
    store.get("job_old_active", "tenant-a")  # kept: not terminal


def test_given_old_terminal_job_in_other_tenant_when_swept_then_not_removed(tmp_path):
    store = SqliteJobStore(str(tmp_path / "jobs.db"))
    old = datetime.now(timezone.utc) - timedelta(days=31)
    store.create(
        _job(job_id="job_1", tenant_id="tenant-b", status=IngestionStatus.indexed, created_at=old, updated_at=old)
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    assert store.sweep_terminal("tenant-a", cutoff) == 0
    store.get("job_1", "tenant-b")
