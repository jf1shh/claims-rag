"""Claim purge tests (M12c-E, enterprise audit finding #6): deletes all of a
claim's documents + job rows together, refuses while jobs are in flight, and
is idempotent."""

import hashlib
from datetime import datetime, timezone

import pytest

from backend.claim_purge import ClaimPurgeConflict, PurgeResult, purge_claim, validate_claim_id
from backend.ingestion import IngestionJob, IngestionStatus
from backend.job_store import SqliteJobStore
from backend.rag_engine import SQLiteVectorStore


class _FakeEmbedder:
    def _vec(self, text):
        digest = hashlib.md5((text or "").encode()).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(16)]

    def embed_chunks(self, chunks):
        return [self._vec(c) for c in chunks]

    def embed_query(self, query):
        return self._vec(query)


def _store(tmp_path):
    return SQLiteVectorStore(db_path=str(tmp_path / "rag.db"), storage_dir=str(tmp_path / "docs"))


def _job_store(tmp_path):
    return SqliteJobStore(str(tmp_path / "jobs.db"))


def _active_job(job_store, claim_id, job_id="job_active", tenant_id="tenant-a"):
    now = datetime.now(timezone.utc)
    job_store.create(
        IngestionJob(
            job_id=job_id, tenant_id=tenant_id, document_id="pending_x", claim_id=claim_id,
            filename="in_flight.txt", checksum="sha256:x", status=IngestionStatus.queued,
            created_at=now, updated_at=now,
        )
    )


def test_given_claim_with_documents_and_jobs_when_purged_then_both_deleted(tmp_path):
    store = _store(tmp_path)
    job_store = _job_store(tmp_path)
    store.add_document("report.pdf", "pdf", 10, "loss report text", _FakeEmbedder(), claim_id="claim-1")
    store.add_document("photo_notes.txt", "txt", 10, "damage photo notes", _FakeEmbedder(), claim_id="claim-1")
    now = datetime.now(timezone.utc)
    job_store.create(
        IngestionJob(
            job_id="job_1", tenant_id="tenant-a", document_id="doc_1", claim_id="claim-1",
            filename="report.pdf", checksum="sha256:x", status=IngestionStatus.indexed,
            created_at=now, updated_at=now,
        )
    )

    result = purge_claim(vector_store=store, job_store=job_store, tenant_id="tenant-a", claim_id="claim-1")

    assert result == PurgeResult(documents_deleted=2, jobs_deleted=1)
    assert store.get_claim_documents("claim-1") == []
    with pytest.raises(KeyError):
        job_store.get("job_1", "tenant-a")


def test_given_claim_with_no_documents_or_jobs_when_purged_then_zeros(tmp_path):
    store = _store(tmp_path)
    job_store = _job_store(tmp_path)
    result = purge_claim(vector_store=store, job_store=job_store, tenant_id="tenant-a", claim_id="claim-empty")
    assert result == PurgeResult(documents_deleted=0, jobs_deleted=0)


def test_given_active_job_when_purged_then_conflict_and_nothing_deleted(tmp_path):
    store = _store(tmp_path)
    job_store = _job_store(tmp_path)
    store.add_document("report.pdf", "pdf", 10, "loss report text", _FakeEmbedder(), claim_id="claim-1")
    _active_job(job_store, "claim-1")

    with pytest.raises(ClaimPurgeConflict):
        purge_claim(vector_store=store, job_store=job_store, tenant_id="tenant-a", claim_id="claim-1")

    assert len(store.get_claim_documents("claim-1")) == 1
    job_store.get("job_active", "tenant-a")  # still present


def test_given_other_claims_documents_when_purged_then_untouched(tmp_path):
    store = _store(tmp_path)
    job_store = _job_store(tmp_path)
    store.add_document("report.pdf", "pdf", 10, "loss report text", _FakeEmbedder(), claim_id="claim-1")
    store.add_document("other.pdf", "pdf", 10, "other claim text", _FakeEmbedder(), claim_id="claim-2")

    purge_claim(vector_store=store, job_store=job_store, tenant_id="tenant-a", claim_id="claim-1")

    assert store.get_claim_documents("claim-1") == []
    assert len(store.get_claim_documents("claim-2")) == 1


@pytest.mark.parametrize(
    "claim_id",
    ["", "a" * 129, "has/slash", "has\\backslash", "has\x00null", None, 42],
)
def test_given_invalid_claim_id_then_value_error(claim_id):
    with pytest.raises(ValueError):
        validate_claim_id(claim_id)


def test_given_valid_claim_id_then_no_error():
    validate_claim_id("#2026-99382")
