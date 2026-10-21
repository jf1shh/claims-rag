"""Claim purge (M12c-E, enterprise audit finding #6): deletes a whole claim --
its indexed documents and ingestion job rows -- used by both the admin API
(DELETE /api/claims/{claim_id}) and the CLI (scripts/purge_claim.py)."""
from __future__ import annotations

import re
from dataclasses import dataclass

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class ClaimPurgeConflict(Exception):
    """Raised when the claim has ingestion jobs still in progress -- no
    document or job row is deleted when this is raised."""


@dataclass(frozen=True)
class PurgeResult:
    documents_deleted: int
    jobs_deleted: int


def validate_claim_id(claim_id: str) -> None:
    if not isinstance(claim_id, str) or not claim_id:
        raise ValueError("claim_id must be a non-empty string")
    if len(claim_id) > 128:
        raise ValueError("claim_id must be at most 128 characters")
    if _CONTROL_CHARS.search(claim_id):
        raise ValueError("claim_id must not contain control characters")
    if "/" in claim_id or "\\" in claim_id:
        raise ValueError("claim_id must not contain path separators")


def purge_claim(*, vector_store, job_store, tenant_id: str, claim_id: str) -> PurgeResult:
    """Deletes every document attached to ``claim_id`` and then its job rows.

    Refuses (``ClaimPurgeConflict``, nothing deleted) while any ingestion job
    for the claim is still in flight. Idempotent: a claim with no documents
    and no jobs returns zeros rather than erroring.
    """
    validate_claim_id(claim_id)
    if job_store.active_jobs_for_claim(tenant_id, claim_id) > 0:
        raise ClaimPurgeConflict(f"claim {claim_id!r} has ingestion jobs in progress")

    documents = vector_store.get_claim_documents(claim_id)
    documents_deleted = sum(
        1 for document in documents if vector_store.delete_document(document["filename"], claim_id)
    )
    jobs_deleted = job_store.delete_for_claim(tenant_id, claim_id)
    return PurgeResult(documents_deleted=documents_deleted, jobs_deleted=jobs_deleted)
