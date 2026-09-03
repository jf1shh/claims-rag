"""Phase 6.1: bulk-seeds a multi-tenant retrieval-load-test corpus directly
into Postgres + pgvector, bypassing HTTP/queue/real ML so 100k+ documents
seed in seconds/minutes rather than the hours a real CPU embedding model
would take. Ingest throughput itself is already load-tested by
scripts/load_test_ingestion.py (Phase 3.3, extended with --backend postgres
for Phase 6.1) -- this script's only job is to reach the "N docs already
indexed" starting state for the retrieval-latency load test.

PostgresVectorStore is RLS-scoped to one tenant per instance, so --tenants N
constructs N separate store instances up front and round-robins documents
across them, rather than a single shared store handling every tenant.

Idempotent when re-run with the same --count/--tenants: document filenames
are deterministic per index (doc_00000.txt, ...), and add_document overwrites
in place for a matching filename within the same scope -- so a second run
with identical args reindexes the same rows rather than accumulating
duplicates. Re-running with a *smaller* --count than a prior run leaves the
extra higher-numbered documents from that prior run in place; use a fresh
database (or matching counts) for a clean measurement.

Run:  python scripts/seed_retrieval_load_corpus.py --count 100000 --tenants 8 --postgres-dsn postgresql://...
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.postgres_store import PostgresVectorStore  # noqa: E402


class _FakeEmbedder:
    """Deterministic, torch-free 384-dim embedder (mirrors
    scripts/load_test_ingestion.py's _FakeEmbedder -- duplicated rather than
    imported, matching this repo's existing precedent of per-script fake
    embedders instead of a shared test-only dependency)."""

    @staticmethod
    def _vec(text: str):
        # usedforsecurity=False: fast deterministic fingerprint, not a security use.
        digest = hashlib.md5((text or "").encode(), usedforsecurity=False).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(384)]

    def embed_chunks(self, chunks):
        return [self._vec(c) for c in chunks]

    def embed_query(self, query):
        return self._vec(query)


def tenant_ids(count: int, prefix: str = "loadtest-tenant") -> list[str]:
    return [f"{prefix}-{i}" for i in range(count)]


def _synthetic_text(index: int) -> str:
    """Stable, index-unique text (mirrors load_test_ingestion.py's generator)
    so retrieval isn't trivially degenerate across the seeded corpus."""
    return (
        f"Auto claims reference document number {index}. The regional labor "
        f"rate schedule applies and the mechanical labor cap for this region "
        f"is $110 per hour as of the current year. Sheet metal and refinishing "
        f"are capped separately at $62 per hour, and glass endorsement terms "
        f"waive the comprehensive deductible for safety glass replacement. "
        f"Claim adjusters must verify the applicable state statute before "
        f"approving any estimate line item. Reference index {index}."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100_000, help="total documents to seed")
    parser.add_argument("--tenants", type=int, default=8, help="number of synthetic tenants")
    parser.add_argument("--postgres-dsn", required=True, help="Postgres + pgvector DSN")
    parser.add_argument(
        "--tenant-prefix",
        default="loadtest-tenant",
        help="synthetic tenant id prefix (isolates concurrent/repeated runs against the same database)",
    )
    parser.add_argument("--batch-log-every", type=int, default=10_000, help="progress log interval")
    args = parser.parse_args(argv)
    if args.count <= 0:
        parser.error("--count must be positive")
    if args.tenants <= 0:
        parser.error("--tenants must be positive")

    embedder = _FakeEmbedder()
    ids = tenant_ids(args.tenants, prefix=args.tenant_prefix)
    stores = [PostgresVectorStore(dsn=args.postgres_dsn, tenant_id=t) for t in ids]

    start = time.perf_counter()
    for index in range(args.count):
        store = stores[index % args.tenants]
        text = _synthetic_text(index)
        store.add_document(
            filename=f"doc_{index:06d}.txt",
            file_type="txt",
            file_size=len(text),
            text=text,
            embedding_engine=embedder,
        )
        if args.batch_log_every and (index + 1) % args.batch_log_every == 0:
            elapsed = time.perf_counter() - start
            print(f"seeded {index + 1}/{args.count} ({(index + 1) / elapsed:.1f} docs/sec)", flush=True)

    total = time.perf_counter() - start
    docs_per_sec = args.count / total if total else 0.0
    print(
        f"SEED COMPLETE count={args.count} tenants={args.tenants} "
        f"total={total:.2f}s throughput={docs_per_sec:.1f} docs/sec"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
