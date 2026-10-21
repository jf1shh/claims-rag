"""Purges a whole claim: its indexed documents and ingestion job rows
(M12c-E, enterprise audit finding #6). Without --yes, prints what would be
deleted and exits without deleting. Also runs the job-retention sweep
on-demand with --sweep-jobs.
"""
import argparse
import getpass
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    from app_factory import build_dependencies
    from backend.claim_purge import ClaimPurgeConflict, purge_claim, validate_claim_id
    from config import get_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claim-id", help="Claim to purge.")
    parser.add_argument("--yes", action="store_true", help="Actually delete; without it this is a dry run.")
    parser.add_argument("--sweep-jobs", action="store_true", help="Run one job-retention sweep and exit.")
    args = parser.parse_args(argv)

    settings = get_settings()
    deps = build_dependencies(settings)
    try:
        if args.sweep_jobs:
            cutoff = datetime.now(timezone.utc) - timedelta(days=settings.job_retention_days)
            count = deps.job_store.sweep_terminal(settings.tenant_id, cutoff)
            print(f"Swept {count} expired job row(s).")
            return 0

        if not args.claim_id:
            parser.error("--claim-id is required unless --sweep-jobs is given")

        try:
            validate_claim_id(args.claim_id)
        except ValueError as exc:
            print(f"Invalid claim id: {exc}", file=sys.stderr)
            return 1

        if not args.yes:
            document_count = len(deps.vector_store.get_claim_documents(args.claim_id))
            job_count = deps.job_store.count_for_claim(settings.tenant_id, args.claim_id)
            print(
                f"Would delete {document_count} document(s) and {job_count} job row(s) "
                f"for claim {args.claim_id!r}. Re-run with --yes to delete."
            )
            return 0

        try:
            result = purge_claim(
                vector_store=deps.vector_store,
                job_store=deps.job_store,
                tenant_id=settings.tenant_id,
                claim_id=args.claim_id,
            )
        except ValueError as exc:
            print(f"Invalid claim id: {exc}", file=sys.stderr)
            return 1
        except ClaimPurgeConflict as exc:
            print(f"Conflict: {exc}", file=sys.stderr)
            return 2

        deps.audit_sink.record(
            {
                "event": "claim_purge",
                "request_id": f"cli_{uuid.uuid4().hex}",
                "tenant_id": settings.tenant_id,
                "subject": f"cli:{getpass.getuser()}",
                "claim_id": args.claim_id,
                "outcome": "purged",
                "document_count": result.documents_deleted,
                "job_count": result.jobs_deleted,
            }
        )
        print(
            f"Deleted {result.documents_deleted} document(s) and {result.jobs_deleted} "
            f"job row(s) for claim {args.claim_id!r}."
        )
        return 0
    finally:
        deps.queue.close()


if __name__ == "__main__":
    sys.exit(main())
