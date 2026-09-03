"""Phase 6.1: tests for scripts/seed_retrieval_load_corpus.py.

PG-gated -- these exercise the real PostgresVectorStore/HNSW schema, mirroring
tests/test_postgres_store.py's gating pattern. Each test uses its own
--tenant-prefix so cases sharing the real Postgres DB in CI/local runs don't
collide with each other's seeded rows.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from backend.postgres_store import PostgresVectorStore  # noqa: E402
from seed_retrieval_load_corpus import main, tenant_ids  # noqa: E402

POSTGRES_DSN = os.environ.get("POSTGRES_DSN")
needs_postgres = pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="POSTGRES_DSN not set; set it to a reachable Postgres + pgvector to run these cases",
)


def _document_count(dsn: str, tenant_id: str) -> int:
    store = PostgresVectorStore(dsn=dsn, tenant_id=tenant_id)
    return len(store.get_all_documents())


@needs_postgres
def test_given_count_and_tenants_when_seeded_then_docs_split_round_robin_across_tenants():
    prefix = "loadtest-test-splitrr"
    exit_code = main(["--count", "20", "--tenants", "4", "--postgres-dsn", POSTGRES_DSN, "--tenant-prefix", prefix])
    assert exit_code == 0
    for tenant_id in tenant_ids(4, prefix=prefix):
        assert _document_count(POSTGRES_DSN, tenant_id) == 5


@needs_postgres
def test_given_reseed_when_run_twice_then_idempotent_not_accumulating():
    prefix = "loadtest-test-reseed"
    args = ["--count", "10", "--tenants", "2", "--postgres-dsn", POSTGRES_DSN, "--tenant-prefix", prefix]
    main(args)
    main(args)
    for tenant_id in tenant_ids(2, prefix=prefix):
        assert _document_count(POSTGRES_DSN, tenant_id) == 5


@needs_postgres
def test_given_seeded_docs_when_embeddings_compared_then_not_all_identical():
    prefix = "loadtest-test-varied"
    main(["--count", "6", "--tenants", "1", "--postgres-dsn", POSTGRES_DSN, "--tenant-prefix", prefix])
    store = PostgresVectorStore(dsn=POSTGRES_DSN, tenant_id=tenant_ids(1, prefix=prefix)[0])
    docs = store.get_all_documents()
    contents = {store.get_document_content(d["filename"]) for d in docs}
    assert len(contents) == len(docs), "expected non-identical seeded document content"
